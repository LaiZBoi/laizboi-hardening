from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('vehicles', '0006_add_vehicle_receipts'),
    ]

    operations = [
        migrations.AddField(
            model_name='vehiclereceipt',
            name='image_hash',
            field=models.CharField(blank=True, db_index=True, max_length=64),
        ),
    ]
