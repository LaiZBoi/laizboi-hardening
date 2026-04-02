from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('vehicles', '0005_add_shop_inventory'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='VehicleReceipt',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('receipt_date', models.DateField()),
                ('vendor', models.CharField(blank=True, max_length=255)),
                ('category', models.CharField(
                    choices=[
                        ('fuel', 'Fuel'), ('maintenance', 'Maintenance'),
                        ('repair', 'Repair / Parts'), ('insurance', 'Insurance'),
                        ('registration', 'Registration / Licensing'),
                        ('toll', 'Tolls / Parking'), ('cleaning', 'Cleaning / Detailing'),
                        ('inspection', 'Inspection'), ('other', 'Other'),
                    ],
                    default='other', max_length=50,
                )),
                ('amount', models.DecimalField(decimal_places=2, max_digits=10)),
                ('tax_amount', models.DecimalField(blank=True, decimal_places=2, max_digits=8, null=True)),
                ('odometer', models.IntegerField(blank=True, null=True)),
                ('description', models.TextField(blank=True)),
                ('notes', models.TextField(blank=True)),
                ('ai_processed', models.BooleanField(default=False)),
                ('ai_confidence', models.CharField(
                    blank=True,
                    choices=[('low', 'Low'), ('medium', 'Medium'), ('high', 'High')],
                    max_length=10,
                )),
                ('vehicle', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='receipts', to='vehicles.servicevehicle',
                )),
                ('created_by', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='vehicle_receipts_created',
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={'db_table': 'vehicle_receipts', 'ordering': ['-receipt_date']},
        ),
    ]
