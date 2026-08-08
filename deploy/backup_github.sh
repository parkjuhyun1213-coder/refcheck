#!/usr/bin/env bash
# ============================================================
# 서비스 데이터 자동 백업 → GitHub 비공개 저장소
#
# 백업 대상: 처리 이력(history/), 원고·발행본 파일(uploads/),
#           사용자 기준(styles/), 제안·기준·통계 JSON, 이력 아카이브 CSV
# 제외 대상: .env, config.json (API 키·비밀번호 — 보안상 GitHub에 올리지 않음)
#
# 최초 설치 방법은 배포가이드.md의 '자동 백업' 절 참조.
# cron 등록 예: 매일 새벽 4시
#   0 4 * * * bash /opt/refstd/deploy/backup_github.sh >> /var/log/refstd-backup.log 2>&1
# ============================================================
set -euo pipefail

SRC=/opt/refstd/app
DST=/opt/refstd-backup   # GitHub 비공개 저장소를 클론해 둔 위치

if [ ! -d "$DST/.git" ]; then
  echo "오류: $DST 에 백업 저장소가 없습니다. 배포가이드의 자동 백업 설치 절차를 먼저 진행하세요."
  exit 1
fi

mkdir -p "$DST/data"

# 폴더 동기화 (없으면 건너뜀)
for d in history uploads styles; do
  if [ -d "$SRC/$d" ]; then
    rsync -a --delete "$SRC/$d/" "$DST/data/$d/"
  fi
done

# 데이터 파일 복사 (비밀 파일 config.json은 제외)
for f in suggestions.json admin_standards.json style_directives.json \
         usage_log.json feedback_log.json case_corpus.json org_requests.json \
         history_archive.csv; do
  if [ -f "$SRC/$f" ]; then
    cp -f "$SRC/$f" "$DST/data/$f"
  fi
done

cd "$DST"
git add -A
if git diff --cached --quiet; then
  echo "$(date '+%F %T') 변경 없음 — 백업 생략"
else
  git commit -q -m "자동 백업 $(date '+%Y-%m-%d %H:%M')"
  git push -q -u origin HEAD
  echo "$(date '+%F %T') 백업 완료"
fi
