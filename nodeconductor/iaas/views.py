from __future__ import unicode_literals

from collections import defaultdict
import datetime
import logging
import time


from django.db import models as django_models
from django.db.models import Q
from django.http import Http404
from django.shortcuts import get_object_or_404
import django_filters
from rest_framework import exceptions
from rest_framework import filters
from rest_framework import mixins
from rest_framework import permissions, status
from rest_framework import viewsets, views
from rest_framework.response import Response
from rest_framework_extensions.decorators import action, link

from nodeconductor.core import mixins as core_mixins
from nodeconductor.core import models as core_models
from nodeconductor.core import exceptions as core_exceptions
from nodeconductor.core import viewsets as core_viewsets
from nodeconductor.core.filters import DjangoMappingFilterBackend
from nodeconductor.core.utils import sort_dict
from nodeconductor.iaas import models
from nodeconductor.iaas import serializers
from nodeconductor.iaas import tasks
from nodeconductor.iaas.serializers import ServiceSerializer
from nodeconductor.structure import filters as structure_filters
from nodeconductor.structure.models import ProjectRole, Project, Customer, ProjectGroup, CustomerRole


logger = logging.getLogger(__name__)


class InstanceFilter(django_filters.FilterSet):
    project_group_name = django_filters.CharFilter(
        name='cloud_project_membership__project__project_groups__name',
        distinct=True,
        lookup_type='icontains',
    )
    project_name = django_filters.CharFilter(
        name='cloud_project_membership__project__name',
        distinct=True,
        lookup_type='icontains',
    )

    project_group = django_filters.CharFilter(
        name='cloud_project_membership__project__project_groups__uuid',
        distinct=True,
    )

    project = django_filters.CharFilter(
        name='cloud_project_membership__project__uuid',
        distinct=True,
        lookup_type='icontains',
    )

    customer_name = django_filters.CharFilter(
        name='cloud_project_membership__project__customer__name',
        distinct=True,
        lookup_type='icontains',
    )

    customer_native_name = django_filters.CharFilter(
        name='cloud_project_membership__project__customer__native_name',
        distinct=True,
        lookup_type='icontains',
    )

    customer_abbreviation = django_filters.CharFilter(
        name='cloud_project_membership__project__customer__abbreviation',
        distinct=True,
        lookup_type='icontains',
    )

    template_name = django_filters.CharFilter(
        name='template__name',
        lookup_type='icontains',
    )

    hostname = django_filters.CharFilter(lookup_type='icontains')
    state = django_filters.CharFilter()

    class Meta(object):
        model = models.Instance
        fields = [
            'hostname',
            'customer_name',
            'customer_native_name',
            'customer_abbreviation',
            'state',
            'project_name',
            'project_group_name',
            'project',
            'project_group',
            'template_name',
            'start_time',
        ]
        order_by = [
            'hostname',
            '-hostname',
            'state',
            '-state',
            'start_time',
            '-start_time',
            'cloud_project_membership__project__customer__name',
            '-cloud_project_membership__project__customer__name',
            'cloud_project_membership__project__customer__native_name',
            '-cloud_project_membership__project__customer__native_name',
            'cloud_project_membership__project__customer__abbreviation',
            '-cloud_project_membership__project__customer__abbreviation',
            'cloud_project_membership__project__name',
            '-cloud_project_membership__project__name',
            'cloud_project_membership__project__project_groups__name',
            '-cloud_project_membership__project__project_groups__name',
            'template__name',
            '-template__name',
        ]
        order_by_mapping = {
            # Proper field naming
            'customer_name': 'cloud_project_membership__project__customer__name',
            'customer_native_name': 'cloud_project_membership__project__customer__native_name',
            'customer_abbreviation': 'cloud_project_membership__project__customer__abbreviation',
            'project_name': 'cloud_project_membership__project__name',
            'project_group_name': 'cloud_project_membership__project__project_groups__name',
            'template_name': 'template__name',

            # Backwards compatibility
            'project__customer__name': 'cloud_project_membership__project__customer__name',
            'project__name': 'cloud_project_membership__project__name',
            'project__project_groups__name': 'cloud_project_membership__project__project_groups__name',
        }


