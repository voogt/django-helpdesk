# Generated by Django 2.2.15 on 2021-09-01 04:17

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('helpdesk', '0035_auto_20210827_0130'),
    ]

    operations = [
        migrations.AddField(
            model_name='ticket',
            name='start_date',
            field=models.DateTimeField(blank=True, null=True, verbose_name='Start date'),
        ),
    ]