from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("payments", "0001_initial")]
    operations = [
        migrations.AddField(
            model_name="payment", name="checkout_snapshot",
            field=models.JSONField(default=dict, blank=True),
        ),
    ]
