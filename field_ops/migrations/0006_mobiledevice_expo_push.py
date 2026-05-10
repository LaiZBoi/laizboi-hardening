from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('field_ops', '0005_orgfopssettings_geofencevisit'),
    ]

    operations = [
        migrations.AddField(
            model_name='mobiledevice',
            name='expo_push_token',
            field=models.CharField(blank=True, default='', max_length=200),
        ),
        migrations.AddField(
            model_name='mobiledevice',
            name='notifications_enabled',
            field=models.BooleanField(default=True),
        ),
    ]
