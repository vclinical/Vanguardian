from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("reconcile", "0001_initial")]

    operations = [
        migrations.AlterField(
            model_name="reconcilevalidationrun",
            name="source",
            field=models.CharField(
                max_length=32,
                choices=[
                    ("FILE_IMPORT", "File import"),
                    ("SUBMIT_FOR_REVIEW", "Submit for review"),
                    ("VALIDATION_ISSUE_ACKNOWLEDGEMENT", "Validation issue acknowledgement"),
                ],
            ),
        ),
    ]
