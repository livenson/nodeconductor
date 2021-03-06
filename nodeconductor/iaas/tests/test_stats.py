from django.core.urlresolvers import reverse
from mock import patch, Mock
from rest_framework import test, status

from nodeconductor.backup.tests import factories as backup_factories
from nodeconductor.iaas import models
from nodeconductor.iaas.tests import factories
from nodeconductor.structure import models as structure_models
from nodeconductor.structure.tests import factories as structure_factories


class CustomerStatsTest(test.APITransactionTestCase):

    def setUp(self):
        self.customer = structure_factories.CustomerFactory()
        self.other_customer = structure_factories.CustomerFactory()
        cloud = factories.CloudFactory(customer=self.customer)

        self.staff = structure_factories.UserFactory(is_staff=True)
        self.admin = structure_factories.UserFactory()
        self.group_manager = structure_factories.UserFactory()
        self.owner = structure_factories.UserFactory()

        self.customer.add_user(self.owner, structure_models.CustomerRole.OWNER)

        self.project_group = structure_factories.ProjectGroupFactory(customer=self.customer)
        self.admin_project = structure_factories.ProjectFactory(customer=self.customer)
        self.manager_project = structure_factories.ProjectFactory(customer=self.customer)
        self.other_customer_project = structure_factories.ProjectFactory(customer=self.other_customer)

        self.manager_project.project_groups.add(self.project_group)
        self.project_group.add_user(self.group_manager, structure_models.ProjectGroupRole.MANAGER)
        self.admin_project.add_user(self.admin, structure_models.ProjectRole.ADMINISTRATOR)

        self.manager_instances = factories.InstanceFactory.create_batch(
            2,
            cloud_project_membership__project=self.manager_project,
            cloud_project_membership__cloud=cloud,
        )
        self.admins_instances = factories.InstanceFactory.create_batch(
            2,
            cloud_project_membership__project=self.admin_project,
            cloud_project_membership__cloud=cloud,
        )

        self.url = reverse('stats_customer')

    def test_staff_receive_statistics_for_all_customers(self):
        self.client.force_authenticate(self.staff)

        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        expected_result = [
            {
                'name': self.customer.name,
                'projects': 2,
                'project_groups': 1,
                'instances': 4,
            },
            {
                'name': self.other_customer.name,
                'projects': 1,
                'project_groups': 0,
                'instances': 0,
            }
        ]
        self.assertItemsEqual(response.data, expected_result)

    def test_owner_receive_statistics_for_his_customer(self):
        self.client.force_authenticate(self.owner)

        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        expected_result = [
            {
                'name': self.customer.name,
                'projects': 2,
                'project_groups': 1,
                'instances': 4,
            },
        ]
        self.assertItemsEqual(response.data, expected_result)

    def test_group_manager_receive_statistics_for_his_group(self):
        self.client.force_authenticate(self.group_manager)

        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        expected_result = [
            {
                'name': self.customer.name,
                'projects': 1,
                'project_groups': 1,
                'instances': 2,
            },
        ]
        self.assertItemsEqual(response.data, expected_result)

    def test_admin_receive_statistics_for_his_project(self):
        self.client.force_authenticate(self.admin)

        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        expected_result = [
            {
                'name': self.customer.name,
                'projects': 1,
                'project_groups': 0,
                'instances': 2,
            },
        ]
        self.assertItemsEqual(response.data, expected_result)


