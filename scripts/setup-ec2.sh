#!/usr/bin/env bash
# EC2(Amazon Linux 2023 / Ubuntu 22.04) 크롤러 서버 최초 설정 스크립트
# 실행: bash setup-ec2.sh
set -euo pipefail

echo "=== [1/6] 패키지 업데이트 ==="
if command -v dnf &>/dev/null; then
    sudo dnf update -y
    sudo dnf install -y docker git curl
elif command -v apt-get &>/dev/null; then
    sudo apt-get update -y
    sudo apt-get install -y docker.io git curl
else
    echo "지원하지 않는 패키지 매니저입니다. Docker를 수동 설치하세요."; exit 1
fi

echo "=== [2/6] Docker 서비스 시작 ==="
sudo systemctl enable --now docker
sudo usermod -aG docker "$USER"

echo "=== [3/6] Docker Compose 플러그인 설치 ==="
COMPOSE_VERSION="v2.27.0"
sudo mkdir -p /usr/local/lib/docker/cli-plugins
sudo curl -SL "https://github.com/docker/compose/releases/download/${COMPOSE_VERSION}/docker-compose-linux-$(uname -m)" \
    -o /usr/local/lib/docker/cli-plugins/docker-compose
sudo chmod +x /usr/local/lib/docker/cli-plugins/docker-compose
docker compose version

echo "=== [4/6] GHCR 로그인 ==="
echo "GitHub PAT (read:packages 권한)를 입력하세요:"
read -rs GHCR_TOKEN
echo "${GHCR_TOKEN}" | docker login ghcr.io -u jty9410 --password-stdin
unset GHCR_TOKEN

echo "=== [5/6] .env 파일 생성 ==="
ENV_FILE="$HOME/jeong-crawler/.env"
mkdir -p "$HOME/jeong-crawler"

if [[ ! -f "$ENV_FILE" ]]; then
    echo "환경변수를 입력하세요."
    read -rp "DATABASE_URL (Neon Postgres URL): " DB_URL
    read -rp "SECRET_KEY (Vercel과 동일): " SK
    read -rp "CRAWLER_SHARED_SECRET (Vercel과 동일): " CSS
    read -rp "DEFAULT_USERNAME [jsy1004]: " DU; DU="${DU:-jsy1004}"
    read -rp "DEFAULT_PASSWORD [jsy0701]: " DP; DP="${DP:-jsy0701}"
    read -rp "CRAWL_TIME (크롤 시각, 기본 07:00): " CT; CT="${CT:-07:00}"

    cat > "$ENV_FILE" <<EOF
DATABASE_URL=${DB_URL}
SECRET_KEY=${SK}
CRAWLER_SHARED_SECRET=${CSS}
DEFAULT_USERNAME=${DU}
DEFAULT_PASSWORD=${DP}
CRAWL_TIME=${CT}
INTERNSHIP_INTERVAL_MINUTES=120
EOF
    chmod 600 "$ENV_FILE"
    echo ".env 파일 생성 완료: $ENV_FILE"
else
    echo ".env 파일이 이미 존재합니다: $ENV_FILE"
fi

echo "=== [6/6] docker-compose.crawler.yml 복사 및 컨테이너 시작 ==="
REPO_URL="https://raw.githubusercontent.com/JTY9410/jeong/main/docker-compose.crawler.yml"
curl -fsSL "$REPO_URL" -o "$HOME/jeong-crawler/docker-compose.crawler.yml"

cd "$HOME/jeong-crawler"
docker compose -f docker-compose.crawler.yml pull
docker compose -f docker-compose.crawler.yml up -d

echo ""
echo "=== 설정 완료 ==="
echo "컨테이너 상태: docker compose -f ~/jeong-crawler/docker-compose.crawler.yml ps"
echo "로그 확인:     docker compose -f ~/jeong-crawler/docker-compose.crawler.yml logs -f"
echo ""
echo "Vercel에 아래 환경변수를 추가하세요:"
echo "  CRAWLER_PROXY_URL    = http://<이 EC2의 퍼블릭 IP>:8000"
echo "  CRAWLER_SHARED_SECRET = (위에서 입력한 값)"