class InstanceViewSet(mixins.CreateModelMixin,
                      mixins.RetrieveModelMixin,
                      core_mixins.ListModelMixin,
                      core_mixins.UpdateOnlyModelMixin,
                      viewsets.GenericViewSet):
    """List of VM instances that are accessible by this user.
    http://nodeconductor.readthedocs.org/en/latest/api/api.html#vm-instance-management
    """

    queryset = models.Instance.objects.all()
    serializer_class = serializers.InstanceSerializer
    lookup_field = 'uuid'
    filter_backends = (structure_filters.GenericRoleFilter, DjangoMappingFilterBackend)
    permission_classes = (permissions.IsAuthenticated, permissions.DjangoObjectPermissions)
    filter_class = InstanceFilter

    def get_serializer_class(self):
        if self.request.method == 'POST':
            return serializers.InstanceCreateSerializer
        elif self.request.method in ('PUT', 'PATCH'):
            return serializers.InstanceUpdateSerializer

        return super(InstanceViewSet, self).get_serializer_class()

    def get_serializer_context(self):
        """
        Extra context provided to the serializer class.
        """
        context = super(InstanceViewSet, self).get_serializer_context()
        context['user'] = self.request.user
        return context

    def pre_save(self, obj):
        super(InstanceViewSet, self).pre_save(obj)

        if obj.pk is None:
            # Create flow
            obj.agreed_sla = obj.template.sla_level
        else:
            # Update flow
            related_data = getattr(self.object, '_related_data', {})

            self.new_security_group_ids = set(
                isg.security_group_id
                for isg in related_data.get('security_groups', [])
            )

            # Prevent DRF from trashing m2m security_group relation
            try:
                del related_data['security_groups']
            except KeyError:
                pass

        # check if connected cloud_project_membership is in a sane state - fail modification operation otherwise
        if obj.cloud_project_membership.state == core_models.SynchronizationStates.ERRED:
            raise core_exceptions.IncorrectStateException(
                detail='Cannot modify an instance if it is connected to a cloud project membership in erred state.'
            )

    def post_save(self, obj, created=False):
        super(InstanceViewSet, self).post_save(obj, created)
        if created:
            tasks.schedule_provisioning.delay(obj.uuid.hex, backend_flavor_id=obj.flavor.backend_id)
            return

        # We care only about update flow
        old_security_groups = dict(
            (isg.security_group_id, isg)
            for isg in self.object.security_groups.all()
        )

        # Remove stale security groups
        for security_group_id, isg in old_security_groups.items():
            if security_group_id not in self.new_security_group_ids:
                isg.delete()

        # Add missing ones
        for security_group_id in self.new_security_group_ids - set(old_security_groups.keys()):
            models.InstanceSecurityGroup.objects.create(
                instance=self.object,
                security_group_id=security_group_id,
            )

        from nodeconductor.iaas.tasks import push_instance_security_groups
        push_instance_security_groups.delay(self.object.uuid.hex)

    def change_flavor(self, instance, flavor):
        instance_cloud = instance.cloud_project_membership.cloud

        if flavor.cloud != instance_cloud:
            return Response({'flavor': "New flavor is not within the same cloud"},
                            status=status.HTTP_400_BAD_REQUEST)

        # System volume size does not get updated since some backends
        # do not support resizing of a root volume
        # instance.system_volume_size = flavor.disk
        instance.ram = flavor.ram
        instance.cores = flavor.cores
        instance.save()
        # This is suboptimal, since it reads and writes instance twice
        return self._schedule_transition(self.request, instance.uuid.hex, 'flavor change',
                                         flavor_uuid=flavor.uuid.hex)

    def resize_disk(self, instance, new_size):
        if new_size <= instance.data_volume_size:
            return Response({'disk_size': "Disk size must be strictly greater than the current one"},
                            status=status.HTTP_400_BAD_REQUEST)

        instance.data_volume_size = new_size
        instance.save()
        # This is suboptimal, since it reads and writes instance twice
        return self._schedule_transition(self.request, instance.uuid.hex, 'disk extension')

    def _schedule_transition(self, request, uuid, operation, **kwargs):
        instance = self.get_object()
        membership = instance.cloud_project_membership

        is_admin = membership.project.has_user(request.user, ProjectRole.ADMINISTRATOR)

        if not is_admin and not request.user.is_staff:
            raise exceptions.PermissionDenied()

        # Importing here to avoid circular imports
        from nodeconductor.core.tasks import set_state, StateChangeError
        from nodeconductor.iaas import tasks

        supported_operations = {
            # code: (scheduled_celery_task, instance_marker_state)
            'start': ('schedule_starting', tasks.schedule_starting),
            'stop': ('schedule_stopping', tasks.schedule_stopping),
            'destroy': ('schedule_deletion', tasks.schedule_deleting),
            'flavor change': ('schedule_resizing', tasks.update_flavor),
            'disk extension': ('schedule_resizing', tasks.extend_disk),
        }

        # logger.info('Scheduling %s of an instance with uuid %s', operation, uuid)
        change_instance_state, processing_task = supported_operations[operation]

        try:
            set_state(models.Instance, uuid, change_instance_state)
            processing_task.delay(uuid, **kwargs)
        except StateChangeError:
            return Response({'status': 'Performing %s operation from instance state \'%s\' is not allowed'
                                       % (operation, instance.get_state_display())},
                            status=status.HTTP_409_CONFLICT)

        return Response({'status': '%s was scheduled' % operation},
                        status=status.HTTP_202_ACCEPTED)

    @action()
    def stop(self, request, uuid=None):
        return self._schedule_transition(request, uuid, 'stop')

    @action()
    def start(self, request, uuid=None):
        return self._schedule_transition(request, uuid, 'start')

    def destroy(self, request, uuid=None):
        return self._schedule_transition(request, uuid, 'destroy')

    @action()
    def resize(self, request, uuid=None):
        instance = self.get_object()

        if instance.state != models.Instance.States.OFFLINE:
            return Response({'detail': 'Instance must be offline'},
                            status=status.HTTP_409_CONFLICT)

        serializer = serializers.InstanceResizeSerializer(data=request.DATA)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        obj = serializer.object

        changed_flavor = obj['flavor']

        # Serializer makes sure that exactly one of the branches
        # will match
        if changed_flavor is not None:
            return self.change_flavor(instance, changed_flavor)
        else:
            return self.resize_disk(instance, obj['disk_size'])

    @link()
    def usage(self, request, uuid):
        instance = self.get_object()

        hour = 60 * 60
        data = {
            'start_timestamp': request.QUERY_PARAMS.get('from', int(time.time() - hour)),
            'end_timestamp': request.QUERY_PARAMS.get('to', int(time.time())),
            'segments_count': request.QUERY_PARAMS.get('datapoints', 6),
            'item': request.QUERY_PARAMS.get('item'),
        }

        serializer = serializers.UsageStatsSerializer(data=data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        stats = serializer.get_stats([instance])
        return Response(stats, status=status.HTTP_200_OK)


class TemplateViewSet(core_viewsets.ModelViewSet):
    """
    List of VM templates that are accessible by this user.

    http://nodeconductor.readthedocs.org/en/latest/api/api.html#templates
    """

    queryset = models.Template.objects.all()
    serializer_class = serializers.TemplateSerializer
    permission_classes = (permissions.IsAuthenticated, permissions.DjangoObjectPermissions)
    lookup_field = 'uuid'

    def get_serializer_class(self):
        if self.request.method in ('POST', 'PUT', 'PATCH'):
            return serializers.TemplateCreateSerializer

        return super(TemplateViewSet, self).get_serializer_class()

    def get_queryset(self):
        queryset = super(TemplateViewSet, self).get_queryset()

        user = self.request.user

        if not user.is_staff:
            queryset = queryset.exclude(is_active=False)

        if self.request.method == 'GET':
            cloud_uuid = self.request.QUERY_PARAMS.get('cloud')
            if cloud_uuid is not None:
                cloud_queryset = structure_filters.filter_queryset_for_user(
                    models.Cloud.objects.all(), user)

                try:
                    cloud = cloud_queryset.get(uuid=cloud_uuid)
                except models.Cloud.DoesNotExist:
                    return queryset.none()

                queryset = queryset.filter(images__cloud=cloud)

        return queryset


class SshKeyFilter(django_filters.FilterSet):
    uuid = django_filters.CharFilter()
    user_uuid = django_filters.CharFilter(
        name='user__uuid'
    )
    name = django_filters.CharFilter(lookup_type='icontains')

    class Meta(object):
        model = core_models.SshPublicKey
        fields = [
            'name',
            'fingerprint',
            'uuid',
            'user_uuid'
        ]
        order_by = [
            'name',
            '-name',
        ]


class SshKeyViewSet(core_viewsets.ModelViewSet):
    """
    List of SSH public keys that are accessible by this user.

    http://nodeconductor.readthedocs.org/en/latest/api/api.html#key-management
    """

    queryset = core_models.SshPublicKey.objects.all()
    serializer_class = serializers.SshKeySerializer
    lookup_field = 'uuid'
    filter_backends = (filters.DjangoFilterBackend,)
    filter_class = SshKeyFilter

    def pre_save(self, key):
        key.user = self.request.user

    def get_queryset(self):
        queryset = super(SshKeyViewSet, self).get_queryset()
        user = self.request.user

        if user.is_staff:
            return queryset

        return queryset.filter(user=user)


class TemplateLicenseViewSet(core_viewsets.ModelViewSet):
    """List of template licenses that are accessible by this user.

    http://nodeconductor.readthedocs.org/en/latest/api/api.html#template-licenses
    """
    queryset = models.TemplateLicense.objects.all()
    serializer_class = serializers.TemplateLicenseSerializer
    permission_classes = (permissions.IsAuthenticated, permissions.DjangoObjectPermissions)
    lookup_field = 'uuid'

    def get_queryset(self):
        if not self.request.user.is_staff:
            raise Http404()
        queryset = super(TemplateLicenseViewSet, self).get_queryset()
        if 'customer' in self.request.QUERY_PARAMS:
            customer_uuid = self.request.QUERY_PARAMS['customer']
            queryset = queryset.filter(templates__images__cloud__customer__uuid=customer_uuid)
        return queryset

    def _filter_queryset(self, queryset):
        if 'customer' in self.request.QUERY_PARAMS:
            customer_uuid = self.request.QUERY_PARAMS['customer']
            queryset = queryset.filter(instance__cloud_project_membership__project__customer__uuid=customer_uuid)
        if 'name' in self.request.QUERY_PARAMS:
            queryset = queryset.filter(template_license__name=self.request.QUERY_PARAMS['name'])
        if 'type' in self.request.QUERY_PARAMS:
            queryset = queryset.filter(template_license__license_type=self.request.QUERY_PARAMS['type'])
        return queryset

    @link(is_for_list=True)
    def stats(self, request):
        queryset = structure_filters.filter_queryset_for_user(models.InstanceLicense.objects.all(), request.user)
        queryset = self._filter_queryset(queryset)

        aggregate_parameters = self.request.QUERY_PARAMS.getlist('aggregate', [])
        aggregate_parameter_to_field_map = {
            'project': [
                'instance__cloud_project_membership__project__uuid',
                'instance__cloud_project_membership__project__name',
            ],
            'project_group': [
                'instance__cloud_project_membership__project__project_groups__uuid',
                'instance__cloud_project_membership__project__project_groups__name',
            ],
            'type': ['template_license__license_type'],
            'name': ['template_license__name'],
        }

        aggregate_fields = []
        for aggregate_parameter in aggregate_parameters:
            if aggregate_parameter not in aggregate_parameter_to_field_map:
                return Response('Licenses statistics can not be aggregated by %s' % aggregate_parameter,
                                status=status.HTTP_400_BAD_REQUEST)
            aggregate_fields += aggregate_parameter_to_field_map[aggregate_parameter]

        queryset = queryset.values(*aggregate_fields).annotate(count=django_models.Count('id', distinct=True))
        # This hack can be removed when https://code.djangoproject.com/ticket/16735 will be closed
        # Replace databases paths by normal names. Ex: instance__project__uuid is replaced by project_uuid
        name_replace_map = {
            'instance__cloud_project_membership__project__uuid': 'project_uuid',
            'instance__cloud_project_membership__project__name': 'project_name',
            'instance__cloud_project_membership__project__project_groups__uuid': 'project_group_uuid',
            'instance__cloud_project_membership__project__project_groups__name': 'project_group_name',
            'template_license__license_type': 'type',
            'template_license__name': 'name',
        }
        for d in queryset:
            for db_name, output_name in name_replace_map.iteritems():
                if db_name in d:
                    d[output_name] = d[db_name]
                    del d[db_name]

        return Response(queryset)


class ServiceFilter(django_filters.FilterSet):
    project_group_name = django_filters.CharFilter(
        name='cloud_project_membership__project__project_groups__name',
        distinct=True,
        lookup_type='icontains',
    )
    project_name = django_filters.CharFilter(
        name='cloud_project_membership__project__name',
        distinct=True,
        lookup_type='icontains',
    )

    # FIXME: deprecated, use project_group_name instead
    project_groups = django_filters.CharFilter(
        name='cloud_project_membership__project__project_groups__name',
        distinct=True,
        lookup_type='icontains',
    )

    hostname = django_filters.CharFilter(lookup_type='icontains')
    customer_name = django_filters.CharFilter(
        name='cloud_project_membership__project__customer__name',
        lookup_type='icontains',
    )
    customer_abbreviation = django_filters.CharFilter(
        name='cloud_project_membership__project__customer__abbreviation',
        lookup_type='icontains',
    )

    customer_native_name = django_filters.CharFilter(
        name='cloud_project_membership__project__customer__native_name',
        lookup_type='icontains',
    )

    template_name = django_filters.CharFilter(
        name='template__name',
        lookup_type='icontains',
    )
    agreed_sla = django_filters.NumberFilter()
    actual_sla = django_filters.NumberFilter(
        name='slas__value',
        distinct=True,
    )

    class Meta(object):
        model = models.Instance
        fields = [
            'hostname',
            'template_name',
            'customer_name',
            'customer_native_name',
            'customer_abbreviation',
            'project_name',
            'project_groups',
            'agreed_sla',
            'actual_sla',
        ]
        order_by = [
            'hostname',
            'template__name',
            'cloud_project_membership__project__customer__name',
            'cloud_project_membership__project__customer__abbreviation',
            'cloud_project_membership__project__customer__native_name',
            'cloud_project_membership__project__name',
            'cloud_project_membership__project__project_groups__name',
            'agreed_sla',
            'slas__value',
            # desc
            '-hostname',
            '-template__name',
            '-cloud_project_membership__project__customer__name',
            '-cloud_project_membership__project__customer__abbreviation',
            '-cloud_project_membership__project__customer__native_name',
            '-cloud_project_membership__project__name',
            '-cloud_project_membership__project__project_groups__name',
            '-agreed_sla',
            '-slas__value',
        ]
        order_by_mapping = {
            # Proper field naming
            'customer_name': 'cloud_project_membership__project__customer__name',
            'customer_abbreviation': 'cloud_project_membership__project__customer__abbreviation',
            'customer_native_name': 'cloud_project_membership__project__customer__native_name',
            'project_name': 'cloud_project_membership__project__name',
            'project_group_name': 'cloud_project_membership__project__project_groups__name',
            'template_name': 'template__name',
            'actual_sla': 'slas__value',

            # Backwards compatibility
            'project__customer__name': 'cloud_project_membership__project__customer__name',
            'project__name': 'cloud_project_membership__project__name',
            'project__project_groups__name': 'cloud_project_membership__project__project_groups__name',
        }


# XXX: This view has to be rewritten or removed after haystack implementation
class ServiceViewSet(core_viewsets.ReadOnlyModelViewSet):
    queryset = models.Instance.objects.exclude(
        state=models.Instance.States.DELETING,
    )
    serializer_class = ServiceSerializer
    lookup_field = 'uuid'
    filter_backends = (structure_filters.GenericRoleFilter, DjangoMappingFilterBackend)
    filter_class = ServiceFilter

    def _get_period(self):
        period = self.request.QUERY_PARAMS.get('period')
        if period is None:
            today = datetime.date.today()
            period = '%s-%s' % (today.year, today.month)
        return period

    def get_queryset(self):
        queryset = super(ServiceViewSet, self).get_queryset()

        period = self._get_period()

        queryset = queryset.filter(slas__period=period, agreed_sla__isnull=False).\
            values(
                'uuid',
                'hostname',
                'template__name',
                'agreed_sla',
                'slas__value', 'slas__period',
                'cloud_project_membership__project__customer__name',
                'cloud_project_membership__project__customer__native_name',
                'cloud_project_membership__project__customer__abbreviation',
                'cloud_project_membership__project__name',
            )
        return queryset

    @link()
    def events(self, request, uuid):
        service = self.get_object()
        period = self._get_period()
        # TODO: this should use a generic service model
        history = get_object_or_404(models.InstanceSlaHistory, instance__uuid=service['uuid'], period=period)

        history_events = history.events.all().order_by('-timestamp').values('timestamp', 'state')

        serializer = serializers.SlaHistoryEventSerializer(data=history_events,
                                                           many=True)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        return Response(serializer.data, status=status.HTTP_200_OK)


class ResourceStatsView(views.APIView):

    def _check_user(self, request):
        if not request.user.is_staff:
            raise exceptions.PermissionDenied()

    def _get_quotas_stats(self, clouds):
        quotas_list = models.ResourceQuota.objects.filter(
            cloud_project_membership__cloud__in=clouds).values('vcpu', 'ram', 'storage', 'backup_storage')
        return {
            'vcpu_quota': sum([q['vcpu'] for q in quotas_list]),
            'memory_quota': sum([q['ram'] for q in quotas_list]),
            'storage_quota': sum([q['storage'] for q in quotas_list]),
            'backup_quota': sum([q['backup_storage'] for q in quotas_list]),
        }

    def get(self, request, format=None):
        self._check_user(request)
        if not 'auth_url' in request.QUERY_PARAMS:
            return Response('GET parameter "auth_url" have to be defined', status=status.HTTP_400_BAD_REQUEST)
        auth_url = request.QUERY_PARAMS['auth_url']

        try:
            clouds = models.Cloud.objects.filter(auth_url=auth_url)
            cloud_backend = clouds[0].get_backend()
        except IndexError:
            return Response('No clouds with auth url: %s' % auth_url, status=status.HTTP_400_BAD_REQUEST)

        stats = cloud_backend.get_resource_stats(auth_url)
        quotas_stats = self._get_quotas_stats(clouds)
        stats.update(quotas_stats)

        # TODO: get from OpenStack once we have Juno and properly working backup quotas
        full_usage = QuotaStatsView.get_sum_of_quotas(models.CloudProjectMembership.objects.filter(cloud__in=clouds))
        stats['backups'] = full_usage.get('backup_storage_usage', 0)

        return Response(sort_dict(stats), status=status.HTTP_200_OK)


class CustomerStatsView(views.APIView):

    def get(self, request, format=None):
        customer_statistics = []
        customer_queryset = structure_filters.filter_queryset_for_user(Customer.objects.all(), request.user)
        for customer in customer_queryset:
            projects_count = structure_filters.filter_queryset_for_user(
                Project.objects.filter(customer=customer), request.user).count()
            project_groups_count = structure_filters.filter_queryset_for_user(
                ProjectGroup.objects.filter(customer=customer), request.user).count()
            instances_count = structure_filters.filter_queryset_for_user(
                models.Instance.objects.filter(cloud_project_membership__project__customer=customer), request.user).count()
            customer_statistics.append({
                'name': customer.name, 'projects': projects_count,
                'project_groups': project_groups_count, 'instances': instances_count
            })

        return Response(customer_statistics, status=status.HTTP_200_OK)


class UsageStatsView(views.APIView):

    aggregate_models = {
        'customer': {'model': Customer, 'path': models.Instance.Permissions.customer_path},
        'project_group': {'model': ProjectGroup, 'path': models.Instance.Permissions.project_group_path},
        'project': {'model': Project, 'path': models.Instance.Permissions.project_path},
    }

    def _get_aggregate_queryset(self, request, aggregate_model_name):
        model = self.aggregate_models[aggregate_model_name]['model']
        return structure_filters.filter_queryset_for_user(model.objects.all(), request.user)

    def _get_aggregate_filter(self, aggregate_model_name, obj):
        path = self.aggregate_models[aggregate_model_name]['path']
        return {path: obj}

    def get(self, request, format=None):
        usage_stats = []

        aggregate_model_name = request.QUERY_PARAMS.get('aggregate', 'customer')
        if aggregate_model_name not in self.aggregate_models.keys():
            return Response(
                'Get parameter "aggregate" can take only this values: ' % ', '.join(self.aggregate_models.keys()),
                status=status.HTTP_400_BAD_REQUEST)

        aggregate_queryset = self._get_aggregate_queryset(request, aggregate_model_name)

        if 'uuid' in request.QUERY_PARAMS:
            aggregate_queryset = aggregate_queryset.filter(uuid=request.QUERY_PARAMS['uuid'])

        for aggregate_object in aggregate_queryset:
            instances = models.Instance.objects.filter(
                **self._get_aggregate_filter(aggregate_model_name, aggregate_object))
            if instances:
                hour = 60 * 60
                data = {
                    'start_timestamp': request.QUERY_PARAMS.get('from', int(time.time() - hour)),
                    'end_timestamp': request.QUERY_PARAMS.get('to', int(time.time())),
                    'segments_count': request.QUERY_PARAMS.get('datapoints', 6),
                    'item': request.QUERY_PARAMS.get('item'),
                }

                serializer = serializers.UsageStatsSerializer(data=data)
                if not serializer.is_valid():
                    return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

                stats = serializer.get_stats(instances)
                usage_stats.append({'name': aggregate_object.name, 'datapoints': stats})
            else:
                usage_stats.append({'name': aggregate_object.name, 'datapoints': []})
        return Response(usage_stats, status=status.HTTP_200_OK)


class FlavorViewSet(core_viewsets.ReadOnlyModelViewSet):
    """List of VM instance flavors that are accessible by this user.

    http://nodeconductor.readthedocs.org/en/latest/api/api.html#flavor-management
    """

    queryset = models.Flavor.objects.all()
    serializer_class = serializers.FlavorSerializer
    lookup_field = 'uuid'
    filter_backends = (structure_filters.GenericRoleFilter,)


class CloudFilter(django_filters.FilterSet):
    name = django_filters.CharFilter(lookup_type='icontains')
    customer = django_filters.CharFilter(
        name='customer__uuid',
    )
    customer_name = django_filters.CharFilter(
        lookup_type='icontains',
        name='customer__name',
    )
    customer_native_name = django_filters.CharFilter(
        lookup_type='icontains',
        name='customer__native_name',
    )
    project = django_filters.CharFilter(
        name='cloudprojectmembership__project__uuid',
        distinct=True,
    )
    project_name = django_filters.CharFilter(
        name='cloudprojectmembership__project__name',
        lookup_type='icontains',
        distinct=True,
    )

    class Meta(object):
        model = models.Cloud
        fields = [
            'name',
            'customer',
            'customer_name',
            'customer_native_name',
            'project',
            'project_name',
        ]


class CloudViewSet(core_viewsets.ModelViewSet):
    """List of clouds that are accessible by this user.

    http://nodeconductor.readthedocs.org/en/latest/api/api.html#cloud-model
    """

    queryset = models.Cloud.objects.all().prefetch_related('flavors')
    serializer_class = serializers.CloudSerializer
    lookup_field = 'uuid'
    permission_classes = (
        permissions.IsAuthenticated,
        permissions.DjangoObjectPermissions,
    )
    filter_backends = (structure_filters.GenericRoleFilter, filters.DjangoFilterBackend)
    filter_class = CloudFilter

    def pre_save(self, cloud):
        super(CloudViewSet, self).pre_save(cloud)

        if cloud.pk is not None:
            return

        if self.request.user.is_staff:
            return

        if cloud.customer.has_user(self.request.user, CustomerRole.OWNER):
            return

        raise exceptions.PermissionDenied()

    def post_save(self, obj, created=False):
        if created:
            tasks.sync_cloud_account.delay(obj.uuid.hex)


class CloudProjectMembershipViewSet(mixins.CreateModelMixin,
                                    mixins.RetrieveModelMixin,
                                    mixins.DestroyModelMixin,
                                    core_mixins.ListModelMixin,
                                    viewsets.GenericViewSet):
    """
    List of project-cloud connections

    http://nodeconductor.readthedocs.org/en/latest/api/api.html#link-cloud-to-a-project
    """
    queryset = models.CloudProjectMembership.objects.all()
    serializer_class = serializers.CloudProjectMembershipSerializer
    filter_backends = (structure_filters.GenericRoleFilter,)
    permission_classes = (permissions.IsAuthenticated, permissions.DjangoObjectPermissions)

    def post_save(self, obj, created=False):
        if created:
            tasks.sync_cloud_membership.delay(obj.pk)


class SecurityGroupFilter(django_filters.FilterSet):
    name = django_filters.CharFilter(
        name='name',
        lookup_type='icontains',
    )
    description = django_filters.CharFilter(
        name='description',
        lookup_type='icontains',
    )
    cloud = django_filters.CharFilter(
        name='cloud_project_membership__cloud__uuid',
    )
    project = django_filters.CharFilter(
        name='cloud_project_membership__project__uuid',
    )

    class Meta(object):
        model = models.SecurityGroup
        fields = [
            'name',
            'description',
            'cloud',
            'project'
        ]


class SecurityGroupViewSet(core_viewsets.ReadOnlyModelViewSet):
    """
    List of security groups

    http://nodeconductor.readthedocs.org/en/latest/api/api.html#security-group-management
    """
    queryset = models.SecurityGroup.objects.all()
    serializer_class = serializers.SecurityGroupSerializer
    lookup_field = 'uuid'
    permission_classes = (permissions.IsAuthenticated,
                          permissions.DjangoObjectPermissions)
    filter_class = SecurityGroupFilter
    filter_backends = (structure_filters.GenericRoleFilter, filters.DjangoFilterBackend,)


class IpMappingFilter(django_filters.FilterSet):
    project = django_filters.CharFilter(
        name='project__uuid',
    )

    class Meta(object):
        model = models.IpMapping
        fields = [
            'project',
            'private_ip',
            'public_ip',
        ]


class IpMappingViewSet(core_viewsets.ModelViewSet):
    """
    List of mappings between public IPs and private IPs

    http://nodeconductor.readthedocs.org/en/latest/api/api.html#ip-mappings
    """
    queryset = models.IpMapping.objects.all()
    serializer_class = serializers.IpMappingSerializer
    lookup_field = 'uuid'
    filter_backends = (structure_filters.GenericRoleFilter, filters.DjangoFilterBackend,)
    permission_classes = (permissions.IsAuthenticated,
                          permissions.DjangoObjectPermissions)
    filter_class = IpMappingFilter


class FloatingIPFilter(django_filters.FilterSet):
    project = django_filters.CharFilter(
        name='cloud_project_membership__project__uuid',
    )
    cloud = django_filters.CharFilter(
        name='cloud_project_membership__cloud__uuid',
    )

    class Meta(object):
        model = models.FloatingIP
        fields = [
            'project',
            'cloud',
            'status',
        ]


class FloatingIPViewSet(core_viewsets.ReadOnlyModelViewSet):
    """
    List of floating ips
    """
    queryset = models.FloatingIP.objects.all()
    serializer_class = serializers.FloatingIPSerializer
    lookup_field = 'uuid'
    permission_classes = (permissions.IsAuthenticated, permissions.DjangoObjectPermissions)
    filter_backends = (structure_filters.GenericRoleFilter, filters.DjangoFilterBackend)
    filter_class = FloatingIPFilter


class QuotaStatsView(views.APIView):

    # This method should be moved from view (to utils.py maybe), when stats will be moved to separate application
    @staticmethod
    def get_sum_of_quotas(memberships):
        fields = ['vcpu', 'ram', 'storage', 'max_instances', 'backup_storage']
        sum_of_quotas = defaultdict(lambda: 0)

        for membership in memberships:
            # quota fields:
            try:
                for field in fields:
                    sum_of_quotas[field] += getattr(membership.resource_quota, field)
            except models.ResourceQuota.DoesNotExist:
                # we ignore memberships without quotas
                pass
            # quota usage fields:
            try:
                for field in fields:
                    sum_of_quotas[field + '_usage'] += getattr(membership.resource_quota_usage, field)
            except models.ResourceQuotaUsage.DoesNotExist:
                # we ignore memberships without quotas
                pass
        return sum_of_quotas

    def get(self, request, format=None):
        serializer = serializers.StatsAggregateSerializer(data={
            'model_name': request.QUERY_PARAMS.get('aggregate', 'customer'),
            'uuid': request.QUERY_PARAMS.get('uuid'),
        })
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        memberships = serializer.get_memberships(request.user)
        sum_of_quotas = self.get_sum_of_quotas(memberships)
        return Response(sum_of_quotas, status=status.HTTP_200_OK)
