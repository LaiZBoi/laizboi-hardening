from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0011_add_tooltips_enabled'),
    ]

    operations = [
        migrations.AddField(
            model_name='userprofile',
            name='time_format',
            field=models.CharField(
                choices=[('12', '12-hour (AM/PM)'), ('24', '24-hour')],
                default='24',
                max_length=2,
            ),
        ),
    ]
