"""
Add CSV import source type with field mapper support.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('imports', '0005_add_rollback_functionality'),
    ]

    operations = [
        migrations.AddField(
            model_name='importjob',
            name='csv_target_model',
            field=models.CharField(
                blank=True,
                choices=[
                    ('asset', 'Assets'),
                    ('password', 'Passwords (Vault)'),
                    ('contact', 'Contacts'),
                    ('document', 'Documents'),
                ],
                max_length=20,
                help_text='What to import CSV rows as (CSV imports only)',
            ),
        ),
        migrations.AddField(
            model_name='importjob',
            name='field_mappings',
            field=models.JSONField(
                blank=True,
                default=dict,
                help_text='Maps source column headers to target model field names',
            ),
        ),
    ]
