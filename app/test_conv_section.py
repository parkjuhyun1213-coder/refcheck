# -*- coding: utf-8 -*-
"""영문 변환 소절 표제 인식 테스트 (2026.09.07-01).

'국한문 참고문헌의 영문 표기' 류 소절 표제 감지 → is_en_conversion 플래그 전달 →
crosscheck 우선 사용 → 배열·그룹핑 → 영문 변환 목록 재활용까지.
실행: python app/test_conv_section.py  (AI 미사용 — 네트워크·API 키 불필요)
"""
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")  # Windows 콘솔(cp949) 대비

sys.path.insert(0, str(Path(__file__).parent))

import extract
import crosscheck
import formatter

_PASS = 0


def ok(cond, label):
    global _PASS
    assert cond, f"실패: {label}"
    _PASS += 1
    print(f"  ✓ {label}")


# ---------------------------------------------------------------- 1) 표제 감지
print("[1] 소절 표제 패턴")
HEADINGS = [
    "국한문 참고문헌의 영문 표기",
    "국문 참고문헌의 영문 표기(English translation / Romanization of references originally written in Korean)",
    "국문 참고문헌의 영어 표기 (알파벳순)",
    "영문 변환 목록",
    "[국문 참고문헌 영문 변환 목록]",
    "영문화 목록",
    "(English translation / Romanization of references originally written in Korean)",
    "• 국한문참고문헌의 영문표기 :",
    "<참고문헌의 영문 표기>",
]
for h in HEADINGS:
    ok(extract._is_conv_heading_line(h), f"표제 인식: {h[:44]}")

NOT_HEADINGS = [
    # 연도가 있는 문헌 항목(제목에 '영문 표기'가 들어가도 표제가 아니다)
    "김철수 (2020). 국문 학술지의 영문 표기 실태. 도서관학, 51(3), 1-20.",
    # 줄바꿈으로 잘린 제목 조각 — 표제 문구 뒤에 다른 문장이 이어진다
    "영문 표기 실태와 개선 방안. 도서관학논집, 12, 1-20",
    # 괄호 없는 영문 줄 — 문헌 제목의 줄바꿈 조각과 구분 불가라 표제로 안 본다
    "Romanization of Korean bibliographic references.",
    # 서양문헌 그룹 표제(변환 목록이 아님)
    "영문 참고문헌",
    "국문 참고문헌",
    "영문 초록",
]
for h in NOT_HEADINGS:
    ok(not extract._is_conv_heading_line(h), f"오인 없음: {h[:44]}")

# ---------------------------------------------------------------- 2) 구역 분할
print("[2] find_en_conversion_split")
SECTION = """김성준 (2024). 학교도서관 현황조사 체계 분석. 한국도서관·정보학회지, 55(1), 1-25. https://doi.org/10.16981/kliss.55.1.202403.1
김철수 (2023). 도서관 통계의 신뢰성 연구. 정보관리학회지, 40(2), 55-78.
이용재 외 (2022). 학교도서관 운영 실태. 부산: 도서출판 미래.
국립중앙도서관 (2025). 전국 도서관 통계조사 보고서. 서울: 국립중앙도서관.
Smith, J. (2020). School library statistics. Library Quarterly, 90(4), 431-450.

국한문 참고문헌의 영문 표기
(English translation / Romanization of references originally written in Korean)

Kim, Sungjun (2024). An analysis of the school library survey system. Journal of Korean Library and Information Science Society, 55(1), 1-25. https://doi.org/10.16981/kliss.55.1.202403.1
Kim, Cheolsu (2023). A study on the reliability of library statistics. Journal of the Korean Society for Information Management, 40(2), 55-78.
Lee, Yongjae et al. (2022). School Library Management Practices. Busan: Mirae Publishing.
The National Library of Korea (2025). National Library Statistics Survey Report. Seoul: The National Library of Korea."""

main_sec, conv_sec, head = extract.find_en_conversion_split(SECTION)
ok(head == "국한문 참고문헌의 영문 표기", f"표제 문구 추출: {head}")
ok("Smith, J." in main_sec and "Kim, Sungjun" not in main_sec, "일반 목록에 원문만 남음")
ok(conv_sec.startswith("Kim, Sungjun") and "English translation" not in conv_sec,
   "변환 목록에서 영문 부기 줄 제거")
