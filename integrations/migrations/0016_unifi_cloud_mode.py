from django.db import migrations, models


def _add_mode_column_if_missing(apps, schema_editor):
    """Add mode column only if absent — idempotent for servers where it already exists.
    Uses the actual db_table name 'unifi_connections' (not the default Django name).
    """
    # The model uses db_table = 'unifi_connections', not 'integrations_unificonnection'
    TABLE = 'unifi_connections'

    with schema_editor.connection.cursor() as cursor:
        existing_tables = schema_editor.connection.introspection.table_names(cursor)

    if TABLE not in existing_tables:
        # Table doesn't exist yet — it will be created with all current fields by
        # whatever migration first creates it (e.g. 0014_unificonnection).
        # Nothing to do here; the state_operations below update Django's model state.
        return

    with schema_editor.connection.cursor() as cursor:
        col_names = [
            info.name
            for info in schema_editor.connection.introspection.get_table_description(
                cursor, TABLE
            )
        ]
    if 'mode' not in col_names:
        with schema_editor.connection.cursor() as cursor:
            cursor.execute(
                "ALTER TABLE unifi_connections "
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