class UsageStatsTest(test.APITransactionTestCase):

    def setUp(self):
        self.customer1 = structure_factories.CustomerFactory()
        self.customer2 = structure_factories.CustomerFactory()

        self.staff = structure_factories.UserFactory(is_staff=True)
        self.owner = structure_factories.UserFactory()
        self.group_manager = structure_factories.UserFactory()
        self.customer1.add_user(self.owner, structure_models.CustomerRole.OWNER)

        self.project1 = structure_factories.ProjectFactory(customer=self.customer1)
        self.project2 = structure_factories.ProjectFactory(customer=self.customer2)
        self.project_group = structure_factories.ProjectGroupFactory(customer=self.customer1)
        self.project_group.projects.add(self.project1)
        self.project_group.add_user(self.group_manager, structure_models.ProjectGroupRole.MANAGER)

        self.instances1 = factories.InstanceFactory.create_batch(2, cloud_project_membership__project=self.project1)
        self.instances2 = factories.InstanceFactory.create_batch(2, cloud_project_membership__project=self.project2)

        self.url = reverse('stats_usage')

        self.expected_datapoints = [
            {'from': 1L, 'to': 471970877L, 'value': 0},
            {'from': 471970877L, 'to': 943941753L, 'value': 0},
            {'from': 943941753L, 'to': 1415912629L, 'value': 3.0}
        ]

    def _get_patched_client(self):
        patched_cliend = Mock()
        patched_cliend.get_item_stats = Mock(return_value=self.expected_datapoints)
        return patched_cliend

    def test_staff_receive_stats_for_all_customers(self):
        self.client.force_authenticate(self.staff)

        patched_cliend = self._get_patched_client()
        with patch('nodeconductor.iaas.serializers.ZabbixDBClient', return_value=patched_cliend) as patched:
            patched.items = {'cpu': {'key': 'cpu_key', 'table': 'cpu_table'}}
            data = {'item': 'cpu', 'from': 1, 'to': 1415912629, 'datapoints': 3}
            response = self.client.get(self.url, data)
            self.assertEqual(response.status_code, status.HTTP_200_OK)
            expected_data = [
                {
                    'name': customer.name,
                    'datapoints': self.expected_datapoints if customer in (self.customer1, self.customer2) else []
                }
                for customer in structure_models.Customer.objects.all()
            ]
            self.assertItemsEqual(response.data, expected_data)

    def test_staff_receive_stats_for_all_projects(self):
        self.client.force_authenticate(self.staff)

        patched_cliend = self._get_patched_client()
        with patch('nodeconductor.iaas.serializers.ZabbixDBClient', return_value=patched_cliend) as patched:
            patched.items = {'cpu': {'key': 'cpu_key', 'table': 'cpu_table'}}
            data = {'item': 'cpu', 'from': 1, 'to': 1415912629, 'datapoints': 3, 'aggregate': 'project'}
            response = self.client.get(self.url, data)
            self.assertEqual(response.status_code, status.HTTP_200_OK)
            expected_data = [
                {'name': project.name, 'datapoints': self.expected_datapoints}
                for project in structure_models.Project.objects.all()
            ]
            self.assertItemsEqual(response.data, expected_data)

    def test_owner_receive_data_for_his_project(self):
        self.client.force_authenticate(self.owner)

        patched_cliend = self._get_patched_client()
        with patch('nodeconductor.iaas.serializers.ZabbixDBClient', return_value=patched_cliend) as patched:
            patched.items = {'cpu': {'key': 'cpu_key', 'table': 'cpu_table'}}
            data = {'item': 'cpu', 'from': 1, 'to': 1415912629, 'datapoints': 3}
            response = self.client.get(self.url, data)
            self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
            expected_data = [{'name': self.customer1.name, 'datapoints': self.expected_datapoints}]
            self.assertItemsEqual(response.data, expected_data)

    def test_group_manager_receive_stats_for_his_group(self):
        self.client.force_authenticate(self.group_manager)

        patched_cliend = self._get_patched_client()
        with patch('nodeconductor.iaas.serializers.ZabbixDBClient', return_value=patched_cliend) as patched:
            patched.items = {'cpu': {'key': 'cpu_key', 'table': 'cpu_table'}}
            data = {'item': 'cpu', 'from': 1, 'to': 1415912629, 'datapoints': 3, 'aggregate': 'project_group'}
            response = self.client.get(self.url, data)
            self.assertEqual(response.status_code, status.HTTP_200_OK)
            expected_data = [{'name': self.project_group.name, 'datapoints': self.expected_datapoints}]
            self.assertItemsEqual(response.data, expected_data)

    def test_project_can_be_filtered_by_uuid(self):
        self.client.force_authenticate(self.staff)

        patched_client = self._get_patched_client()
        with patch('nodeconductor.iaas.serializers.ZabbixDBClient', return_value=patched_client) as patched:
            patched.items = {'cpu': {'key': 'cpu_key', 'table': 'cpu_table'}}
            data = {
                'item': 'cpu', 'from': 1, 'to': 1415912629, 'datapoints': 3,
                'aggregate': 'project', 'uuid': self.project1.uuid.hex
            }
            response = self.client.get(self.url, data)
            self.assertEqual(response.status_code, status.HTTP_200_OK)
            expected_data = [{'name': self.project1.name, 'datapoints': self.expected_datapoints}]
            self.assertItemsEqual(response.data, expected_data)


