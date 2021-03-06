from __future__ import unicode_literals

import time

from django.contrib import auth
from django.db.models.query_utils import Q
from django.http.response import Http404
import django_filters
from rest_framework import filters as rf_filter
from rest_framework import mixins as rf_mixins
from rest_framework import permissions as rf_permissions
from rest_framework import status
from rest_framework import views
from rest_framework import viewsets as rf_viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.settings import api_settings

from nodeconductor.core import filters as core_filters
from nodeconductor.core import mixins
from nodeconductor.core import permissions
from nodeconductor.core import viewsets
from nodeconductor.structure import filters
from nodeconductor.structure import models
from nodeconductor.structure import serializers
from nodeconductor.structure.models import ProjectRole, CustomerRole, ProjectGroupRole


User = auth.get_user_model()


class CustomerFilter(django_filters.FilterSet):
    name = django_filters.CharFilter(
        lookup_type='icontains',
    )
    native_name = django_filters.CharFilter(
        lookup_type='icontains',
    )
    abbreviation = django_filters.CharFilter(
        lookup_type='icontains',
    )
    contact_details = django_filters.CharFilter(
        lookup_type='icontains',
    )

    class Meta(object):
        model = models.Customer
        fields = [
            'name',
            'abbreviation',
            'contact_details',
            'native_name',
        ]
        order_by = [
            'name',
            'abbreviation',
            'contact_details',
            'native_name',
            # desc
            '-name',
            '-abbreviation',
            '-contact_details',
            '-native_name',
        ]


class CustomerViewSet(viewsets.ModelViewSet):
    """List of customers that are accessible by this user.

    http://nodeconductor.readthedocs.org/en/latest/api/api.html#customer-management
    """

    queryset = models.Customer.objects.all()
    serializer_class = serializers.CustomerSerializer
    lookup_field = 'uuid'
    permission_classes = (rf_permissions.IsAuthenticated,
                          rf_permissions.DjangoObjectPermissions)
    filter_backends = (filters.GenericRoleFilter, rf_filter.DjangoFilterBackend,)
    filter_class = CustomerFilter


class ProjectFilter(django_filters.FilterSet):
    customer = django_filters.CharFilter(
        name='customer__uuid',
        distinct=True,
    )

    customer_name = django_filters.CharFilter(
        name='customer__name',
        distinct=True,
        lookup_type='icontains'
    )

    customer_native_name = django_filters.CharFilter(
        name='customer__native_name',
        distinct=True,
        lookup_type='icontains'
    )

    customer_abbreviation = django_filters.CharFilter(
        name='customer__abbreviation',
        distinct=True,
        lookup_type='icontains'
    )

    project_group = django_filters.CharFilter(
        name='project_groups__uuid',
        distinct=True,
    )

    project_group_name = django_filters.CharFilter(
        name='project_groups__name',
        distinct=True,
        lookup_type='icontains'
    )

    name = django_filters.CharFilter(lookup_type='icontains')

    description = django_filters.CharFilter(lookup_type='icontains')

    # TODO: quota filters are matching quota values in every CloudProjectMembership quota, not aggregated.
    vcpu = django_filters.NumberFilter(
        name='cloudprojectmembership__resource_quota__vcpu',
    )

    ram = django_filters.NumberFilter(
        name='cloudprojectmembership__resource_quota__ram',
    )

    storage = django_filters.NumberFilter(
        name='cloudprojectmembership__resource_quota__storage',
    )

    max_instances = django_filters.NumberFilter(
        name='cloudprojectmembership__resource_quota__max_instances',
    )

    backup = django_filters.NumberFilter(
        name='cloudprojectmembership__resource_quota__backup_storage',
    )

    class Meta(object):
        model = models.Project
        fields = [
            'project_group',
            'project_group_name',
            'name',
            'customer', 'customer_name', 'customer_native_name', 'customer_abbreviation',
            'description',
            # quotas
            'vcpu',
            'ram',
            'storage',
            'max_instances',
            'backup',
        ]
        order_by = [
            'name',
            '-name',
            'project_groups__name',
            '-project_groups__name',
            'customer__native_name',
            '-customer__native_name',
            'customer__name',
            '-customer__name',
            'customer__abbreviation',
            '-customer__abbreviation',
            'cloudprojectmembership__resource_quota__vcpu',
            '-cloudprojectmembership__resource_quota__vcpu',
            'cloudprojectmembership__resource_quota__ram',
            '-cloudprojectmembership__resource_quota__ram',
            'cloudprojectmembership__resource_quota__storage',
            '-cloudprojectmembership__resource_quota__storage',
            'cloudprojectmembership__resource_quota__max_instances',
            '-cloudprojectmembership__resource_quota__max_instances',
            'cloudprojectmembership__resource_quota__backup_storage',
            '-cloudprojectmembership__resource_quota__backup_storage',
        ]

        order_by_mapping = {
            # Proper field naming
            'project_group_name': 'project_groups__name',
            'vcpu': 'cloudprojectmembership__resource_quota__vcpu',
            'ram': 'cloudprojectmembership__resource_quota__ram',
            'max_instances': 'cloudprojectmembership__resource_quota__max_instances',
            'storage': 'cloudprojectmembership__resource_quota__storage',
            'backup': 'cloudprojectmembership__resource_quota__backup_storage',
            'customer_name': 'customer__name',
            'customer_abbreviation': 'customer__abbreviation',
            'customer_native_name': 'customer__native_name',

            # Backwards compatibility
            'project_groups__name': 'project_groups__name',
            'resource_quota__vcpu': 'cloudprojectmembership__resource_quota__vcpu',
            'resource_quota__ram': 'cloudprojectmembership__resource_quota__ram',
            'resource_quota__storage': 'cloudprojectmembership__resource_quota__storage',
            'resource_quota__backup_storage': 'cloudprojectmembership__resource_quota__backup_storage',
        }


