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

### Vercel(웹 UI) + EC2(크롤러) + Neon(Postgres) 권장 배포
서버리스(Vercel/Lambda) 환경에서는 Playwright 크롤링이 불안정/제한이 많아, 웹 UI와 크롤러를 분리하는 구성을 권장합니다.

#### 1) Neon Postgres 준비
- Neon에서 DB를 만들고 `DATABASE_URL`을 발급받습니다.

#### 2) EC2(크롤러 서버) 환경변수
EC2에서는 Docker로 앱을 띄우고 스케줄러/크롤링을 수행합니다.

- `DATABASE_URL`: Neon에서 받은 값
- `SCHEDULER_ENABLED`: `"true"`
- `CRAWLER_SHARED_SECRET`: 임의의 긴 문자열(예: 32+ chars)

EC2에서는 내부 실행 API가 열립니다:
- `POST /internal/crawl/run` (헤더 `X-CRAWL-SECRET: <CRAWLER_SHARED_SECRET>` 필요)

#### 3) Vercel(웹 UI) 환경변수
Vercel에서는 크롤링 버튼을 EC2로 프록시합니다.

- `DATABASE_URL`: Neon에서 받은 값 (UI/검색용)
- `SCHEDULER_ENABLED`: `"false"`
- `CRAWLER_PROXY_URL`: 예) `https://crawler.example.com` (EC2의 공개 주소)
- `CRAWLER_SHARED_SECRET`: EC2와 동일 값
- (선택) `CRAWLER_REQUEST_TIMEOUT_SEC`: Vercel 함수가 EC2 응답을 기다리는 최대 초(기본 250). 플랜 한도가 더 짧으면 이 값을 **함수 maxDuration보다 10~20초 작게** 맞추세요.

`vercel.json`의 `functions["api/index.py"].maxDuration`(기본 300초)은 **요금제/프로젝트 설정**에 따라 실제 한도가 더 짧을 수 있습니다. 크롤이 자주 끊기면 Vercel **Runtime Logs**에서 `vercel crawl proxy` 로그와 응답 JSON의 `error` 필드를 확인하세요.

### 개발 실행 (로컬)
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium
export FLASK_APP=run.py
flask run --host=0.0.0.0 --port=8000
```

### 크롤링 의존성(Playwright) 설치
Vercel/Lambda 같은 서버리스 배포 번들을 줄이기 위해, Playwright는 분리되어 있습니다.

```bash
pip install -r requirements-crawl.txt
python -m playwright install chromium
```

### (선택) ML/XAI 의존성 설치
요구사항의 ML/XAI(Scikit-learn/LightGBM/XGBoost/SHAP)는 용량이 커서 서버리스(Lambda 등) 배포 번들을 크게 만들 수 있습니다.
필요할 때만 아래를 추가로 설치하세요.

```bash
pip install -r requirements-ml.txt
```

