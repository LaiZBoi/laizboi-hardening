"""
Repair migration: add unifi_connections.mode if it was missed by 0016_unifi_cloud_mode.

0016_unifi_cloud_mode used the wrong table name ('integrations_unificonnection'
instead of the actual db_table 'unifi_connections'), so on fresh installs the
RunPython function returned early and the column was never added.  This migration
is idempotent — it is a no-op on servers where the column already exists.
"""
from django.db import migrations


def _ensure_mode_column(apps, schema_editor):
    TABLE = 'unifi_connections'
    with schema_editor.connection.cursor() as cursor:
        existing_tables = schema_editor.connection.introspection.table_names(cursor)

    if TABLE not in existing_tables:
        return  # Table doesn't exist yet; will be created correctly by 0014.

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
        ('integrations', '0020_unificonnection_site_org_map'),
    ]

    operations = [
        migrations.RunPython(_ensure_mode_column, migrations.RunPython.noop),
    ]