class ProjectViewSet(viewsets.ModelViewSet):
    """List of projects that are accessible by this user.

    http://nodeconductor.readthedocs.org/en/latest/api/api.html#project-management
    """

    queryset = models.Project.objects.all()
    serializer_class = serializers.ProjectSerializer
    lookup_field = 'uuid'
    filter_backends = (filters.GenericRoleFilter, core_filters.DjangoMappingFilterBackend)
    permission_classes = (rf_permissions.IsAuthenticated,
                          rf_permissions.DjangoObjectPermissions)
    filter_class = ProjectFilter

    def get_queryset(self):
        user = self.request.user
        queryset = super(ProjectViewSet, self).get_queryset()

        can_manage = self.request.QUERY_PARAMS.get('can_manage', None)
        if can_manage is not None:
            #XXX: Let the DB cry...
            queryset = queryset.filter(
                Q(customer__roles__permission_group__user=user,
                  customer__roles__role_type=models.CustomerRole.OWNER)
                |
                Q(roles__permission_group__user=user,
                  roles__role_type=models.ProjectRole.MANAGER)
            ).distinct()

        can_admin = self.request.QUERY_PARAMS.get('can_admin', None)

        if can_admin is not None:
            queryset = queryset.filter(
                roles__permission_group__user=user,
                roles__role_type=models.ProjectRole.ADMINISTRATOR,
            )

        return queryset

    def get_serializer_class(self):
        if self.request.method in ('POST', 'PUT', 'PATCH'):
            return serializers.ProjectCreateSerializer

        return super(ProjectViewSet, self).get_serializer_class()


class ProjectGroupFilter(django_filters.FilterSet):
    customer = django_filters.CharFilter(
        name='customer__uuid',
        distinct=True,
    )
    customer_name = django_filters.CharFilter(
        name='customer__name',
        distinct=True,
        lookup_type='icontains',
    )
    customer_native_name = django_filters.CharFilter(
        name='customer__native_name',
        distinct=True,
        lookup_type='icontains',
    )

    customer_abbreviation = django_filters.CharFilter(
        name='customer__abbreviation',
        distinct=True,
        lookup_type='icontains',
    )

    name = django_filters.CharFilter(lookup_type='icontains')

    class Meta(object):
        model = models.ProjectGroup
        fields = [
            'name',
            'customer',
            'customer_name',
            'customer_native_name',
            'customer_abbreviation',
        ]
        order_by = [
            'name',
            '-name',
            'customer__name',
            '-customer__name',
            'customer__native_name',
            '-customer__native_name',
            'customer__abbreviation',
            '-customer__abbreviation',
        ]
        order_by_mapping = {
            'customer_name': 'customer__name',
            'customer_abbreviation': 'customer__abbreviation',
            'customer_native_name': 'customer__native_name',
        }


