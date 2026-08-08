#!/usr/bin/env bash
# ============================================================
# 앱 업데이트 — 업로드한 새 버전을 서비스 폴더에 반영하고 재시작
#
# 사용법:
#   1) WinSCP로 writing_reference 폴더 전체를 /root 에 덮어쓰기(업로드)
#   2) 터미널에서:  bash /root/writing_reference/deploy/update.sh
#
# 코드 파일(app/·deploy/)만 갱신하며, 운영 데이터(.env·config.json·history/·
# uploads/·styles/·*.json 로그)는 건드리지 않습니다.
# ============================================================
set -euo pipefail

SRC="${1:-/root/writing_reference}"
APP_DIR=/opt/refstd

if [ ! -f "$SRC/app/main.py" ]; then
  echo "오류: $SRC 에서 새 버전을 찾을 수 없습니다. WinSCP 업로드를 먼저 확인하세요."
  exit 1
fi
if [ ! -d "$APP_DIR" ]; then
  echo "오류: $APP_DIR 가 없습니다. 최초 설치는 setup_server.sh 를 사용하세요."
  exit 1
fi

echo "== 코드 파일 반영 =="
# 파이썬 소스와 화면 파일만 복사(운영 데이터 보존)
find "$SRC/app" -maxdepth 1 -name "*.py" -exec cp -f {} "$APP_DIR/app/" \;
mkdir -p "$APP_DIR/app/static"
cp -f "$SRC/app/static/index.html" "$APP_DIR/app/static/index.html"
mkdir -p "$APP_DIR/deploy"
cp -f "$SRC"/deploy/* "$APP_DIR/deploy/" 2>/dev/null || true
find "$APP_DIR/deploy" -type f -name "*.sh" -exec sed -i 's/\r$//' {} \;
# 시드 제안 파일은 서버에 없을 때만(운영 중 축적분 보호)
if [ ! -f "$APP_DIR/app/suggestions.json" ] && [ -f "$SRC/app/suggestions.json" ]; then
  cp -f "$SRC/app/suggestions.json" "$APP_DIR/app/suggestions.json"
fi
if [ -f "$SRC/requirements.txt" ]; then
  cp -f "$SRC/requirements.txt" "$APP_DIR/requirements.txt"
  "$APP_DIR/venv/bin/pip" install --quiet -r "$APP_DIR/requirements.txt" || true
fi
chown -R www-data:www-data "$APP_DIR/app" "$APP_DIR/deploy" 2>/dev/null || true

echo "== 서비스 재시작 =="
systemctl restart refstd
sleep 2
if systemctl is-active --quiet refstd; then
  echo "정상 실행 중 — 버전: $(grep -m1 APP_VERSION "$APP_DIR/app/main.py" | cut -d'"' -f2)"
  echo "브라우저에서 Ctrl+F5 로 새로고침한 뒤 확인하세요."
else
  echo "⚠ 앱이 시작되지 않았습니다. 아래 로그를 확인하세요:"
  journalctl -u refstd -n 20 --no-pager
  exit 1
fi
