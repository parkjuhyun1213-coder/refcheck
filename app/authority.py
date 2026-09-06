# -*- coding: utf-8 -*-
"""저자명 전거(authority) 파일 — 국문 저자의 영문 표기를 축적·관리한다.

왜 필요한가(2026-09-07 실측):
KCI에 등록된 영문 저자명이 발행본과 다른 사례가 드물지 않다. 8건을 Crossref
(출판사가 발행본에서 등록한 메타데이터)와 대조했더니 변우열 Woo-Yeoul→Woo-Yeol,
이병기 Byeong-Ki→Byeong-Kee, 노영희 Young-Hee→Younghee, 권재현 Jae-Hyun→jae-hyun,
장보성 Bo Seong→Boseong으로 KCI 쪽이 어긋났다. 그러면서도 같은 저자가 논문마다
다르게 표기한 사례(변우열: 2011년 Woo-Yeoul / 2018년 Woo-Yeol)도 실재한다.

그래서 '정답 하나'를 기계가 정할 수 없다. 대신 **이용자가 발행본을 확인하고 확정한
표기**를 문헌 단위로 보관해 다음 원고에서 다시 쓴다 — 도서관의 전거 통제와 같은 방식.

구조(app/author_authority.json):
  {"version": 1,
   "docs":    {"<문헌키>": {"변우열": "Byun, Woo-Yeoul", ...}},   # 문헌 단위(정확)
   "authors": {"변우열": {"Byun, Woo-Yeoul": {"n": 3, "docs": [...]}}}}  # 저자 단위(참고)

문헌 단위를 우선한다 — 참고문헌은 '그 논문'의 기록이므로 그 논문의 표기가 전거다.
저자 단위는 문헌키가 없을 때의 제안과 '같은 저자의 다른 표기' 안내에만 쓴다.
"""
import json
import re
import threading
from pathlib import Path

PATH = Path(__file__).parent / "author_authority.json"
_LOCK = threading.Lock()
_HANGUL = re.compile(r"[가-힣]")
_ROMAN = re.compile(r"^[A-Za-z][A-Za-z\-'.’ ]*$")
# 한 저자 이름에 붙는 표기 이형 보관 상한 — 무한정 쌓이면 제안이 흐려진다
_MAX_FORMS = 8
_MAX_DOCS_PER_FORM = 20


def _blank() -> dict:
    return {"version": 1, "docs": {}, "authors": {}}


def load() -> dict:
    try:
        d = json.loads(PATH.read_text(encoding="utf-8"))
        if isinstance(d, dict) and "docs" in d and "authors" in d:
            return d
    except (OSError, ValueError):
        pass
    return _blank()


def _save(d: dict) -> None:
    try:
        PATH.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError:
        pass  # 전거는 부가 기능 — 저장 실패가 검사 자체를 막지 않는다


def norm_ko(name: str) -> str:
    """국문 저자명 정규화 — 소속 괄호·역할어·공백 제거('강봉숙(대구 서부고)' → '강봉숙')."""
    s = re.sub(r"[\(（\[].*?[\)）\]]", "", name or "")
    s = re.sub(r"\s*(외|편|공편|엮음|옮김|번역|감독|연출)\s*$", "", s)
    return re.sub(r"\s+", "", s).strip()


def norm_en(name: str) -> str:
    """영문 표기 정돈 — 전각 콤마·중복 공백·콤마 뒤 띄어쓰기만 손본다.

    철자는 절대 바꾸지 않는다. 발행본에 인쇄된 철자가 전거이기 때문이다.
    """
    s = re.sub(r"\s+", " ", (name or "").replace("，", ",")).strip().strip(",").strip()
    return re.sub(r",\s*", ", ", s)


def _same_en(a: str, b: str) -> bool:
    """철자 수준 비교 — 붙임표·띄어쓰기·대소문자 차이는 같은 표기로 본다."""
    f = lambda x: re.sub(r"[^a-z]", "", (x or "").casefold())
    return bool(f(a)) and f(a) == f(b)