class ResourceStatsTest(test.APITransactionTestCase):

    def setUp(self):
        self.auth_url = 'http://example.com/'

        self.project1 = structure_factories.ProjectFactory()
        self.project2 = structure_factories.ProjectFactory()

        self.cloud = factories.CloudFactory(auth_url=self.auth_url)
        membership1 = models.CloudProjectMembership.objects.create(
            cloud=self.cloud, project=self.project1, tenant_id='1')
        membership2 = models.CloudProjectMembership.objects.create(
            cloud=self.cloud, project=self.project2, tenant_id='2')

        self.quota1 = factories.ResourceQuotaFactory(cloud_project_membership=membership1)
        self.quota2 = factories.ResourceQuotaFactory(cloud_project_membership=membership2)
        self.quota_usage1 = factories.ResourceQuotaUsageFactory(cloud_project_membership=membership1)
        self.quota_usage2 = factories.ResourceQuotaUsageFactory(cloud_project_membership=membership2)

        self.user = structure_factories.UserFactory()
        self.staff = structure_factories.UserFactory(is_staff=True)

        self.url = reverse('stats_resource')

    def test_resource_stats_is_not_available_for_user(self):
        self.client.force_authenticate(self.user)

        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_resource_stats_auth_url_parameter_have_to_be_defined(self):
        self.client.force_authenticate(self.staff)

        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_resource_stats_auth_url_have_to_be_one_of_cloud_urls(self):
        self.client.force_authenticate(self.staff)

        data = {'auth_url': 'some_random_url'}
        response = self.client.get(self.url, data)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_resource_stats_returns_backend_resource_stats(self):
        mocked_backend = Mock()
        backend_result = {
            u'count': 2, u'vcpus_used': 0, u'local_gb_used': 0, u'memory_mb': 7660, u'current_workload': 0,
            u'vcpus': 2, u'running_vms': 0, u'free_disk_gb': 12, u'disk_available_least': 6, u'local_gb': 12,
            u'free_ram_mb': 6636, u'memory_mb_used': 1024
        }

        mocked_backend.get_resource_stats = Mock(return_value=backend_result)
        expected_result = backend_result.copy()
        expected_result.update({
            'vcpu_quota': self.quota1.vcpu + self.quota2.vcpu,
            'memory_quota': self.quota1.ram + self.quota2.ram,
            'storage_quota': self.quota1.storage + self.quota2.storage,
            'backup_quota': self.quota1.backup_storage + self.quota2.backup_storage,
        })
        expected_result['backups'] = self.quota_usage1.backup_storage + self.quota_usage2.backup_storage

        with patch('nodeconductor.iaas.models.Cloud.get_backend', return_value=mocked_backend):
            self.client.force_authenticate(self.staff)

            response = self.client.get(self.url, {'auth_url': self.auth_url})
            self.assertEqual(response.status_code, status.HTTP_200_OK)
            self.assertEqual(response.data, expected_result)
            mocked_backend.get_resource_stats.assert_called_once_with(self.auth_url)


