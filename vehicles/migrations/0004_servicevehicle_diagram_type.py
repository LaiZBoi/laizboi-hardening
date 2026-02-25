from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('vehicles', '0003_add_service_schedules_alerts_providers'),
    ]

    operations = [
        migrations.AddField(
            model_name='servicevehicle',
            name='diagram_type',
            field=models.CharField(
                choices=[
                    ('cargo_van', 'Cargo Van (Transit / Sprinter style)'),
                    ('pickup_truck', 'Pickup Truck (F-150 / Ram style)'),
                ],
                default='cargo_van',
                help_text='Vehicle silhouette used for damage diagrams',
                max_length=30,
            ),
        ),
    ]
