#!/usr/bin/env bash
# ============================================================
# 앱 업데이트 — 업로드한 새 버전을 서비스 폴더에 반영하고 재시작
#
# 사용법:
#   1) WinSCP로 writing_reference_agent 폴더 전체를 /root 에 덮어쓰기(업로드)
#   2) 터미널에서:  bash /root/writing_reference_agent/deploy/update.sh
#
# 코드 파일(app/·deploy/)만 갱신하며, 운영 데이터(.env·config.json·history/·
# uploads/·styles/·*.json 로그)는 건드리지 않습니다.
# ============================================================
set -euo pipefail

SRC="${1:-/root/writing_reference_agent}"
APP_DIR=/opt/refstd

if [ ! -f "$SRC/app/main.py" ]; then
  echo "오류: $SRC 에서 새 버전을 찾을 수 없습니다. WinSCP 업로드를 먼저 확인하세요."
  exit 1
fi
if [ ! -d "$APP_DIR" ]; then
  echo "오류: $APP_DIR 가 없습니다. 최초 설치는 setup_server.sh 를 사용하세요."
  exit 1
fi

# 재시작하면 처리 중이던 작업이 통째로 사라진다(작업 상태가 메모리에만 있음).
# 편집위원이 논문을 돌리는 중에 배포하지 않도록 확인하고 미룬다.
BUSY=$(curl -s --max-time 3 http://127.0.0.1:8765/api/busy | grep -o '"running": *[0-9]*' | grep -o '[0-9]*' || true)
if [ "${FORCE:-0}" != "1" ] && [ -n "${BUSY:-}" ] && [ "$BUSY" -gt 0 ]; then
  echo "⚠ 지금 처리 중인 작업이 ${BUSY}건 있습니다. 재시작하면 그 작업이 사라집니다."
  echo "  작업이 끝난 뒤 다시 실행하시거나, 그래도 진행하려면:  FORCE=1 bash $0"
  exit 1
fi

echo "== 코드 파일 반영 =="
# 파이썬 소스와 화면 파일만 복사(운영 데이터 보존)
find "$SRC/app" -maxdepth 1 -name "*.py" -exec cp -f {} "$APP_DIR/app/" \;
mkdir -p "$APP_DIR/app/static"
# index.html·guide.html 등 화면 파일 전부 (예전에는 index.html만 복사해 guide.html이 반영되지 않았다)
# PDF는 공통기준 원문 배포용(/guide/standard.pdf)
find "$SRC/app/static" -maxdepth 1 \( -name "*.html" -o -name "*.pdf" \) -exec cp -f {} "$APP_DIR/app/static/" \;
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

# 서비스 정의가 바뀌었으면 systemd에 반영(예: --proxy-headers 추가)
if ! cmp -s "$APP_DIR/deploy/refstd.service" /etc/systemd/system/refstd.service; then
  cp -f "$APP_DIR/deploy/refstd.service" /etc/systemd/system/refstd.service
  systemctl daemon-reload
  echo "== 서비스 정의 갱신(daemon-reload) =="
fi

# 파일 복사·pip 사이에 새 작업이 시작됐을 수 있다 — 재시작 직전에 한 번 더 확인
BUSY=$(curl -s --max-time 3 http://127.0.0.1:8765/api/busy | grep -o '"running": *[0-9]*' | grep -o '[0-9]*' || true)
if [ "${FORCE:-0}" != "1" ] && [ -n "${BUSY:-}" ] && [ "$BUSY" -gt 0 ]; then
  echo "⚠ 코드 복사 중에 새 작업이 ${BUSY}건 시작되었습니다. 재시작을 중단합니다."
  echo "  (코드는 이미 복사되었으므로, 작업이 끝난 뒤  systemctl restart refstd  만 실행하면 됩니다)"
  exit 1
fi

echo "== 서비스 재시작 =="
systemctl restart refstd
sleep 2
if systemctl is-active --quiet refstd; then
  # 줄 첫머리의 APP_VERSION만 — 그냥 APP_VERSION으로 찾으면 위쪽 주석줄이 먼저 걸린다
  echo "정상 실행 중 — 버전: $(grep -m1 '^APP_VERSION' "$APP_DIR/app/main.py" | cut -d'"' -f2)"
  echo "브라우저에서 Ctrl+F5 로 새로고침한 뒤 확인하세요."
else
  echo "⚠ 앱이 시작되지 않았습니다. 아래 로그를 확인하세요:"
  journalctl -u refstd -n 20 --no-pager
  exit 1
fi
