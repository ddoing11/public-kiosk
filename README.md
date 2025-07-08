## 🛠️ 설치 및 실행 방법
프로젝트 시작할 때 (최초 1회만)
```bash
# 1. 저장소 클론
git clone https://github.com/ddoing11/public-kiosk.git
cd public-kiosk

# 2. 가상환경 생성
python -m venv venv

# 3. 패키지 설치
pip install -r requirements.txt
`\`\`\``


## 매일 작업할 때
```bash
# 1. 가상환경 활성화
venv\Scripts\activate  # Windows
# source venv/bin/activate  # Mac/Linux

# 2. 서버 실행 (개발하면서)
python manage.py runserver

# 3. 작업 완료 후 Git 업로드
git add .
git commit -m "커밋 메시지"
git push origin main
`\`\`\``

## 추가 유용한 명령어
```bash
# 다른 사람의 변경사항 가져오기
git pull origin main

# 서버 중지
Ctrl + C
`\`\`\``
