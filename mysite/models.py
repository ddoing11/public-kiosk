from django.db import models

class MedicalCertificate(models.Model):
    """진료확인서 (Medical_Certificate 테이블)"""
    GENDER_CHOICES = [('M', '남성'), ('F', '여성')]
    
    patient_name = models.CharField(max_length=100)
    patient_id = models.CharField(max_length=50)
    gender = models.CharField(max_length=1, choices=GENDER_CHOICES)
    birth_date = models.DateField()
    contact = models.CharField(max_length=20)
    address = models.TextField()
    
    # 키오스크용 추가 필드
    requested_at = models.DateTimeField(auto_now_add=True)
    status = models.CharField(max_length=20, choices=[
        ('pending', '대기중'), ('processing', '처리중'), 
        ('completed', '완료'), ('failed', '실패')
    ], default='pending')
    quantity = models.IntegerField(default=1)
    
    class Meta:
        db_table = 'Medical_Certificate'

class Prescription(models.Model):
    """처방전 (prescription 테이블)"""
    GENDER_CHOICES = [('M', '남성'), ('F', '여성')]
    
    patient_name = models.CharField(max_length=100)
    patient_id = models.CharField(max_length=50)
    gender = models.CharField(max_length=1, choices=GENDER_CHOICES)
    birth_date = models.DateField()
    contact = models.CharField(max_length=20)
    prescription_date = models.DateField()
    doctor_name = models.CharField(max_length=100)
    department = models.CharField(max_length=50)
    
    # 키오스크용 추가 필드
    requested_at = models.DateTimeField(auto_now_add=True)
    status = models.CharField(max_length=20, choices=[
        ('pending', '대기중'), ('processing', '처리중'), 
        ('completed', '완료'), ('failed', '실패')
    ], default='pending')
    quantity = models.IntegerField(default=1)
    
    class Meta:
        db_table = 'prescription'

class MedicalReceipt(models.Model):
    """진료비영수증 (medical_receipt 테이블)"""
    GENDER_CHOICES = [('M', '남성'), ('F', '여성')]
    
    patient_name = models.CharField(max_length=100)
    patient_id = models.CharField(max_length=50)
    gender = models.CharField(max_length=1, choices=GENDER_CHOICES)
    birth_date = models.DateField()
    receipt_date = models.DateField()
    
    # 키오스크용 추가 필드
    requested_at = models.DateTimeField(auto_now_add=True)
    status = models.CharField(max_length=20, choices=[
        ('pending', '대기중'), ('processing', '처리중'), 
        ('completed', '완료'), ('failed', '실패')
    ], default='pending')
    quantity = models.IntegerField(default=1)
    
    class Meta:
        db_table = 'medical_receipt'