class QuotaStatsTest(test.APITransactionTestCase):

    def setUp(self):
        self.customer = structure_factories.CustomerFactory()
        self.project_group = structure_factories.ProjectGroupFactory(customer=self.customer)
        self.project1 = structure_factories.ProjectFactory(customer=self.customer)
        self.project2 = structure_factories.ProjectFactory(customer=self.customer)
        self.membership1 = factories.CloudProjectMembershipFactory(project=self.project1)
        self.membership2 = factories.CloudProjectMembershipFactory(project=self.project2)

        self.project_group.projects.add(self.project1)
        # quotas:
        for membership in self.membership1, self.membership2:
            factories.ResourceQuotaFactory(cloud_project_membership=membership)
            factories.ResourceQuotaUsageFactory(cloud_project_membership=membership)
        # users
        self.staff = structure_factories.UserFactory(is_staff=True)
        self.customer_owner = structure_factories.UserFactory()
        self.customer.add_user(self.customer_owner, structure_models.CustomerRole.OWNER)
        self.project_group_manager = structure_factories.UserFactory()
        self.project_group.add_user(self.project_group_manager, structure_models.ProjectGroupRole.MANAGER)
        self.project1_admin = structure_factories.UserFactory()
        self.project1.add_user(self.project1_admin, structure_models.ProjectRole.ADMINISTRATOR)

        fields = ['vcpu', 'ram', 'storage', 'max_instances', 'backup_storage']

        self.expected_quotas_for_project1 = dict((f, getattr(self.membership1.resource_quota, f)) for f in fields)
        self.expected_quotas_for_project1.update(
            dict((f + '_usage', getattr(self.membership1.resource_quota_usage, f)) for f in fields))

        self.expected_quotas_for_both_projects = self.expected_quotas_for_project1.copy()
        for f in fields:
            self.expected_quotas_for_both_projects[f] += getattr(self.membership2.resource_quota, f)
            self.expected_quotas_for_both_projects[f + '_usage'] += getattr(self.membership2.resource_quota_usage, f)

    def execute_request_with_data(self, user, data):
        self.client.force_authenticate(user)
        url = 'http://testserver' + reverse('stats_quota')
        return self.client.get(url, data)

    def test_customer_owner_receive_quotas_for_projects_from_his_customer(self):
        # when
        response = self.execute_request_with_data(self.customer_owner, {'aggregate': 'customer'})
        # then
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data, self.expected_quotas_for_both_projects)

    def test_project_group_manager_receive_quotas_for_projects_from_his_group(self):
        # when
        response = self.execute_request_with_data(self.project_group_manager, {'aggregate': 'project_group'})
        # then
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data, self.expected_quotas_for_project1)

    def test_project_admin_receive_quotas_for_his_projects(self):
        # when
        response = self.execute_request_with_data(self.project1_admin, {'aggregate': 'project'})
        # then
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data, self.expected_quotas_for_project1)

    def test_proejct_group_manager_does_not_receive_quotas_for_other_cusotmer_projects(self):
        # when
        response = self.execute_request_with_data(self.project_group_manager, {'aggregate': 'customer'})
        # then
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data, self.expected_quotas_for_project1)

    def test_project_without_both_quotas_is_ignored(self):
        # project without quotas
        structure_factories.ProjectFactory(customer=self.customer)
        # when
        response = self.execute_request_with_data(self.staff, {'aggregate': 'customer'})
        # then
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data, self.expected_quotas_for_both_projects)

    def test_project_can_be_filtered_by_uuid(self):
        # when
        response = self.execute_request_with_data(
            self.staff, {'aggregate': 'project', 'uuid': self.project1.uuid.hex})
        # then
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data, self.expected_quotas_for_project1)
