from django.test import TestCase, Client
from django.contrib.auth.models import User
from django.urls import reverse

class HomeRedirectTests(TestCase):
    def setUp(self):
        self.c = Client()

    def test_home_redirects_to_orders_for_anonymous(self):
        resp = self.c.get(reverse('home'), follow=False)
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(resp['Location'].endswith('/orders/') or resp['Location'].endswith('/orders/'))

    def test_home_redirects_to_dashboard_for_staff(self):
        u = User.objects.create_user('boss', 'boss@example.com', 'x')
        u.is_staff = True
        u.save()
        self.c.force_login(u)
        resp = self.c.get(reverse('home'), follow=False)
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(resp['Location'].endswith('/dashboard/'))

class BasicPages200(TestCase):
    def setUp(self):
        self.c = Client()
        # staff pour /dashboard/ si nécessaire
        self.staff = User.objects.create_user('staff', 's@example.com', 'x')
        self.staff.is_staff = True
        self.staff.save()

    def test_orders_list_200(self):
        resp = self.c.get('/orders/', follow=True)
        self.assertEqual(resp.status_code, 200)

    def test_dashboard_index_200(self):
        self.c.force_login(self.staff)
        resp = self.c.get('/dashboard/', follow=True)
        self.assertEqual(resp.status_code, 200)
