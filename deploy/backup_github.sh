#!/usr/bin/env bash
# ============================================================
# 서비스 데이터 자동 백업
#
# 두 갈래로 나눈다 — 학회에 '보관 자료는 외부에 제공하지 않는다'고 약속했으므로
# 미공개 원고 원본(uploads/)은 GitHub로 보내지 않는다.
#   1) GitHub 비공개 저장소: 처리 이력(history/), 사용자 기준(styles/),
#      제안·기준·통계 JSON, 이력 아카이브 CSV,
#      3단 비교 누적 코퍼스(compare_corpus.jsonl — 소급 생성 불가, 최우선 보존)
#   2) 서버 내 로컬 미러(/opt/refstd-local-backup): 원고·발행본 파일(uploads/)
#      — 실수 삭제·덮어쓰기 대비용. 서버 디스크가 통째로 죽으면 원고는 복구
#      불가함을 감수하는 정책이다(2026-08-17 결정).
# 제외 대상: .env, config.json (API 키·비밀번호 — 어디에도 올리지 않음)
#
# 최초 설치 방법은 배포가이드.md의 '자동 백업' 절 참조.
# cron 등록 예: 매일 새벽 4시
#   0 4 * * * bash /opt/refstd/deploy/backup_github.sh >> /var/log/refstd-backup.log 2>&1
# ============================================================
set -euo pipefail

SRC=/opt/refstd/app
DST=/opt/refstd-backup         # GitHub 비공개 저장소를 클론해 둔 위치
LOCAL=/opt/refstd-local-backup # 원고 원본의 서버 내 미러(외부 전송 없음)

if ! command -v rsync >/dev/null 2>&1; then
  echo "오류: rsync가 설치되어 있지 않습니다.  apt-get install -y rsync  후 다시 실행하세요."
  exit 1
fi

# --- 1) 원고 원본 로컬 미러 (GitHub 설치 여부와 무관하게 항상 수행) ---
if [ -d "$SRC/uploads" ]; then
  mkdir -p "$LOCAL/uploads"
  # --delete: 위원장이 화면에서 삭제한 자료는 미러에서도 지운다(삭제 약속 준수)
  rsync -a --delete "$SRC/uploads/" "$LOCAL/uploads/"
  echo "$(date '+%F %T') 원고 로컬 미러 완료 ($LOCAL/uploads)"
fi

# --- 2) GitHub 백업 (원고 제외) ---
if [ ! -d "$DST/.git" ]; then
  echo "안내: $DST 에 백업 저장소가 없어 GitHub 백업은 건너뜁니다. 설치는 배포가이드 참조."
  exit 0
fi

# 안전장치: 저장소가 실수로 Public이면 중단한다.
# 비공개 저장소는 로그인 없이 접근하면 404가 온다 — 최종 응답이 200이면 공개 상태.
# -L: 저장소 이름 변경·이관 시 GitHub이 301로 새 주소를 알려주므로 끝까지 따라가 판정한다.
ORIGIN=$(git -C "$DST" remote get-url origin)
WEB=$(echo "$ORIGIN" | sed -e 's#^git@github.com:#https://github.com/#' -e 's#\.git$##')
CODE=$(curl -sL -o /dev/null --max-time 15 -w '%{http_code}' "$WEB" || echo 000)
if [ "$CODE" = "200" ]; then
  echo "중단: 백업 저장소($WEB)가 공개(Public) 상태입니다. GitHub에서 Private로 바꾼 뒤 다시 실행하세요."
  exit 1
fi

mkdir -p "$DST/data"
rm -rf "$DST/data/uploads"   # 과거 버전이 올렸을 수 있는 원고 폴더는 저장소에서 제거

# 폴더 동기화 (없으면 건너뜀) — uploads는 정책상 제외
for d in history styles; do
  if [ -d "$SRC/$d" ]; then
    rsync -a --delete "$SRC/$d/" "$DST/data/$d/"
  fi
done

# 데이터 파일 복사 (비밀 파일 config.json은 제외)
for f in suggestions.json admin_standards.json style_directives.json \
         usage_log.json feedback_log.json case_corpus.json org_requests.json \
         history_archive.csv compare_corpus.jsonl api_cost_log.json; do
  if [ -f "$SRC/$f" ]; then
    cp -f "$SRC/$f" "$DST/data/$f"
  fi
done

cd "$DST"
git add -A
if git diff --cached --quiet; then
  echo "$(date '+%F %T') 변경 없음 — GitHub 백업 생략"
else
  git commit -q -m "자동 백업 $(date '+%Y-%m-%d %H:%M')"
  git push -q -u origin HEAD
  echo "$(date '+%F %T') GitHub 백업 완료"
fi
