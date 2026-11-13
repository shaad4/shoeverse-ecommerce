from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("payments", "0002_payment_checkout_snapshot"),
        ("shop", "0015_alter_orderitem_status"),
    ]
    operations = [
        migrations.AddField(
            model_name="payment", name="order",
            field=models.OneToOneField(
                to="shop.order", on_delete=django.db.models.deletion.PROTECT,
                null=True, blank=True, related_name="payment",
            ),
        ),
    ]
