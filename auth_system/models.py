from django.db import models

# Create your models here.
class PatientList(models.Model):
    patient_name = models.CharField(max_length=100)
    patient_id = models.CharField(max_length=20, unique=True)
    gender = models.CharField(max_length=10)
    birth_date = models.DateField()
    contact = models.CharField(max_length=20)

    class Meta:
        db_table = "PatientList"

    def __str__(self):
        return f"{self.patient_name} ({self.patient_id})"