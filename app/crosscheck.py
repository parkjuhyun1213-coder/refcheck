# -*- coding: utf-8 -*-
"""본문 내 인용 ↔ 참고문헌 목록 대조.

문편협 기준 II-1-(1): 본문에서 인용한 문헌은 반드시 참고문헌 목록에 포함해야 하며,
참고문헌 목록은 본문에서 인용·언급한 문헌만 제시한다.
"""
import re

_YEAR = r"(?:1[89]\d{2}|20\d{2})[a-z]?"

# 괄호 인용: (홍길동, 2020), (홍길동 외, 2020; 김철수, 2021), (Smith et al., 2020, 15-17)
_PAREN_RE = re.compile(r"\(([^()]{2,120}?(?:1[89]\d{2}|20\d{2})[^()]{0,40})\)")

# 서술 인용: 홍길동(2020), 홍길동 외(2020), Smith(2020), Smith et al.(2020), Golder와 Huberman(2006)
_NARR_KO = re.compile(r"([가-힣]{2,5})(\s*외)?\s*\(\s*(" + _YEAR + r")")
_NARR_WEST = re.compile(
    r"([A-Z][A-Za-z\-']{2,})(?:\s+(?:et al\.?|and|&)\s*[A-Z]?[A-Za-z\-']*|와|과)?\s*\(\s*(" + _YEAR + r")"
)

_STOPWORDS_KO = {"그림", "부록", "제시", "발행", "개정", "조사", "연구", "분석", "결과", "이용", "적용", "기준"}
_STOPWORDS_WEST = {"Table", "Figure", "Appendix", "Chapter", "Section", "Vol", "No", "The", "In", "According"}


def _norm_year(y: str) -> str:
    return re.sub(r"[a-z]$", "", y or "")


def extract_citations(body_text: str) -> list[dict]:
    """본문에서 (이름, 연도) 인용 후보 추출."""
    found: dict[tuple, dict] = {}

    def add(name: str, year: str, snippet: str):
        name = name.strip().rstrip(",")
        if not name or name in _STOPWORDS_KO or name in _STOPWORDS_WEST:
            return
        key = (name, _norm_year(year))
        if key not in found:
            found[key] = {"name": name, "year": _norm_year(year), "snippet": snippet.strip()[:90]}

    # 괄호 인용 — 세미콜론 구분 복합 인용 처리
    for m in _PAREN_RE.finditer(body_text):
        inner = m.group(1)
        if re.search(r"https?://|표\s*\d|그림\s*\d|Figure|Table", inner):
            continue
        for seg in inner.split(";"):
            seg = seg.strip()
            ym = re.search(_YEAR, seg)
            if not ym:
                continue
            year = ym.group(0)
            name_part = seg[: ym.start()].strip().rstrip(",").strip()
            name_part = re.sub(r"\s*(외|et al\.?|&.*|와$|과$)\s*$", "", name_part).strip().rstrip(",")
            # 복수 저자 표기 "김영석, 이용재" → 첫 저자
            first = re.split(r"[,·]", name_part)[0].strip()
            if re.fullmatch(r"[가-힣]{2,5}|[A-Z][A-Za-z\-']{2,}|[一-鿿]{2,6}", first):
                add(first, year, m.group(0))

    # 서술 인용
    for m in _NARR_KO.finditer(body_text):
        add(m.group(1), m.group(3), m.group(0))
    for m in _NARR_WEST.finditer(body_text):
        add(m.group(1), m.group(2), m.group(0))

    return list(found.values())


def _ref_keys(entry: dict) -> set[tuple[str, str]]:
    """참고문헌 한 건에서 매칭용 (이름, 연도) 키 집합 생성."""
    keys = set()
    year = _norm_year(entry.get("year", ""))
    years = {year}
    if entry.get("orig_year"):
        years.add(entry["orig_year"])
    for a in entry.get("authors") or []:
        a = a.strip()
        if not a:
            continue
        if entry.get("lang") == "west":
            last = a.split(",")[0].strip()
            for y in years:
                keys.add((last.lower(), y))
        else:
            name = re.sub(r"\s*(외|편|공편|옮김|번역)\s*$", "", a).strip()
            for y in years:
                keys.add((name, y))
    if not entry.get("authors"):
        title = (entry.get("title") or "")[:12]
        for y in years:
            keys.add((title, y))
    return keys


def cross_check(body_text: str, entries: list[dict]) -> dict:
    """본문 인용과 참고문헌 목록 대조 결과.
    {citations_found, cited_not_listed: [...], listed_not_cited: [...]}"""
    citations = extract_citations(body_text)

    all_ref_keys: set[tuple[str, str]] = set()
    per_entry_keys: list[set] = []
    for e in entries:
        ks = _ref_keys(e)
        per_entry_keys.append(ks)
        all_ref_keys |= ks

    ref_names = {k[0] for k in all_ref_keys}
    cited_not_listed = []
    matched_keys: set[tuple[str, str]] = set()
    for c in citations:
        name = c["name"]
        key_candidates = [(name, c["year"]), (name.lower(), c["year"])]
        hit = None
        for kc in key_candidates:
            if kc in all_ref_keys:
                hit = kc
                break
        if hit:
            matched_keys.add(hit)
        else:
            # 이름만 일치(연도 상이)도 목록 누락으로 보지 않되 메모
            name_only = name in ref_names or name.lower() in ref_names
            cited_not_listed.append({**c, "name_only_match": name_only})

    listed_not_cited = []
    for e, ks in zip(entries, per_entry_keys):
        if not ks:
            continue
        if not (ks & matched_keys):
            # 이름만이라도 본문에 등장하면 인용된 것으로 간주(연도 표기 차이 허용)
            names = {k[0] for k in ks}
            body_lower = body_text.lower()
            if any((n and (n in body_text or n in body_lower)) for n in names):
                continue
            listed_not_cited.append({
                "raw": e.get("raw", "")[:120],
                "authors": ", ".join(e.get("authors") or [])[:60],
                "year": e.get("year", ""),
            })

    return {
        "citations_found": len(citations),
        "cited_not_listed": cited_not_listed,
        "listed_not_cited": listed_not_cited,
    }
