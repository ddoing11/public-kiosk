"""
URL configuration for mysite project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.2/topics/http/urls/
"""
from django.contrib import admin
from django.urls import path
from . import views

urlpatterns = [
    path('admin/', admin.site.urls),
    
    # 키오스크 관련 라우팅
    path('kiosk/idle/', views.idle, name='kiosk_idle'),           # 대기화면 (터치만)
    path('kiosk/main/', views.main, name='kiosk_main'),           # 메인화면 (주민번호 + 상담)
    path('kiosk/id-verify/', views.id_verify, name='kiosk_id_verify'),
    path('kiosk/services/', views.services, name='kiosk_services'),
]