class ProjectGroupViewSet(viewsets.ModelViewSet):
    """
    List of project groups that are accessible to this user.
    """

    queryset = models.ProjectGroup.objects.all()
    serializer_class = serializers.ProjectGroupSerializer
    lookup_field = 'uuid'
    filter_backends = (filters.GenericRoleFilter, core_filters.DjangoMappingFilterBackend)
    # permission_classes = (permissions.IsAuthenticated,)  # TODO: Add permissions for Create/Update
    filter_class = ProjectGroupFilter


class ProjectGroupMembershipFilter(django_filters.FilterSet):
    project_group = django_filters.CharFilter(
        name='projectgroup__uuid',
    )

    project_group_name = django_filters.CharFilter(
        name='projectgroup__name',
        lookup_type='icontains',
    )

    project = django_filters.CharFilter(
        name='project__uuid',
    )

    project_name = django_filters.CharFilter(
        name='project__name',
        lookup_type='icontains',
    )

    class Meta(object):
        model = models.ProjectGroup.projects.through
        fields = [
            'project_group',
            'project_group_name',
            'project',
            'project_name',
        ]


class ProjectGroupMembershipViewSet(rf_mixins.CreateModelMixin,
                                    rf_mixins.RetrieveModelMixin,
                                    rf_mixins.DestroyModelMixin,
                                    mixins.ListModelMixin,
                                    rf_viewsets.GenericViewSet):
    """List of project groups members that are accessible by this user.

    http://nodeconductor.readthedocs.org/en/latest/api/api.html#managing-project-roles
    """

    queryset = models.ProjectGroup.projects.through.objects.all()
    serializer_class = serializers.ProjectGroupMembershipSerializer
    filter_backends = (filters.GenericRoleFilter, rf_filter.DjangoFilterBackend,)
    filter_class = ProjectGroupMembershipFilter

# XXX: This should be put to models
filters.set_permissions_for_model(
    models.ProjectGroup.projects.through,
    customer_path='projectgroup__customer',
)


class UserFilter(django_filters.FilterSet):
    project_group = django_filters.CharFilter(
        name='groups__projectrole__project__project_groups__name',
        distinct=True,
        lookup_type='icontains',
    )
    project = django_filters.CharFilter(
        name='groups__projectrole__project__name',
        distinct=True,
        lookup_type='icontains',
    )

    full_name = django_filters.CharFilter(lookup_type='icontains')
    username = django_filters.CharFilter()
    native_name = django_filters.CharFilter(lookup_type='icontains')
    organization = django_filters.CharFilter(lookup_type='icontains')
    job_title = django_filters.CharFilter(lookup_type='icontains')
    description = django_filters.CharFilter(lookup_type='icontains')
    email = django_filters.CharFilter(lookup_type='icontains')
    is_active = django_filters.BooleanFilter()

    class Meta(object):
        model = User
        fields = [
            'full_name',
            'native_name',
            'organization',
            'email',
            'phone_number',
            'description',
            'job_title',
            'project',
            'project_group',
            'username',
            'civil_number',
            'is_active',
        ]
        order_by = [
            'full_name',
            'native_name',
            'organization',
            'email',
            'phone_number',
            'description',
            'job_title',
            'username',
            'is_active',
            # descending
            '-full_name',
            '-native_name',
            '-organization',
            '-email',
            '-phone_number',
            '-description',
            '-job_title',
            '-username',
            '-is_active',
        ]


