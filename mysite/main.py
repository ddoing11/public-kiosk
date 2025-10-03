#!/usr/bin/env python3
import os
import sys
import django

# Django 설정
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "mysite.settings")
django.setup()

from hospital_kiosk.websocket_server import start_hospital_server

def main():
    print("병원 서류 발급 키오스크 시스템")
    print("WebSocket 서버 시작 중...")
    
    try:
        start_hospital_server()
    except KeyboardInterrupt:
        print("\n서버를 종료합니다.")

if __name__ == "__main__":
    main()