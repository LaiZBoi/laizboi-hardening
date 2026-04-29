# Generated for v3.17.134 — KB permission group fields
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0018_userprofile_notify_assigned_email_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='roletemplate',
            name='kb_view_articles',
            field=models.BooleanField(default=True, help_text='View KB articles (browse the knowledge base)'),
        ),
        migrations.AddField(
            model_name='roletemplate',
            name='kb_edit_articles',
            field=models.BooleanField(default=False, help_text='Create / edit / delete KB articles'),
        ),
        migrations.AddField(
            model_name='roletemplate',
            name='kb_move_articles',
            field=models.BooleanField(default=False, help_text='Move KB articles between categories (bulk move)'),
        ),
        migrations.AddField(
            model_name='roletemplate',
            name='kb_manage_categories',
            field=models.BooleanField(default=False, help_text='Create / edit / delete KB categories (global or org)'),
        ),
        migrations.AddField(
            model_name='roletemplate',
            name='kb_publish_articles',
            field=models.BooleanField(default=False, help_text='Publish/unpublish KB articles (toggle is_published)'),
        ),
    ]