class UserViewSet(viewsets.ModelViewSet):
    """
    List of NodeConductor users.

    http://nodeconductor.readthedocs.org/en/latest/api/api.html#user-management
    """

    queryset = User.objects.all()
    serializer_class = serializers.UserSerializer
    lookup_field = 'uuid'
    permission_classes = (
        rf_permissions.IsAuthenticated,
        permissions.IsAdminOrOwner,
    )
    filter_class = UserFilter

    def get_queryset(self):
        user = self.request.user
        queryset = super(UserViewSet, self).get_queryset()

        # ?current
        current_user = self.request.QUERY_PARAMS.get('current', None)
        if current_user is not None and not user.is_anonymous():
            queryset = User.objects.filter(uuid=user.uuid)

        # TODO: refactor to a separate endpoint or structure
        # a special query for all users with assigned privileges that the current user can remove privileges from
        if 'potential' in self.request.QUERY_PARAMS:
            # XXX: Let the DB cry...
            connected_customers_query = models.Customer.objects.filter(
                Q(roles__permission_group__user=user)
                |
                Q(projects__roles__permission_group__user=user)
                |
                Q(project_groups__roles__permission_group__user=user)
            ).distinct()

            # check if we need to filter potential users by a customer
            potential_customer = self.request.QUERY_PARAMS.get('potential_customer', None)
            if potential_customer:
                connected_customers_query = connected_customers_query.filter(uuid=potential_customer)
                connected_customers_query = filters.filter_queryset_for_user(connected_customers_query, user)

            connected_customers = list(connected_customers_query.all())

            queryset = queryset.filter(
                # customer owners
                Q(
                    groups__customerrole__customer__in=connected_customers,
                )
                |
                Q(
                    groups__projectrole__project__customer__in=connected_customers,
                )
                |
                Q(
                    groups__projectgrouprole__project_group__customer__in=connected_customers,
                )
                |
                # users with no role
                Q(
                    groups__customerrole=None,
                    groups__projectrole=None,
                    groups__projectgrouprole=None,
                )
            ).distinct()

        if not user.is_staff:
            queryset = queryset.filter(is_active=True)

        return queryset

    @action()
    def password(self, request, uuid=None):
        try:
            user = User.objects.get(uuid=uuid)
        except User.DoesNotExist:
            raise Http404()

        password_data = request.DATA
        serializer = serializers.PasswordSerializer(data=password_data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        user.set_password(password_data['password'])
        user.save()

        return Response({'detail': "Password has been successfully updated"},
                        status=status.HTTP_200_OK)


# TODO: cover filtering/ordering with tests
class ProjectPermissionFilter(django_filters.FilterSet):
    project = django_filters.CharFilter(
        name='group__projectrole__project__uuid',
    )
    username = django_filters.CharFilter(
        name='user__username',
        lookup_type='icontains',
    )
    full_name = django_filters.CharFilter(
        name='user__full_name',
        lookup_type='icontains',
    )
    native_name = django_filters.CharFilter(
        name='user__native_name',
        lookup_type='icontains',
    )
    role = django_filters.NumberFilter(
        name='group__projectrole__role_type',
    )

    class Meta(object):
        model = User.groups.through
        fields = [
            'role',
            'project',
            'username',
            'full_name',
            'native_name',
        ]
        order_by = [
            'user__username',
            'user__full_name',
            'user__native_name',
            # desc
            '-user__username',
            '-user__full_name',
            '-user__native_name',
        ]


class ProjectPermissionViewSet(rf_mixins.RetrieveModelMixin,
                               mixins.ListModelMixin,
                               rf_viewsets.GenericViewSet):
    queryset = User.groups.through.objects.exclude(group__projectrole=None)
    serializer_class = serializers.ProjectPermissionSerializer
    permission_classes = (rf_permissions.IsAuthenticated,
                          rf_permissions.DjangoObjectPermissions)
    filter_backends = (filters.GenericRoleFilter, rf_filter.DjangoFilterBackend,)
    filter_class = ProjectPermissionFilter

    def can_save(self, user_group):
        user = self.request.user
        if user.is_staff:
            return True

        project = user_group.group.projectrole.project

        if project.has_user(user, ProjectRole.MANAGER):
            return True

        if project.customer.has_user(user, CustomerRole.OWNER):
            return True

        for project_group in project.project_groups.iterator():
            if project_group.has_user(user, ProjectGroupRole.MANAGER):
                return True

        return False

    def get_queryset(self):
        queryset = super(ProjectPermissionViewSet, self).get_queryset()

        # TODO: refactor against django filtering
        user_uuid = self.request.QUERY_PARAMS.get('user', None)
        if user_uuid is not None:
            queryset = queryset.filter(user__uuid=user_uuid)

        return queryset

    def get_success_headers(self, data):
        try:
            return {'Location': data[api_settings.URL_FIELD_NAME]}
        except (TypeError, KeyError):
            return {}

    def pre_save(self, obj):
        if not self.can_save(obj):
            raise PermissionDenied()

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.DATA, files=request.FILES)

        if serializer.is_valid():
            self.pre_save(serializer.object)

            project = serializer.object.group.projectrole.project
            user = serializer.object.user
            role = serializer.object.group.projectrole.role_type

            self.object, created = project.add_user(user, role)

            self.post_save(self.object, created=created)

            # Instantiating serializer again, this time with instance
            # to make urls render properly.
            serializer = self.get_serializer(instance=self.object)

            headers = self.get_success_headers(serializer.data)

            if created:
                return Response(
                    serializer.data,
                    status=status.HTTP_201_CREATED,
                    headers=headers,
                )
            else:
                return Response(
                    {'detail': 'Permissions were not modified'},
                    status=status.HTTP_304_NOT_MODIFIED,
                    headers=headers,
                )

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def destroy(self, request, *args, **kwargs):
        obj = self.get_object()
        self.pre_delete(obj)

        user = obj.user
        project = obj.group.projectrole.project
        role = obj.group.projectrole.role_type

        project.remove_user(user, role)

        self.post_delete(obj)

        return Response(status=status.HTTP_204_NO_CONTENT)