main_raws = extract.split_entries(main_sec)
conv_raws = extract.split_entries(conv_sec)
ok(len(main_raws) == 5, f"일반 목록 5건 분리 (실제 {len(main_raws)})")
ok(len(conv_raws) == 4, f"변환 목록 4건 분리 (실제 {len(conv_raws)})")

# 표제가 없는 구역은 그대로 반환
same, empty, no_head = extract.find_en_conversion_split("홍길동 (2020). 제목. 학회지, 1(1), 1-10.")
ok(empty == "" and no_head == "", "표제 없는 구역은 분할하지 않음")

# split_entries 단독 사용 시에도 표제 줄이 직전 항목에 붙지 않는다
glued = extract.split_entries(
    "이용재 (2022). 학교도서관 운영 실태. 부산: 도서출판 미래.\n국한문 참고문헌의 영문 표기\n"
    "Kim, Sungjun (2024). An analysis. Journal of KLISS, 55(1), 1-25.")
ok(all("영문 표기" not in r for r in glued), "split_entries가 표제 줄을 항목에서 배제")

# ---------------------------------------------------------------- 3) 플래그 우선
print("[3] crosscheck 플래그 우선")
e_ko = {"lang": "ko", "authors": ["김성준"], "year": "2024", "title": "학교도서관 현황조사 체계 분석",
        "doi": "10.16981/kliss.55.1.202403.1", "type": "journal", "is_en_conversion": False}
e_conv = {"lang": "west", "authors": ["Kim, Sungjun"], "year": "2024", "title": "An analysis",
          "doi": "10.16981/kliss.55.1.202403.1", "type": "journal", "is_en_conversion": True}
e_west = {"lang": "west", "authors": ["Smith, J."], "year": "2020", "title": "School library statistics",
          "type": "journal", "is_en_conversion": False}
flags = crosscheck.en_conversion_flags([e_ko, e_west, e_conv])
ok(flags == [False, False, True], "명시 플래그를 그대로 사용")
ok(crosscheck.is_conversion_pair(e_ko, e_conv), "원문↔변환(플래그 상이)은 짝")
ok(not crosscheck.is_conversion_pair(e_conv, dict(e_conv)), "변환끼리는 짝 아님")
# 표제 밖 서양어 문헌(플래그 False)과 국문 문헌은 DOI가 같아도 휴리스틱으로 짝짓지 않는다
e_west_same_doi = dict(e_west, doi=e_ko["doi"])
ok(not crosscheck.is_conversion_pair(e_ko, e_west_same_doi),
   "명시 모드에서는 표제 밖 서양어 문헌을 변환으로 추정하지 않음")
# 플래그가 아예 없는 원고는 현행 휴리스틱 유지
h_ko = {k: v for k, v in e_ko.items() if k != "is_en_conversion"}
h_conv = {k: v for k, v in e_conv.items() if k != "is_en_conversion"}
ok(crosscheck.en_conversion_flags([h_ko, h_conv]) == [False, True], "플래그 없으면 휴리스틱")

# ---------------------------------------------------------------- 4) 배열
print("[4] formatter 배열")
order = formatter.sort_and_disambiguate([dict(e_conv), dict(e_west), dict(e_ko)])
ok([e.get("is_en_conversion") for e in order] == [False, False, True]
   and order[0]["lang"] == "ko" and order[1]["authors"] == ["Smith, J."],
   "국내→서양→(동양)→영문 변환 순으로 배열")

