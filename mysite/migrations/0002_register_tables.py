# Register existing MySQL tables without touching DB
from django.db import migrations, models

class Migration(migrations.Migration):
    dependencies = [
        ('mysite', '0001_initial'),  # 앱 라벨을 mysite로!
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[],      # DB에는 아무 작업도 안 함
            state_operations=[
                migrations.CreateModel(
                    name='Medical_Certificate',
                    fields=[
                        ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                        ('patient_name', models.CharField(max_length=100, verbose_name='환자 이름')),
                        ('patient_id', models.CharField(max_length=50, verbose_name='환자 식별 번호')),
                        ('gender', models.CharField(choices=[('M', '남성'), ('F', '여성')], max_length=1, verbose_name='성별')),
                        ('birth_date', models.DateField(verbose_name='생년월일')),
                        ('contact', models.CharField(max_length=20, verbose_name='연락처')),
                        ('address', models.TextField(blank=True, null=True, verbose_name='주소')),
                    ],
                    options={'db_table': 'Medical_Certificate',
                             'verbose_name': '진료확인서',
                             'verbose_name_plural': '진료확인서 목록'},
                ),
                migrations.CreateModel(
                    name='MedicalReceipt',
                    fields=[
                        ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                        ('patient_name', models.CharField(max_length=100, verbose_name='환자 이름')),
                        ('patient_id', models.CharField(max_length=50, verbose_name='환자 식별 번호')),
                        ('gender', models.CharField(choices=[('M', '남성'), ('F', '여성')], max_length=1, verbose_name='성별')),
                        ('birth_date', models.DateField(verbose_name='생년월일')),
                        ('receipt_date', models.DateField(verbose_name='영수증 발행일')),
                    ],
                    options={'db_table': 'medical_receipt',
                             'verbose_name': '진료영수증',
                             'verbose_name_plural': '진료영수증 목록'},
                ),
                migrations.CreateModel(
                    name='Prescription',
                    fields=[
                        ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                        ('patient_name', models.CharField(max_length=100, verbose_name='환자 이름')),
                        ('patient_id', models.CharField(max_length=50, verbose_name='환자 식별 번호')),
                        ('gender', models.CharField(choices=[('M', '남성'), ('F', '여성')], max_length=1, verbose_name='성별')),
                        ('birth_date', models.DateField(verbose_name='생년월일')),
                        ('contact', models.CharField(max_length=20, verbose_name='연락처')),
                        ('prescription_date', models.DateField(verbose_name='처방전 발행일')),
                        ('doctor_name', models.CharField(max_length=100, verbose_name='의사 이름')),
                        ('department', models.CharField(max_length=50, verbose_name='진료과')),
                        ('hospital_name', models.CharField(max_length=100, verbose_name='병원명')),
                        ('notes', models.TextField(blank=True, null=True, verbose_name='특이사항')),
                    ],
                    options={'db_table': 'prescription',
                             'verbose_name': '처방전',
                             'verbose_name_plural': '처방전 목록'},
                ),
            ],
        ),
    ]