class ProjectGroupPermissionFilter(django_filters.FilterSet):
    project_group = django_filters.CharFilter(
        name='group__projectgrouprole__project_group__uuid',
    )
    username = django_filters.CharFilter(
        name='user__username',
        lookup_type='icontains',
    )
    full_name = django_filters.CharFilter(
        name='user__full_name',
        lookup_type='icontains',
    )
    native_name = django_filters.CharFilter(
        name='user__native_name',
        lookup_type='icontains',
    )
    role = django_filters.NumberFilter(
        name='group__projectgrouprole__role_type',
    )

    class Meta(object):
        model = User.groups.through
        fields = [
            'role',
            'project_group',
            'username',
            'full_name',
            'native_name',
        ]
        order_by = [
            'user__username',
            'user__full_name',
            'user__native_name',
            # desc
            '-user__username',
            '-user__full_name',
            '-user__native_name',

        ]


class ProjectGroupPermissionViewSet(rf_mixins.RetrieveModelMixin,
                                    mixins.ListModelMixin,
                                    rf_viewsets.GenericViewSet):
    queryset = User.groups.through.objects.all()
    serializer_class = serializers.ProjectGroupPermissionSerializer
    permission_classes = (rf_permissions.IsAuthenticated,
                          rf_permissions.DjangoObjectPermissions)
    filter_backends = (rf_filter.DjangoFilterBackend,)
    filter_class = ProjectGroupPermissionFilter

    def get_queryset(self):
        queryset = super(ProjectGroupPermissionViewSet, self).get_queryset()
        queryset = queryset.exclude(group__projectgrouprole=None)

        # TODO: refactor against django filtering
        user_uuid = self.request.QUERY_PARAMS.get('user', None)
        if user_uuid is not None:
            queryset = queryset.filter(user__uuid=user_uuid)

        # TODO: Test for it!
        # XXX: This should be removed after permissions refactoring
        if not self.request.user.is_staff:
            queryset = queryset.filter(
                Q(group__projectgrouprole__project_group__customer__roles__permission_group__user=self.request.user,
                  group__projectgrouprole__project_group__customer__roles__role_type=models.CustomerRole.OWNER)
                |
                Q(group__projectgrouprole__project_group__projects__roles__permission_group__user=self.request.user,
                  group__projectgrouprole__project_group__customer__roles__role_type=models.ProjectRole.MANAGER)
                |
                Q(group__projectgrouprole__project_group__roles__permission_group__user=self.request.user)
            ).distinct()

        return queryset

    def can_save(self, user_group):
        user = self.request.user
        if user.is_staff:
            return True

        project_group = user_group.group.projectgrouprole.project_group

        if project_group.customer.has_user(user, CustomerRole.OWNER):
            return True

        return False

    def pre_save(self, obj):
        if not self.can_save(obj):
            raise PermissionDenied()

    def get_success_headers(self, data):
        try:
            return {'Location': data[api_settings.URL_FIELD_NAME]}
        except (TypeError, KeyError):
            return {}

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.DATA, files=request.FILES)

        if serializer.is_valid():
            self.pre_save(serializer.object)

            project_group = serializer.object.group.projectgrouprole.project_group
            user = serializer.object.user
            role = serializer.object.group.projectgrouprole.role_type

            self.object, created = project_group.add_user(user, role)

            self.post_save(self.object, created=created)

            # Instantiating serializer again, this time with instance
            # to make urls render properly.
            serializer = self.get_serializer(instance=self.object)

            headers = self.get_success_headers(serializer.data)

            if created:
                return Response(
                    serializer.data,
                    status=status.HTTP_201_CREATED,
                    headers=headers,
                )
            else:
                return Response(
                    {'detail': 'Permissions were not modified'},
                    status=status.HTTP_304_NOT_MODIFIED,
                    headers=headers,
                )

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def destroy(self, request, *args, **kwargs):
        obj = self.get_object()
        self.pre_delete(obj)

        user = obj.user
        project_group = obj.group.projectgrouprole.project_group
        role = obj.group.projectgrouprole.role_type

        project_group.remove_user(user, role)

        return Response(status=status.HTTP_204_NO_CONTENT)