def doc_key(entry: dict) -> str:
    """문헌 식별자 — DOI가 있으면 DOI, 없으면 '제1저자|연도|제목앞부분'."""
    doi = (entry.get("doi") or "").strip().lower()
    if doi:
        return doi
    au = (entry.get("authors") or [""])[0]
    year = re.sub(r"\D", "", entry.get("year") or "")[:4]
    title = re.sub(r"[^0-9a-z가-힣]", "", (entry.get("title") or "").lower())[:24]
    key = f"{norm_ko(au)}|{year}|{title}"
    return key if len(key) > 4 else ""


def record_pair(ko_entry: dict, en_entry: dict, data: dict | None = None) -> int:
    """국문 문헌과 그 영문 변환 표기의 저자를 짝지어 전거에 기록. 기록 건수 반환.

    이용자가 원고에 병기한 영문 표기 = 발행본을 확인하고 확정한 표기로 본다.
    저자 수가 다르면 어느 이름이 어느 이름인지 확신할 수 없으므로 기록하지 않는다.
    """
    ko_au = [norm_ko(a) for a in (ko_entry.get("authors") or []) if _HANGUL.search(a or "")]
    en_au = [norm_en(a) for a in (en_entry.get("authors") or [])]
    en_au = [a for a in en_au if _ROMAN.match(a.replace(",", ""))]
    if not ko_au or len(ko_au) != len(en_au):
        return 0
    key = doc_key(ko_entry) or doc_key(en_entry)
    if not key:
        return 0

    own = data is None
    d = load() if own else data
    doc = d["docs"].setdefault(key, {})
    n = 0
    for ko, en in zip(ko_au, en_au):
        if not ko or not en:
            continue
        doc[ko] = en
        forms = d["authors"].setdefault(ko, {})
        # 철자가 같은 이형이 이미 있으면 그쪽에 합친다(대소문자·붙임표 차이로 갈리지 않게)
        slot = next((f for f in forms if _same_en(f, en)), en)
        rec = forms.setdefault(slot, {"n": 0, "docs": []})
        rec["n"] += 1
        if key not in rec["docs"]:
            rec["docs"].append(key)
            del rec["docs"][:-_MAX_DOCS_PER_FORM]
        if len(forms) > _MAX_FORMS:  # 드물게 쌓인 이형은 사용 빈도가 낮은 것부터 정리
            for f in sorted(forms, key=lambda x: forms[x]["n"])[:len(forms) - _MAX_FORMS]:
                forms.pop(f, None)
        n += 1
    if own and n:
        with _LOCK:
            _save(d)
    return n


def record_pairs(pairs: list[tuple[dict, dict]]) -> int:
    """여러 짝을 한 번에 기록(파일 쓰기 1회)."""
    d = load()
    total = sum(record_pair(ko, en, d) for ko, en in pairs)
    if total:
        with _LOCK:
            _save(d)
    return total


def lookup(entry: dict, data: dict | None = None) -> dict:
    """이 문헌에 대해 축적된 저자 영문 표기. {국문명: 영문표기} — 없으면 빈 dict."""
    d = data if data is not None else load()
    key = doc_key(entry)
    return dict(d["docs"].get(key) or {}) if key else {}


def authors_en_for(entry: dict, data: dict | None = None) -> list[str]:
    """이 문헌 저자 순서대로의 영문 표기 목록 — 전원 확보됐을 때만 반환."""
    known = lookup(entry, data)
    if not known:
        return []
    out = []
    for a in entry.get("authors") or []:
        en = known.get(norm_ko(a))
        if not en:
            return []
        out.append(en)
    return out


def variants(ko_name: str, data: dict | None = None) -> list[tuple[str, int]]:
    """같은 저자의 표기 이형 — [(표기, 사용 횟수)] 사용 많은 순."""
    d = data if data is not None else load()
    forms = d["authors"].get(norm_ko(ko_name)) or {}
    return sorted(((f, r.get("n", 0)) for f, r in forms.items()), key=lambda x: -x[1])


def stats() -> dict:
    d = load()
    return {"docs": len(d["docs"]), "authors": len(d["authors"]),
            "forms": sum(len(v) for v in d["authors"].values())}
