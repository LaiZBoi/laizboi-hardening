# State-only migration: DB changes were already applied by 0012 RunPython functions.
# SeparateDatabaseAndState updates Django's migration state without touching the DB.

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('integrations', '0012_remove_rmmdevice_rmm_devices_lat_lon_idx_and_more'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.RemoveIndex(
                    model_name='rmmdevice',
                    name='rmm_devices_lat_lon_idx',
                ),
                migrations.RenameIndex(
                    model_name='externalobjectmap',
                    new_name='external_ob_connect_9c6dfd_idx',
                    old_name='ext_obj_map_conn_idx',
                ),
            ],
            database_operations=[],
        ),
    ]
