## 채용 정보 수집기 (Flask Web)

`요구사항.md`를 기반으로 **인문계열 공개채용/인턴 공고를 자동 수집**하고, **신규 데이터 발생 시 웹 UI에서 알림**을 제공하는 Flask 웹앱입니다.

### 주요 기능
- 로그인 기반 접근 (초기 계정 자동 생성)
- 수동/자동 크롤링 (APScheduler)
- 수집 데이터 저장 (SQLite 기본, PostgreSQL 운영 전환 가능)
- 대시보드(Chart.js) + 목록/검색
- 신규 항목 웹 알림(AJAX 폴링)

### 기술 스택
- Backend: Python 3.11, Flask 3.x, SQLAlchemy
- DB: SQLite(기본) → PostgreSQL(선택)
- Scraping: Playwright + playwright-stealth
- Security: Flask-WTF(CSRF), Flask-Limiter, bcrypt
- Scheduler: APScheduler
- Frontend: Bootstrap 5.3, Chart.js 4.x, AJAX
- Deploy: Docker, Docker Compose

### 빠른 시작 (Docker Compose)
1) 빌드/실행

```bash
docker compose up --build
```

2) 접속
- `http://localhost:8100`

3) 초기 로그인
- **ID**: `jsy1004`
- **PW**: `jsy0701`

### 개발 실행 (로컬)
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium
export FLASK_APP=run.py
flask run --host=0.0.0.0 --port=8000
```

### (선택) ML/XAI 의존성 설치
요구사항의 ML/XAI(Scikit-learn/LightGBM/XGBoost/SHAP)는 용량이 커서 서버리스(Lambda 등) 배포 번들을 크게 만들 수 있습니다.
필요할 때만 아래를 추가로 설치하세요.

```bash
pip install -r requirements-ml.txt
```