# ---------------------------------------------------------------- 5) 짝 매칭(엄격)
print("[5] 원고 병기 변환 짝 매칭")
import main as main_mod
E = [
    {"lang": "ko", "type": "journal", "year": "2024", "doi": "10.1/a", "volume": "55", "issue": "1",
     "pages": "1-25", "raw": "김성준…", "is_en_conversion": False},                    # 0 — DOI 짝
    {"lang": "ko", "type": "journal", "year": "2023", "doi": "", "volume": "40", "issue": "2",
     "pages": "55-78", "raw": "김철수…", "is_en_conversion": False},                   # 1 — 권호면수 짝
    {"lang": "ko", "type": "thesis", "year": "2021", "raw": "박사1…", "is_en_conversion": False},  # 2 — 동년 학위 2건: 모호
    {"lang": "ko", "type": "thesis", "year": "2021", "raw": "박사2…", "is_en_conversion": False},  # 3
    {"lang": "ko", "type": "book", "year": "2022", "raw": "이용재…", "is_en_conversion": False},   # 4 — 유형+연도 유일 짝
    {"lang": "west", "type": "journal", "year": "2024", "doi": "10.1/a", "volume": "55",
     "issue": "1", "pages": "1-25", "raw": "Kim, Sungjun…", "is_en_conversion": True},   # 5
    {"lang": "west", "type": "journal", "year": "2023", "doi": "", "volume": "40", "issue": "2",
     "pages": "55-78", "raw": "Kim, Cheolsu…", "is_en_conversion": True},                # 6
    {"lang": "west", "type": "thesis", "year": "2021", "raw": "Park…", "is_en_conversion": True},  # 7
    {"lang": "west", "type": "book", "year": "2022", "raw": "Lee…", "is_en_conversion": True},     # 8
]
pairs = main_mod._pair_manuscript_conversions(E)
ok(pairs.get(0) == 5, "DOI 일치로 짝")
ok(pairs.get(1) == 6, "연도+권·호·면수 일치로 짝")
ok(pairs.get(4) == 8, "유형+연도 유일성으로 짝")
ok(2 not in pairs and 3 not in pairs, "같은 해 학위논문 2건(모호)은 짝짓지 않음")

# ---------------------------------------------------------------- 6) 파이프라인 종단
print("[6] 파이프라인 (규칙 모드, AI·네트워크 없음)")
import aiengine
aiengine.is_configured = lambda: False  # AI 키가 있어도 규칙 모드로 강제

MANUSCRIPT = """학교도서관 현황조사 체계 분석 연구

Ⅰ. 서론
학교도서관 현황조사의 체계를 분석하였다(김성준, 2024). 도서관 통계의 신뢰성 문제는 김철수(2023)가 지적한 바 있다.
해외에서는 Smith(2020)의 연구가 대표적이다. 국립중앙도서관(2025)의 조사와 이용재 외(2022)의 연구도 참고하였다.

Ⅱ. 결론
이상의 논의를 종합하였다.

참고문헌

""" + SECTION

res = main_mod._process_file("테스트_학교도서관.txt", MANUSCRIPT.encode("utf-8"),
                             {"style_id": "munpyeonhyeop", "verify": False,
                              "crosscheck": True, "english": True}, lambda *a: None)
ok(not res.get("error"), f"오류 없음 (error={res.get('error')})")
groups = []
for it in res["items"]:
    if it["group"] not in groups:
        groups.append(it["group"])
ok(groups == ["국내문헌", "서양문헌", "국문 문헌의 영문 변환 표기"],
   f"그룹 순서: {' → '.join(groups)}")
n_conv = sum(1 for it in res["items"] if it["group"] == "국문 문헌의 영문 변환 표기")
ok(n_conv == 4, f"변환 그룹 4건 (실제 {n_conv})")
ok(any("소절을 인식" in w for w in res["warnings"]), "소절 인식 안내 표시")
ok(res.get("english_list") and len(res["english_list"]) == 4,
   f"영문 변환 목록: 원고 표기 4건 재활용 (실제 {len(res.get('english_list') or [])})")
ok(any("재활용" in w for w in res["warnings"]), "재활용 안내 표시")
cc = res.get("crosscheck") or {}
uncited = [c.get("raw", "") + c.get("authors", "") for c in cc.get("listed_not_cited", [])]
ok(not any("Kim" in u or "Lee" in u or "National Library" in u for u in uncited),
   f"변환 항목이 '본문 미인용'으로 잡히지 않음 (미인용 {len(uncited)}건)")
ok(res["health"].get("en_conversions") == 4, "건전성 리포트 변환 건수 분리 집계")
ok(res["health"].get("year_dist", {}).get("2024", 0) == 1, "연도 분포에 변환 이중 집계 없음")

print(f"\n전체 {_PASS}건 통과")
