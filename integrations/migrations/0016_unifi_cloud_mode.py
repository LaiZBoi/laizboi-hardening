from django.db import migrations, models


def _add_mode_column_if_missing(apps, schema_editor):
    """Add mode column only if absent — idempotent for servers where it already exists.
    No-op on fresh installs where the table doesn't exist yet (Django creates it
    with the correct schema from the ORM state).
    """
    with schema_editor.connection.cursor() as cursor:
        existing_tables = schema_editor.connection.introspection.table_names(cursor)

    if 'integrations_unificonnection' not in existing_tables:
        # Fresh install — table will be created with the column included; nothing to do.
        return

    with schema_editor.connection.cursor() as cursor:
        col_names = [
            info.name
            for info in schema_editor.connection.introspection.get_table_description(
                cursor, 'integrations_unificonnection'
            )
        ]
    if 'mode' not in col_names:
        with schema_editor.connection.cursor() as cursor:
            cursor.execute(
                "ALTER TABLE integrations_unificonnection "
                "ADD COLUMN mode varchar(20) NOT NULL DEFAULT 'self_hosted'"
            )


class Migration(migrations.Migration):

    dependencies = [
        ('integrations', '0015_m365connection'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunPython(_add_mode_column_if_missing, migrations.RunPython.noop),
            ],
            state_operations=[
                migrations.AddField(
                    model_name='unificonnection',
                    name='mode',
                    field=models.CharField(
                        choices=[
                            ('self_hosted', 'Self-hosted (local controller)'),
                            ('cloud', 'Cloud (UniFi Site Manager / ui.com)'),
                        ],
                        default='self_hosted',
                        help_text='Self-hosted controller or UniFi Site Manager cloud API',
                        max_length=20,
                    ),
                ),
            ],
        ),
        migrations.AlterField(
            model_name='unificonnection',
            name='host',
            field=models.URLField(
                blank=True,
                help_text='UniFi controller URL (self-hosted only), e.g. https://192.168.1.1',
                max_length=500,
            ),
        ),
    ]
