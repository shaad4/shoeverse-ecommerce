from io import BytesIO, StringIO
from unittest.mock import patch
from PIL import Image
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from products.models import Product, ProductImage, ProductVariant, SubCategory


class DemoCatalogTests(TestCase):
    def setUp(self):
        buffer = BytesIO()
        Image.new("RGB", (16, 16), "white").save(buffer, format="JPEG")
        self.image = buffer.getvalue()

    def seed(self, **options):
        return call_command("seed_demo_products", stdout=StringIO(), stderr=StringIO(), **options)

    @patch("products.management.commands.seed_demo_products.Command.download_image")
    def test_import_has_photos_variants_and_preserves_stock_on_rerun(self, download):
        download.return_value = self.image
        self.seed(limit=3)
        self.assertEqual(Product.objects.count(), 3)
        self.assertEqual(ProductImage.objects.count(), 3)
        self.assertTrue(ProductVariant.objects.exists())
        variant = ProductVariant.objects.first()
        variant.stock = 2
        variant.save()
        self.seed(limit=3)
        variant.refresh_from_db()
        self.assertEqual(variant.stock, 2)
        self.assertEqual(Product.objects.count(), 3)
        self.assertEqual(ProductImage.objects.count(), 3)
        self.assertEqual(download.call_count, 3)

    @patch("products.management.commands.seed_demo_products.Command.download_image")
    def test_dry_run_does_not_download_or_write(self, download):
        output = StringIO()
        call_command("seed_demo_products", dry_run=True, stdout=output)
        self.assertIn("Validated 53 products", output.getvalue())
        download.assert_not_called()
        self.assertFalse(Product.objects.exists())

    @patch("products.management.commands.seed_demo_products.Command.download_image", side_effect=ValueError("bad photo"))
    def test_failed_image_does_not_create_partial_product(self, download):
        with self.assertRaises(CommandError):
            self.seed(limit=1)
        self.assertFalse(Product.objects.exists())
        self.assertFalse(SubCategory.objects.exists())

    @patch("products.management.commands.seed_demo_products.Command.download_image")
    def test_upload_failure_rolls_back_product_and_variants(self, download):
        download.return_value = self.image
        with patch("products.management.commands.seed_demo_products.ProductImage.save", side_effect=RuntimeError("storage")):
            with self.assertRaises(CommandError):
                self.seed(limit=1)
        self.assertFalse(Product.objects.exists())
        self.assertFalse(ProductVariant.objects.exists())
        self.assertFalse(SubCategory.objects.exists())

    def test_invalid_limit_is_rejected(self):
        with self.assertRaises(CommandError):
            self.seed(limit=0)
