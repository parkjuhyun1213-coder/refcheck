#!/usr/bin/env bash
# ============================================================
# 참고문헌 표준화·검증 에이전트 — 서버 초기 설치 스크립트
# 대상: Ubuntu 22.04 / 24.04 (AWS Lightsail 등)
#
# 사용법:
#   1) 프로젝트 폴더를 /home/ubuntu/writing_reference 로 업로드해 둔 상태에서
#   2) sudo bash ~/writing_reference/deploy/setup_server.sh 도메인
#      예: sudo bash ~/writing_reference/deploy/setup_server.sh refcheck.kr
# ============================================================
set -euo pipefail

DOMAIN="${1:?사용법: sudo bash setup_server.sh <도메인>  (예: refcheck.kr)}"
APP_DIR=/opt/refstd

# 업로드 위치 자동 탐색 (AWS: /home/ubuntu, 가비아 등 root 접속: /root)
SRC="${2:-}"
if [ -z "$SRC" ]; then
  for cand in /root/writing_reference /home/ubuntu/writing_reference; do
    if [ -f "$cand/app/main.py" ]; then SRC="$cand"; break; fi
  done
fi
if [ -z "$SRC" ] || [ ! -f "$SRC/app/main.py" ]; then
  echo "오류: 업로드된 프로젝트를 찾을 수 없습니다. WinSCP로 writing_reference 폴더를"
  echo "      /root 또는 /home/ubuntu 아래에 업로드했는지 확인하세요."
  exit 1
fi
echo "프로젝트 위치: $SRC"

echo "== 1/6 시스템 패키지 설치 =="
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-pip curl unzip \
  debian-keyring debian-archive-keyring apt-transport-https

echo "== 2/6 Caddy 웹서버 설치 (HTTPS 자동 발급) =="
if ! command -v caddy >/dev/null 2>&1; then
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
    | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
    | tee /etc/apt/sources.list.d/caddy-stable.list >/dev/null
  apt-get update -qq && apt-get install -y -qq caddy
fi

echo "== 3/6 스왑 메모리 확보 (저사양 인스턴스 안정화) =="
if [ ! -f /swapfile ]; then
  fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
  echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

echo "== 4/6 애플리케이션 설치 ($APP_DIR) =="
mkdir -p "$APP_DIR"
cp -r "$SRC/." "$APP_DIR/"
# 윈도우 줄바꿈(CRLF) 제거
find "$APP_DIR/deploy" -type f -exec sed -i 's/\r$//' {} \;
sed -i 's/\r$//' "$APP_DIR/.env" 2>/dev/null || true
cd "$APP_DIR"
python3 -m venv venv
./venv/bin/pip install --quiet --upgrade pip
./venv/bin/pip install --quiet -r requirements.txt
chown -R www-data:www-data "$APP_DIR"

echo "== 5/6 서비스 등록 (부팅 시 자동 시작) =="
cp "$APP_DIR/deploy/refstd.service" /etc/systemd/system/refstd.service
systemctl daemon-reload
systemctl enable --now refstd
sleep 2
systemctl --no-pager --lines=5 status refstd || true

echo "== 6/6 Caddy 설정 (도메인: $DOMAIN) =="
sed "s/{{DOMAIN}}/$DOMAIN/g" "$APP_DIR/deploy/Caddyfile" > /etc/caddy/Caddyfile
systemctl restart caddy

echo ""
echo "============================================================"
echo "설치 완료!  브라우저에서  https://$DOMAIN  으로 접속해 확인하세요."
echo "(도메인 DNS 설정 직후라면 전파까지 몇 분~몇 시간 걸릴 수 있습니다)"
echo ""
echo "확인 명령:"
echo "  sudo systemctl status refstd    # 앱 상태"
echo "  sudo journalctl -u refstd -f    # 앱 로그 실시간 보기"
echo "  sudo systemctl restart refstd   # 앱 재시작"
echo "============================================================"