class CustomerPermissionFilter(django_filters.FilterSet):
    customer = django_filters.CharFilter(
        name='group__customerrole__customer__uuid',
    )
    username = django_filters.CharFilter(
        name='user__username',
        lookup_type='icontains',
    )
    full_name = django_filters.CharFilter(
        name='user__full_name',
        lookup_type='icontains',
    )
    native_name = django_filters.CharFilter(
        name='user__native_name',
        lookup_type='icontains',
    )
    role = filters.CustomerRoleFilter(
        name='group__customerrole__role_type',
    )

    class Meta(object):
        model = User.groups.through
        fields = [
            'role',
            'customer',
            'username',
            'full_name',
            'native_name',
        ]
        order_by = [
            'user__username',
            'user__full_name',
            'user__native_name',
            # desc
            '-user__username',
            '-user__full_name',
            '-user__native_name',

        ]


class CustomerPermissionViewSet(rf_mixins.RetrieveModelMixin,
                                mixins.ListModelMixin,
                                rf_viewsets.GenericViewSet):
    queryset = User.groups.through.objects.exclude(group__customerrole=None)
    serializer_class = serializers.CustomerPermissionSerializer
    permission_classes = (rf_permissions.IsAuthenticated,
                          rf_permissions.DjangoObjectPermissions)
    filter_backends = (rf_filter.DjangoFilterBackend,)
    filter_class = CustomerPermissionFilter

    def can_save(self, user_group):
        user = self.request.user
        if user.is_staff:
            return True

        customer = user_group.group.customerrole.customer

        if customer.has_user(user, CustomerRole.OWNER):
            return True

        return False

    def get_queryset(self):
        queryset = super(CustomerPermissionViewSet, self).get_queryset()

        # TODO: Test for it!
        if not self.request.user.is_staff:
            queryset = queryset.filter(
                Q(group__customerrole__customer__roles__permission_group__user=self.request.user,
                  group__customerrole__customer__roles__role_type=models.CustomerRole.OWNER)
                |
                Q(group__customerrole__customer__projects__roles__permission_group__user=self.request.user)
                |
                Q(group__customerrole__customer__project_groups__roles__permission_group__user=self.request.user)
            ).distinct()

        return queryset

    def get_success_headers(self, data):
        try:
            return {'Location': data[api_settings.URL_FIELD_NAME]}
        except (TypeError, KeyError):
            return {}

    def pre_save(self, obj):
        if not self.can_save(obj):
            raise PermissionDenied()

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.DATA, files=request.FILES)

        if serializer.is_valid():
            self.pre_save(serializer.object)

            customer = serializer.object.group.customerrole.customer
            user = serializer.object.user
            role = serializer.object.group.customerrole.role_type

            self.object, created = customer.add_user(user, role)

            self.post_save(self.object, created=created)

            # Instantiating serializer again, this time with instance
            # to make urls render properly.
            serializer = self.get_serializer(instance=self.object)

            headers = self.get_success_headers(serializer.data)

            if created:
                return Response(
                    serializer.data,
                    status=status.HTTP_201_CREATED,
                    headers=headers,
                )
            else:
                return Response(
                    {'detail': 'Permissions were not modified'},
                    status=status.HTTP_304_NOT_MODIFIED,
                    headers=headers,
                )

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def destroy(self, request, *args, **kwargs):
        obj = self.get_object()
        self.pre_delete(obj)

        user = obj.user
        customer = obj.group.customerrole.customer
        role = obj.group.customerrole.role_type

        customer.remove_user(user, role)

        self.post_delete(obj)

        return Response(status=status.HTTP_204_NO_CONTENT)


class CreationTimeStatsView(views.APIView):

    def get(self, request, format=None):
        month = 60 * 60 * 24 * 30
        data = {
            'start_timestamp': request.QUERY_PARAMS.get('from', int(time.time() - month)),
            'end_timestamp': request.QUERY_PARAMS.get('to', int(time.time())),
            'segments_count': request.QUERY_PARAMS.get('datapoints', 6),
            'model_name': request.QUERY_PARAMS.get('type', 'customer'),
        }

        serializer = serializers.CreationTimeStatsSerializer(data=data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        stats = serializer.get_stats(request.user)
        return Response(stats, status=status.HTTP_200_OK)
