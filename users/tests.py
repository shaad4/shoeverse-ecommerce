from django.test import TestCase, override_settings
from django.urls import reverse
from products.models import Product


class HomeTests(TestCase):
    def test_empty_catalog_renders_without_hardcoded_ids(self):
        response = self.client.get(reverse("home"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "New arrivals are coming soon.")

    def test_active_product_without_image_renders(self):
        Product.objects.create(name="Available Shoe", price="1000", color="Black", category="MEN")
        Product.objects.create(
            name="Hidden Shoe", price="1000", color="Black", category="MEN", is_active=False,
        )
        response = self.client.get(reverse("home"))
        self.assertContains(response, "Available Shoe")
        self.assertNotContains(response, "Hidden Shoe")

    @override_settings(HOMEPAGE_FEATURED_PRODUCT_IDS=[999999])
    def test_deleted_featured_products_do_not_break_home(self):
        self.assertEqual(self.client.get(reverse("home")).status_code, 200)
