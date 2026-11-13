import json
from decimal import Decimal, InvalidOperation
from io import BytesIO
from pathlib import Path
from urllib.parse import urlsplit

import requests
from PIL import Image
from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from products.models import Product, ProductImage, ProductVariant, SubCategory


class Command(BaseCommand):
    help = "Import the bundled demo catalog with real photos, without resetting existing stock."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, help="Import only the first N products")
        parser.add_argument("--dry-run", action="store_true", help="Validate and preview without network or database writes")

    def download_image(self, url):
        parsed = urlsplit(url)
        if parsed.scheme != "https" or parsed.hostname not in {
            "image.goat.com", "images.stockx.com", "nb.scene7.com",
        } or parsed.username or parsed.password or parsed.port not in (None, 443):
            raise ValueError("Unsupported image URL")
        with requests.get(url, stream=True, timeout=(10, 30), allow_redirects=False,
                          headers={"User-Agent": "ShoeverseDemoImporter/1.0"}) as response:
            response.raise_for_status()
            if response.status_code != 200:
                raise ValueError("Image server did not return HTTP 200")
            data = bytearray()
            for chunk in response.iter_content(65536):
                data.extend(chunk)
                if len(data) > 5 * 1024 * 1024:
                    raise ValueError("Image exceeds 5 MiB")
        with Image.open(BytesIO(data)) as picture:
            picture.verify()
        return bytes(data)

    def handle(self, *args, **options):
        path = Path(__file__).resolve().parents[2] / "data" / "demo_products.json"
        catalog = json.loads(path.read_text())
        rows = catalog["products"]
        if options["limit"] is not None:
            if options["limit"] < 1:
                raise CommandError("--limit must be positive")
            rows = rows[:options["limit"]]
        codes = set()
        for row in rows:
            try:
                price = Decimal(str(row["price"]))
                valid = (
                    row["code"] not in codes and price.is_finite() and price > 0
                    and row["category"] in {"MEN", "WOMEN", "KIDS"}
                    and row["name"] and row["brand"] and row["sizes"]
                    and all(str(size).isdigit() for size in row["sizes"])
                )
            except (KeyError, InvalidOperation, TypeError):
                valid = False
            if not valid:
                raise CommandError("Invalid or duplicate demo catalog entry")
            codes.add(row["code"])
        if options["dry_run"]:
            for row in rows:
                self.stdout.write(f'{row["code"]}: {row["name"]} / {row["category"]} / INR {row["price"]}')
            self.stdout.write(self.style.SUCCESS(f"Validated {len(rows)} products; no changes made."))
            return

        created_count = repaired_count = skipped_count = 0
        errors = []
        for row in rows:
            marker = f'Demo SKU: {row["code"]}\n'
            existing = Product.objects.filter(specifications__contains=marker).first()
            if existing and existing.images.exists():
                skipped_count += 1
                continue
            try:
                # Download before creating rows; a failed download leaves no empty listing.
                data = self.download_image(row["image_url"])
                with transaction.atomic():
                    product = existing
                    if product is None:
                        subcategory, _ = SubCategory.objects.get_or_create(
                            category=row["category"], name=row["brand"],
                        )
                        product = Product.objects.create(
                            name=row["name"], category=row["category"], color=row["color"],
                            price=Decimal(row["price"]), subcategory=subcategory,
                            description="Demo catalog listing. Prices and inventory are sample data, not a live offer.",
                            highlights="Real product photo\nSample sizes and inventory",
                            specifications=marker + f'Brand: {row["brand"]}\nCatalog: Demonstration\n',
                        )
                        for size in row["sizes"]:
                            ProductVariant.objects.create(product=product, size=str(size), stock=8)
                    picture = ProductImage(product=product, is_primary=True)
                    # Let ProductImage.save process the in-memory file and upload once.
                    picture.image = ContentFile(data, name=f'{row["code"]}.jpg')
                    picture.save()
                if existing:
                    repaired_count += 1
                else:
                    created_count += 1
                self.stdout.write(f'Imported: {row["name"]}')
            except Exception as error:
                errors.append(row["code"])
                # Storage errors may include credentials; only show the exception type.
                self.stderr.write(f'{row["code"]}: {type(error).__name__}; check image access and media-storage configuration.')
        self.stdout.write(f"Created {created_count}; repaired photos {repaired_count}; skipped {skipped_count}; failed {len(errors)}.")
        if errors:
            raise CommandError("Some products failed. Fix storage/network access and rerun; successful entries are preserved.")
