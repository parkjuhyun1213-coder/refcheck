# -*- coding: utf-8 -*-
"""규정 Q&A 코퍼스 생성 — 규정/ 폴더의 원문(PDF·HWPX)에서 app/regs_corpus.py를 만든다.

실행(로컬 전용): app/ 디렉터리에서  python make_regs_corpus.py
규정이 개정되면 규정/ 폴더의 파일을 교체한 뒤 재실행하고 결과를 커밋한다.
서버에는 규정/ 폴더가 배포되지 않으므로(update.sh는 app/만 복사) 이 스크립트는
서버에서 실행할 수 없고, 생성물인 regs_corpus.py를 반드시 커밋해야 한다.
"""
import re
import sys
import time
from pathlib import Path

import parsing

APP_DIR = Path(__file__).parent
REGS_DIR = APP_DIR.parent / "규정"
OUT_PATH = APP_DIR / "regs_corpus.py"

# (학회명 또는 "" = 공통기준, 표시용 문서명, 규정/ 아래 상대 경로)
# 학회명은 main.py의 DEFAULT_ORGS 표기와 정확히 일치해야 한다(가운뎃점 없음).
SOURCES = [
    ("", "문편협 「인용 및 참고문헌의 기술요소와 형식에 관한 공통기준」 v7(2024. 6. 17. 개정) 및 참고문헌 기술 주요 오류 유형",
     "붙임 2. 참고문헌기술_주요 오류 유형 및 [문편협] 인용 및 참고문헌 기술요소 및 형식에 관한 공통기준_v7_20240617.pdf"),
    ("한국도서관정보학회", "한국도서관·정보학회 논문투고규정(문편협 공통기준 적용, 2026. 2. 2. 개정)",
     "한국도서관정보학회 규정/붙임 3. 한국도서관·정보학회 논문투고규정__(문편협공통기준적용)_20260202.pdf"),
    ("한국도서관정보학회", "한국도서관·정보학회 참고문헌 기술 관련 주요 오류 유형 v2",
     "한국도서관정보학회 규정/붙임 4. 한국도서관·정보학회_참고문헌기술관련주요오류유형_v2.pdf"),
    ("한국도서관정보학회", "도서관정보학회지 원고형식",
     "한국도서관정보학회 규정/도서관정보학회지_원고형식.hwpx"),
    ("한국비블리아학회", "한국비블리아학회 논문투고규정(2025. 2. 개정)",
     "한국비블리아학회_논문투고규정(2025. 2).pdf"),
    ("한국정보관리학회", "한국정보관리학회 편집위원회 규정(논문 투고·심사 규정 포함)",
     "한국정보관리학회_편집위원회_규정.pdf"),
]

# 자체 규정 원문이 없는 학회도 키를 만들어 둔다(Q&A는 공통기준만으로 답한다).
ALL_ORGS = ["한국도서관정보학회", "한국문헌정보학회", "한국비블리아학회", "한국정보관리학회"]


def clean(text: str) -> str:
    """추출 텍스트 정돈 — 원문 인용을 보존해야 하므로 공백 정리만 한다."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+\n", "\n", text)        # 줄 끝 공백
    text = re.sub(r"\n{3,}", "\n\n", text)        # 3연속 이상 빈 줄 축소
    return text.strip()


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):   # Windows 콘솔(cp949)에서도 한글·기호 출력
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if not REGS_DIR.exists():
        print("규정/ 폴더를 찾을 수 없습니다. 이 스크립트는 규정 원문이 있는 로컬에서만 실행합니다.")
        print("서버에는 생성물(regs_corpus.py)만 배포됩니다.")
        return 1

    common = None
    org_regs: dict[str, list[dict]] = {org: [] for org in ALL_ORGS}
    for org, source, rel in SOURCES:
        path = REGS_DIR / rel
        if not path.exists():
            print(f"[누락] {rel} — 파일이 없어 건너뜁니다.")
            continue
        try:
            text = clean(parsing.extract_text(path.name, path.read_bytes()))
        except parsing.ParseError as ex:
            print(f"[실패] {rel} — {ex}")
            continue
        if len(text) < 300:
            print(f"[경고] {rel} — 추출 텍스트가 {len(text)}자뿐입니다. 원문을 확인하세요.")
        doc = {"source": source, "text": text}
        if org:
            org_regs[org].append(doc)
        else:
            common = doc
        print(f"[추출] {source} — {len(text):,}자")

    if common is None:
        print("공통기준(붙임 2) 추출에 실패해 중단합니다.")
        return 1
    for org in ALL_ORGS:
        if not org_regs[org]:
            print(f"[안내] {org} — 자체 규정 원문 없음(Q&A는 공통기준만으로 답합니다).")

    body = (
        '# -*- coding: utf-8 -*-\n'
        '"""자동 생성 파일 — make_regs_corpus.py가 규정/ 원문에서 생성. 직접 수정 금지.\n\n'
        '규정이 개정되면 규정/ 폴더의 원문을 교체하고 make_regs_corpus.py를 재실행할 것.\n'
        '"""\n'
        f'GENERATED = {time.strftime("%Y-%m-%d")!r}\n\n'
        f'# 문편협 공통기준(전 학회 공통 근거)\nCOMMON = {common!r}\n\n'
        f'# 학회별 자체 규정 — 빈 리스트는 원문 미등록(공통기준만으로 답함)\nORG_REGS = {org_regs!r}\n'
    )
    OUT_PATH.write_text(body, encoding="utf-8")
    total = len(common["text"]) + sum(len(d["text"]) for docs in org_regs.values() for d in docs)
    print(f"\n생성 완료: {OUT_PATH.name} — 문서 {1 + sum(len(v) for v in org_regs.values())}건, 총 {total:,}자")
    return 0


if __name__ == "__main__":
    sys.exit(main())
