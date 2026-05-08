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

서버리스(Vercel)에서는 Playwright 크롤링이 불가능하므로, **크롤러는 EC2에서 Docker로 실행**하고 Vercel UI는 EC2로 요청을 프록시합니다.

```
브라우저 → Vercel(웹 UI) → EC2:8000(/internal/crawl/run) → Neon Postgres
```

#### 1) Neon Postgres 준비
- [neon.tech](https://neon.tech)에서 DB를 만들고 `DATABASE_URL`을 발급받습니다.  
  예: `postgresql://user:pass@ep-xxx.ap-southeast-1.aws.neon.tech/neondb?sslmode=require`

#### 2) EC2 최초 설정 (Amazon Linux 2023 / Ubuntu)

```bash
# 저장소 클론 후 스크립트 실행
curl -fsSL https://raw.githubusercontent.com/JTY9410/jeong/main/scripts/setup-ec2.sh | bash
```

스크립트가 자동으로:
- Docker 설치 및 GHCR 로그인
- `.env` 파일 생성 (DATABASE_URL, SECRET_KEY, CRAWLER_SHARED_SECRET 등 입력 안내)
- `docker-compose.crawler.yml`로 컨테이너 시작

**EC2 보안 그룹**: 인바운드 **TCP 8000** 포트를 허용해야 합니다.

#### 3) EC2 환경변수 목록 (~/jeong-crawler/.env)

| 변수 | 설명 |
|------|------|
| `DATABASE_URL` | Neon Postgres URL |
| `SECRET_KEY` | Vercel과 동일한 고정 값 |
| `CRAWLER_SHARED_SECRET` | Vercel과 동일한 비밀 값 |
| `SCHEDULER_ENABLED` | `true` (자동 크롤 활성화) |
| `CRAWL_TIME` | 자동 크롤 시각 (기본 `07:00`) |
| `INTERNSHIP_INTERVAL_MINUTES` | 인턴 반복 크롤 간격(분, 기본 `120`) |

#### 4) Vercel 환경변수

Vercel 대시보드 → **Settings → Environment Variables** 에서 추가:

| 변수 | 값 |
|------|-----|
| `DATABASE_URL` | Neon URL (UI/검색용) |
| `SECRET_KEY` | EC2와 동일 값 |
| `SCHEDULER_ENABLED` | `false` |
| `CRAWLER_PROXY_URL` | `http://<EC2 퍼블릭 IP>:8000` |
| `CRAWLER_SHARED_SECRET` | EC2와 동일 값 |
| `CRAWLER_REQUEST_TIMEOUT_SEC` | (선택) EC2 응답 대기 초, 기본 250 |

추가 후 **Vercel → Deployments → Redeploy** 또는 `vercel deploy --prod --yes` 실행.

#### 5) EC2 자동 배포 (GitHub Actions)

`.github/workflows/deploy-ec2.yml` 이 포함되어 있습니다.  
GitHub 레포 → **Settings → Secrets and variables → Actions** 에 아래 등록:

| Secret | 값 |
|--------|-----|
| `EC2_HOST` | EC2 퍼블릭 IP 또는 도메인 |
| `EC2_USER` | SSH 계정 (`ec2-user` 또는 `ubuntu`) |
| `EC2_SSH_KEY` | PEM 키 전체 내용 |
| `EC2_DEPLOY_DIR` | 배포 경로 (예: `/home/ec2-user/jeong-crawler`) |

등록 후 `main` 푸시 시 **Docker 이미지 빌드 → EC2 자동 pull & 재시작** 순서로 진행됩니다.

#### 6) 크롤 오류 확인

Vercel 크롤 버튼 클릭 후 브라우저 **Network 탭**에서 `/api/crawl/run` 응답 JSON의 `error` 필드로 원인 파악:

| `error` | 의미 |
|---------|------|
| `crawler_proxy_not_configured` | `CRAWLER_PROXY_URL` / `CRAWLER_SHARED_SECRET` 미설정 |
| `crawler_unreachable` | EC2 접근 불가 (보안 그룹 8000 포트, 도메인 확인) |
| `crawler_timeout` | EC2 크롤이 `CRAWLER_REQUEST_TIMEOUT_SEC` 초 초과 |
| `crawler_http_error` | EC2가 4xx/5xx 반환 → EC2 로그 확인 |

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

