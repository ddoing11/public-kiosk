from django.db import models


class Document(models.Model):
    title = models.CharField(max_length=255)
    doc_type = models.CharField(max_length=100)   # 예: "진료확인서/처방전/진료영수증"
    file = models.FileField(upload_to='documents/')
    description = models.TextField(blank=True)

    def __str__(self):
        return f"[{self.doc_type}] {self.title}"



class Medical_Certificate(models.Model):
    """Medical_Certificate 테이블"""
    GENDER_CHOICES = [('M', '남성'), ('F', '여성')]

    patient_name = models.CharField(max_length=100, null=True, blank=True)
    patient_id   = models.CharField(max_length=50,  null=True, blank=True)
    gender       = models.CharField(max_length=1, choices=GENDER_CHOICES, null=True, blank=True)
    birth_date   = models.DateField(null=True, blank=True)
    contact      = models.CharField(max_length=20,  null=True, blank=True)
    address      = models.TextField(null=True, blank=True)

    class Meta:
        db_table  = 'Medical_Certificate'
        managed   = False
        verbose_name = '진료확인서'
        verbose_name_plural = '진료확인서 목록'

    def __str__(self):
        return f"{self.patient_name} ({self.patient_id})"

class Prescription(models.Model):
    """prescription 테이블"""
    GENDER_CHOICES = [('M', '남성'), ('F', '여성')]

    patient_name      = models.CharField(max_length=100, null=True, blank=True)
    patient_id        = models.CharField(max_length=50,  null=True, blank=True)
    gender            = models.CharField(max_length=1, choices=GENDER_CHOICES, null=True, blank=True)
    birth_date        = models.DateField(null=True, blank=True)
    contact           = models.CharField(max_length=20,  null=True, blank=True)
    prescription_date = models.DateField(null=True, blank=True)
    doctor_name       = models.CharField(max_length=100, null=True, blank=True)
    department        = models.CharField(max_length=50,  null=True, blank=True)
    hospital_name     = models.CharField(max_length=100, null=True, blank=True)
    notes             = models.TextField(null=True, blank=True)

    class Meta:
        db_table  = 'prescription'
        managed   = False
        verbose_name = '처방전'
        verbose_name_plural = '처방전 목록'

    def __str__(self):
        return f"{self.patient_name} - {self.prescription_date}"

class MedicalReceipt(models.Model):
    """medical_receipt 테이블"""
    GENDER_CHOICES = [('M', '남성'), ('F', '여성')]

    patient_name = models.CharField(max_length=100, null=True, blank=True)
    patient_id   = models.CharField(max_length=50,  null=True, blank=True)
    gender       = models.CharField(max_length=1, choices=GENDER_CHOICES, null=True, blank=True)
    birth_date   = models.DateField(null=True, blank=True)
    receipt_date = models.DateField(null=True, blank=True)

    class Meta:
        db_table  = 'medical_receipt'
        managed   = False
        verbose_name = '진료영수증'
        verbose_name_plural = '진료영수증 목록'

    def __str__(self):
        return f"{self.patient_name} - {self.receipt_date}"
