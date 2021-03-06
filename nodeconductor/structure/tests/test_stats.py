from __future__ import unicode_literals

from datetime import timedelta

from django.core.urlresolvers import reverse
from django.utils import timezone
from rest_framework import test, status

# This test contains dependencies from iaas and backup app,
# but it will be moved to separate app 'stats' in future, so this is ok
from nodeconductor.backup.tests import factories as backup_factories
from nodeconductor.core import utils as core_utils
from nodeconductor.iaas.tests import factories as iaas_factories
from nodeconductor.structure import models
from nodeconductor.structure.tests import factories


class CreationTimeStatsTest(test.APITransactionTestCase):

    def setUp(self):
        # customers
        self.old_customer = factories.CustomerFactory(created=timezone.now() - timedelta(days=10))
        self.new_customer = factories.CustomerFactory(created=timezone.now() - timedelta(days=1))
        # groups
        self.old_project_group = factories.ProjectGroupFactory(
            customer=self.old_customer, created=timezone.now() - timedelta(days=10))
        self.new_project_group = factories.ProjectGroupFactory(
            customer=self.new_customer, created=timezone.now() - timedelta(days=1))
        # projects
        self.old_projects = factories.ProjectFactory.create_batch(
            3, created=timezone.now() - timedelta(days=10), customer=self.old_customer)
        self.new_projects = factories.ProjectFactory.create_batch(
            3, created=timezone.now() - timedelta(days=1), customer=self.new_customer)
        # users
        self.staff = factories.UserFactory(is_staff=True)
        self.old_cusotmer_owner = factories.UserFactory()
        self.old_customer.add_user(self.old_cusotmer_owner, models.CustomerRole.OWNER)
        self.new_project_group_manager = factories.UserFactory()
        self.new_project_group.add_user(self.new_project_group_manager, models.ProjectGroupRole.MANAGER)
        self.all_projects_admin = factories.UserFactory()
        for p in self.old_projects + self.new_projects:
            p.add_user(self.all_projects_admin, models.ProjectRole.ADMINISTRATOR)

        self.url = reverse('stats_creation_time')
        self.default_data = {
            'from': core_utils.datetime_to_timestamp(timezone.now() - timedelta(days=12)),
            'datapoints': 2,
        }

    def execute_request_with_data(self, user, extra_data):
        self.client.force_authenticate(user)
        data = self.default_data
        data.update(extra_data)
        return self.client.get(self.url, data)

    def test_staff_receive_all_stats_about_customers(self):
        # when
        response = self.execute_request_with_data(self.staff, {'type': 'customer'})
        # then
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 2, 'Response has to contain 2 datapoints')
        self.assertEqual(response.data[0]['value'], 1, 'First datapoint has to contain 1 customer (old)')
        self.assertEqual(response.data[1]['value'], 1, 'Second datapoint has to contain 1 customer (new)')

    def test_customer_owner_receive_stats_only_about_his_cusotmers(self):
        # when
        response = self.execute_request_with_data(self.old_cusotmer_owner, {'type': 'customer'})
        # then
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 2, 'Response has to contain 2 datapoints')
        self.assertEqual(response.data[0]['value'], 1, 'First datapoint has to contain 1 customer (old)')
        self.assertEqual(response.data[1]['value'], 0, 'Second datapoint has to contain 0 customers')

    def test_admin_receive_info_about_his_projects(self):
        # when
        response = self.execute_request_with_data(self.all_projects_admin, {'type': 'project', 'datapoints': 3})
        # then
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 3, 'Response has to contain 3 datapoints')
        self.assertEqual(response.data[0]['value'], 3, 'First datapoint has to contain 3 projects (old)')
        self.assertEqual(response.data[1]['value'], 0, 'Second datapoint has to contain 0 projects')
        self.assertEqual(response.data[2]['value'], 3, 'Third datapoint has to contain 3 projects (new)')

    def test_staff_receive_project_groups_stats_only_for_given_time_interval(self):
        # when
        data = {
            'from': core_utils.datetime_to_timestamp(timezone.now() - timedelta(days=8)),
            'to': core_utils.datetime_to_timestamp(timezone.now()),
            'datapoints': 2,
            'type': 'project_group',
        }
        response = self.execute_request_with_data(self.staff, data)
        # then
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 2, 'Response has to contain 2 datapoints')
        self.assertEqual(response.data[0]['value'], 0, 'First datapoint has to contain 0 project_groups')
        self.assertEqual(response.data[1]['value'], 1, 'Second datapoint has to contain 1 project_group(new)')

    def test_group_manager_receive_project_groups_stats_only_for_his_project_groups(self):
        # when
        response = self.execute_request_with_data(self.new_project_group_manager, {'type': 'project_group'})
        # then
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 2, 'Response has to contain 2 datapoints')
        self.assertEqual(response.data[0]['value'], 0, 'First datapoint has to contain 0 project_groups')
        self.assertEqual(response.data[1]['value'], 1, 'Second datapoint has to contain 1 project_groups (new)')

    def test_user_with_no_permissions_receive_only_zero_stats(self):
        # when
        response = self.execute_request_with_data(self.all_projects_admin, {'type': 'project_group'})
        # then
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 2, 'Response has to contain 2 datapoints')
        self.assertEqual(response.data[0]['value'], 0, 'First datapoint has to contain 0 project_groups')
        self.assertEqual(response.data[1]['value'], 0, 'Second datapoint has to contain 0 project_groups')
