from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("identity", "0005_tourprogress"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="totp_secret_enc",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="user",
            name="totp_last_step",
            field=models.BigIntegerField(default=0),
        ),
    ]
