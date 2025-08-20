"""
URL configuration for mysite project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.2/topics/http/urls/
"""
from django.contrib import admin
from django.urls import path, include
from django.conf import settings 
from django.conf.urls.static import static 
from . import views
from .views import speech_token

urlpatterns = [
    path('admin/', admin.site.urls),
    path('speech/token/', views.speech_token, name='speech_token'),
    
    # 키오스크 관련 라우팅
    path('kiosk/idle/', views.idle, name='kiosk_idle'),
    path('kiosk/main/', views.main, name='kiosk_main'),         # <-- 주민번호 1차 검색은 이 URL을 계속 사용합니다.
    path('kiosk/id-verify/', views.id_verify, name='kiosk_id_verify'), # 이 URL은 이제 사용되지 않지만, 다른 기능과의 호환성을 위해 남겨둡니다.
    path('kiosk/services/', views.services, name='kiosk_services'),

    path('kiosk/search_by_name/', views.search_by_name_view, name='kiosk_search_by_name'),
]


if settings.DEBUG:
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATICFILES_DIRS[0])