# Manually written to avoid Django's RemoveIndex failing on MySQL
# when the lat_lon index was already physically removed by RunPython
# migrations 0007/0008/0010 but never recorded in the ORM state.
from django.db import migrations


def remove_lat_lon_index_if_exists(apps, schema_editor):
    """Safely drop lat_lon index — may already be gone on some installs."""
    with schema_editor.connection.cursor() as cursor:
        vendor = schema_editor.connection.vendor
        if vendor == 'mysql':
            cursor.execute("""
                SELECT COUNT(*) FROM information_schema.statistics
                WHERE table_schema = DATABASE()
                AND table_name = 'rmm_devices'
                AND index_name = 'rmm_devices_lat_lon_idx'
            """)
            if cursor.fetchone()[0]:
                cursor.execute(
                    "DROP INDEX `rmm_devices_lat_lon_idx` ON `rmm_devices`"
                )
        elif vendor == 'postgresql':
            cursor.execute("DROP INDEX IF EXISTS rmm_devices_lat_lon_idx")
        elif vendor == 'sqlite':
            cursor.execute("DROP INDEX IF EXISTS rmm_devices_lat_lon_idx")


def rename_ext_obj_map_index(apps, schema_editor):
    """Rename ext_obj_map_conn_idx → external_ob_connect_9c6dfd_idx if needed."""
    with schema_editor.connection.cursor() as cursor:
        vendor = schema_editor.connection.vendor
        if vendor == 'mysql':
            cursor.execute("""
                SELECT COUNT(*) FROM information_schema.statistics
                WHERE table_schema = DATABASE()
                AND table_name = 'external_object_maps'
                AND index_name = 'ext_obj_map_conn_idx'
            """)
            old_exists = cursor.fetchone()[0] > 0
            cursor.execute("""
                SELECT COUNT(*) FROM information_schema.statistics
                WHERE table_schema = DATABASE()
                AND table_name = 'external_object_maps'
                AND index_name = 'external_ob_connect_9c6dfd_idx'
            """)
            new_exists = cursor.fetchone()[0] > 0
            if old_exists and not new_exists:
                cursor.execute("""
                    ALTER TABLE `external_object_maps`
                    RENAME INDEX `ext_obj_map_conn_idx`
                    TO `external_ob_connect_9c6dfd_idx`
                """)
        elif vendor == 'sqlite':
            # SQLite doesn't support RENAME INDEX — just drop the old one;
            # Django will recreate it with the new name on the next DDL op.
            cursor.execute("""
                SELECT COUNT(*) FROM sqlite_master
                WHERE type='index' AND name='ext_obj_map_conn_idx'
            """)
            if cursor.fetchone()[0]:
                cursor.execute("DROP INDEX ext_obj_map_conn_idx")
        # PostgreSQL supports RENAME INDEX natively
        elif vendor == 'postgresql':
            cursor.execute("""
                SELECT COUNT(*) FROM pg_indexes
                WHERE indexname = 'ext_obj_map_conn_idx'
            """)
            if cursor.fetchone()[0]:
                cursor.execute(
                    "ALTER INDEX ext_obj_map_conn_idx "
                    "RENAME TO external_ob_connect_9c6dfd_idx"
                )


class Migration(migrations.Migration):

    dependencies = [
        ('integrations', '0010_add_system_package_scan'),
    ]

    operations = [
        migrations.RunPython(
            remove_lat_lon_index_if_exists,
            migrations.RunPython.noop,
        ),
        migrations.RunPython(
            rename_ext_obj_map_index,
            migrations.RunPython.noop,
        ),
    ]
