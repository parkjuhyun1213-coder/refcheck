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
# 연도 뒤는 닫는 괄호·쉼표·콜론이어야 한다 — '(2025년 기준)' 같은 본문 괄호가
# 서술 인용으로 오인되어 '이루어졌다(2025…'가 허위 누락으로 보고됐다(2026-09 실측)
_NARR_KO = re.compile(r"([가-힣]{2,5})(\s*외)?\s*\(\s*(" + _YEAR + r")(?=\s*[\),:;])")
_NARR_WEST = re.compile(
    r"([A-Z][A-Za-z\-']{2,})(?:\s+(?:et al\.?|and|&)\s*[A-Z]?[A-Za-z\-']*|와|과)?\s*\(\s*(" + _YEAR + r")(?=\s*[\),:;])"
)

_STOPWORDS_KO = {"그림", "부록", "제시", "발행", "개정", "조사", "연구", "분석", "결과", "이용", "적용", "기준",
                 "차이", "현황", "비교", "변화", "증가", "감소", "연수", "근속연수", "수준", "분포", "방법", "대상"}
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
        if re.fullmatch(r"[가-힣]+다", name):
            # '시행되었다(2025)' 같은 서술어+연도 괄호 — '다'로 끝나는 국내 저자명은
            # 사실상 없다(외국인명은 기준상 원어로 적으므로 한글 표기 충돌도 없음)
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


def _year4(e: dict) -> str:
    m = re.match(r"\d{4}", e.get("year") or "")
    return m.group(0) if m else ""


def is_conversion_pair(a: dict, b: dict) -> bool:
    """국문 문헌과 그 '국한문 참고문헌의 영문 표기'(영문 변환) 짝인지.

    학회 투고규정은 국문 참고문헌에 영문화 목록을 병기하게 하므로, 같은 문헌이
    국문·영문으로 한 번씩 나타나는 것은 중복이 아니라 규정 이행이다. DOI가 같다고
    '중복 의심'으로 몰거나, 영문 표기를 '본문에 인용 없음'으로 몰면 안 된다
    (국문 본문은 국문 표기로 인용한다. 2026-09 실측).
    """
    if "is_en_conversion" in a or "is_en_conversion" in b:
        # 원고가 소절 표제('국한문 참고문헌의 영문 표기' 등)로 변환 구역을 명시한 경우
        # extract 단계가 달아 준 플래그가 우선이다 — 변환끼리·원문끼리는 짝이 아니고,
        # 표제 밖의 서양어 문헌은 변환 표기가 아니므로 휴리스틱으로 짝짓지 않는다.
        if bool(a.get("is_en_conversion")) == bool(b.get("is_en_conversion")):
            return False
    if (a.get("lang") == "west") == (b.get("lang") == "west"):
        return False  # 둘 다 국문이거나 둘 다 서양어면 변환 짝이 아니다
    ko, west = (a, b) if b.get("lang") == "west" else (b, a)
    da, db = (ko.get("doi") or "").lower(), (west.get("doi") or "").lower()
    if da and da == db:
        return True
    if _year4(ko) != _year4(west):
        return False
    if ko.get("volume") and ko.get("pages") and \
            ko.get("volume") == west.get("volume") and ko.get("issue") == west.get("issue") and \
            re.sub(r"\D", "", ko["pages"]) == re.sub(r"\D", "", west.get("pages") or ""):
        return True
    # 권·호·면수가 없는 유형(학위논문·보고서·법령·단행본)은 같은 해 같은 유형이면 짝으로 본다
    # — 잘못 짝지어도 결과는 경고 억제뿐이라, 허위 경고보다 해가 작다
    return ko.get("type") in ("thesis", "report", "law", "book") and ko.get("type") == west.get("type")


def en_conversion_flags(entries: list[dict]) -> list[bool]:
    """항목별 '영문 변환 표기' 여부.

    원고가 소절 표제('국한문 참고문헌의 영문 표기'·'영문 변환 목록' 등)를 명시해
    extract 단계에서 is_en_conversion 플래그가 달렸으면 그것을 그대로 쓴다(표제 기반이
    더 정확하고, DOI·권호면수가 없는 유형까지 빠짐없이 잡는다). 플래그가 없는 원고에서만
    서지 요소 휴리스틱(is_conversion_pair)으로 추정한다.
    """
    if any("is_en_conversion" in e for e in entries):
        return [bool(e.get("is_en_conversion")) for e in entries]
    ko_entries = [e for e in entries if e.get("lang") != "west"]
    return [e.get("lang") == "west" and any(is_conversion_pair(k, e) for k in ko_entries)
            for e in entries]


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
    conv_flags = en_conversion_flags(entries)
    for e, ks, is_conv in zip(entries, per_entry_keys, conv_flags):
        if not ks:
            continue
        if is_conv:
            # '국한문 참고문헌의 영문 표기' 항목 — 본문(국문)은 국문 표기로 인용하므로
            # 변환 표기가 본문에 없는 것이 정상이다(2026-09 실측)
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
