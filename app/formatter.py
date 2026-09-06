# -*- coding: utf-8 -*-
"""문편협 공통기준(2024. 6. 17. 개정) 형식 변환·정렬·형식 검증."""
import re

# ---------------------------------------------------------------- 저자 표기

_SMALL_WORDS = {"a", "an", "the", "and", "or", "of", "in", "on", "for", "to",
                "with", "at", "by", "from", "as", "but", "nor", "vs"}


# 단체·기관 저자를 알아보는 단서. 공통기준 Ⅰ-6)은 단체명을 그대로 기재하도록 하며
# (예: Public Library Association), 인명처럼 뒤집으면 'IFLA Study Group on the FRBR'이
# 'FRBR, I. S. G. O. T.'가 되어 버린다.
_ORG_FUNCTION_WORDS = {"of", "on", "the", "for", "and", "in", "at", "&"}
_ORG_WORDS = {
    "association", "society", "institute", "institution", "university", "college",
    "school", "library", "libraries", "group", "committee", "council", "department",
    "division", "ministry", "agency", "bureau", "center", "centre", "foundation",
    "organization", "organisation", "board", "commission", "office", "museum",
    "archives", "federation", "union", "corporation", "company", "press",
    "national", "international", "federal", "administration", "network",
    "consortium", "academy", "authority", "service", "services", "project",
    # 'Softlink Education'을 인명으로 보고 'Education, S.'로 뒤집던 문제(2026-09 실측)
    "education", "research", "publishing", "publishers", "media", "solutions",
}


def _is_org_name(name: str) -> bool:
    """단체·기관 저자인지 — 인명 뒤집기를 건너뛸지 판단한다."""
    words = [w for w in re.split(r"[\s,]+", name.strip()) if w]
    if len(words) < 2:
        return False  # 한 낱말은 원래 뒤집지 않는다(IFLA 등)
    low = {w.lower().strip(".") for w in words}
    # 인명에는 들어가지 않는 기능어(of·on·the…)나 기관 명칭어가 있으면 단체로 본다
    return bool(low & _ORG_FUNCTION_WORDS) or bool(low & _ORG_WORDS)


# 로마자 표기 한국 성씨(주요 이형 포함) — 공통기준 Ⅱ-1)(3)은 '국내를 포함한 중국,
# 일본 저자는 성명을 그대로 기재'하고 서양 인명만 이름을 두문자로 줄이게 한다.
# 국문 문헌의 영문 인용(Byun, Woo-Yeoul)을 서양 저자로 보고 'Byun, W. Y.'로
# 줄이던 문제의 방어선(2026-09 실측).
_KR_SURNAMES = {
    "kim", "lee", "yi", "rhee", "park", "pak", "choi", "choe", "jung", "jeong", "chung",
    "kang", "gang", "cho", "jo", "yoon", "yun", "jang", "chang", "lim", "im", "rim",
    "han", "oh", "seo", "suh", "shin", "sin", "kwon", "gwon", "hwang", "ahn", "an",
    "song", "yoo", "yu", "ryu", "ryoo", "hong", "jeon", "chun", "jun", "ko", "koh",
    "go", "moon", "mun", "yang", "son", "sohn", "bae", "pae", "baek", "paik", "heo",
    "hur", "huh", "noh", "roh", "no", "nam", "sim", "shim", "ha", "joo", "ju", "chu",
    "koo", "gu", "ku", "min", "byun", "byeon", "kwak", "gwak", "sung", "seong", "cha",
    "woo", "kil", "gil", "hyun", "hyeon", "hu", "na", "ra", "do", "seok", "pyo",
    "chae", "won", "jin", "ok", "maeng", "bang", "pyeon", "byeong", "myung", "myeong",
}


def _east_asian_full_name(last: str, first: str) -> bool:
    """로마자 표기 동아시아 저자로 보이면 True — 이름을 두문자로 줄이지 않는다."""
    toks = [t for t in re.split(r"[\s\-]+", first) if t]
    if not toks or any(len(t.rstrip(".")) <= 1 for t in toks):
        return False  # 'J. A.'처럼 이미 두문자면 서양식 표기
    if len(toks) == 2 and ("-" in first or all(2 <= len(t) <= 7 for t in toks)):
        return True   # Woo-Yeoul · Bong-suk · Jee Yeon · Bo Seong 꼴
    return last.lower().rstrip(".") in _KR_SURNAMES  # Park, Juhyeon / Hong, Soram 꼴


def _west_author(name: str) -> str:
    """서양 저자명 → 'Last, F. M.' 형식. 단체·기관명은 그대로 둔다."""
    name = name.strip().rstrip(".")
    if not name:
        return name
    if _is_org_name(name):
        return name
    if "," in name:  # 이미 Last, First 형태
        last, first = [p.strip() for p in name.split(",", 1)]
    else:
        parts = name.split()
        if len(parts) == 1:
            return name
        last, first = parts[-1], " ".join(parts[:-1])
    if _east_asian_full_name(last, first):
        return f"{last}, {first}"
    initials = " ".join(
        f"{w[0].upper()}." for w in re.split(r"[\s\.\-]+", first) if w and w[0].isalpha()
    )
    return f"{last}, {initials}" if initials else last


def format_authors(entry: dict) -> str:
    authors = [a for a in entry.get("authors", []) if a and a.strip()]
    note = entry.get("author_note", "").strip()
    lang = entry.get("lang", "ko")
    if not authors:
        return ""
    if lang == "west":
        formatted = [_west_author(a) for a in authors]
        if len(formatted) == 1:
            s = formatted[0]
        elif len(formatted) == 2:
            s = f"{formatted[0]} & {formatted[1]}"
        else:
            s = ", ".join(formatted[:-1]) + ", & " + formatted[-1]
        if note:
            s += f" {note}" if note.endswith(".") else f" {note}."
        return s
    s = ", ".join(a.strip() for a in authors)
    if note:
        s += f" {note}"
    return s


# ---------------------------------------------------------------- 대소문자

def title_case(s: str) -> str:
    """서양 서명·간행물명: 단어 첫 글자 대문자(관사·전치사 제외, 약어 유지)."""
    if not s or not re.search(r"[A-Za-z]", s):
        return s
    words = s.split()
    out = []
    for i, w in enumerate(words):
        # 부제 경계(콜론·물음표·느낌표) 뒤 첫 낱말은 관사라도 대문자
        # (예: 'Equal Futures? An Imbalance of Opportunities')
        after_break = i > 0 and words[i - 1].endswith((":", "：", "?", "!"))
        if w.isupper() and len(w) >= 2:  # 약어(IFLA, DCF 등) 유지
            out.append(w)
        elif i > 0 and not after_break and w.lower() in _SMALL_WORDS:
            out.append(w.lower())
        else:
            out.append(w[0].upper() + w[1:] if w[0].isalpha() else w)
    return " ".join(out)


def sentence_case(s: str) -> str:
    """서양 논문명: 첫 글자만 대문자. 약어·고유명사(내부 대문자 연속어)는 유지.
    이미 소문자 위주면 그대로 두고, Title Case 로 판단될 때만 변환."""
    if not s or not re.search(r"[A-Za-z]", s):
        return s
    words = s.split()
    cap_words = [w for w in words[1:] if w[:1].isupper() and not (w.isupper() and len(w) >= 2)]
    if len(words) > 3 and len(cap_words) >= max(2, int(len(words) * 0.5)):
        out = []
        for i, w in enumerate(words):
            if w.isupper() and len(w) >= 2:
                out.append(w)
            elif i == 0:
                out.append(w[0].upper() + w[1:].lower() if w[0].isalpha() else w)
            else:
                out.append(w.lower())
        s = " ".join(out)
        # 부제 경계(콜론·물음표·느낌표) 뒤 첫 글자는 대문자
        s = re.sub(r"([:?!]\s*)([a-z])", lambda m: m.group(1) + m.group(2).upper(), s)
    else:
        s = s[0].upper() + s[1:] if s[0].isalpha() else s
    return s


# ---------------------------------------------------------------- 형식 변환

def format_entry(e: dict) -> str:
    """구조화된 문헌 → 문편협 기준 참고문헌 문자열."""
    # 구조화가 아무것도 못 건진 항목(제목·저자·수록지 전부 없음)은 빈 껍데기
    # '. (발행년불명).'을 만들지 않고 원문을 그대로 돌려준다 — 어떤 문헌인지
    # 알아볼 수 있어야 '확인 필요' 표시도 의미가 있다.
    if (not (e.get("title") or "").strip() and not (e.get("authors") or [])
            and not (e.get("container") or "").strip()):
        raw = re.sub(r"\s+", " ", (e.get("raw") or "")).strip()
        if raw:
            return raw
    lang = e.get("lang", "ko")
    west = lang == "west"
    t = e.get("type", "unknown")
    year = e.get("year", "") or ("n.d." if west else "발행년불명")
    if e.get("orig_year"):
        year = f"{e['orig_year']}/{e['year']}"
    date = e.get("date", "")

    authors = format_authors(e)
    title = (e.get("title") or "").strip().rstrip(".")
    container = (e.get("container") or "").strip().rstrip(".,")
    if west:
        if t == "journal":
            title = sentence_case(title)
            container = title_case(container)
        elif t in ("book", "report", "thesis", "web"):
            # 단독으로 간행되는 저작의 서명은 Title Case(공통기준 Ⅱ-1)(5)).
            # 학위논문과 웹 단독 문서도 단행본과 같이 다룬다 — 학회 원고형식 예시
            # 'Functional Requirements for Bibliographic Records: Final Report.'도
            # Title Case다. 문장식으로 낮추면 고유명사(Australia 등)까지 뭉개진다.
            title = title_case(title)
            container = title_case(container)
        elif t in ("newspaper", "conference"):
            title = sentence_case(title)
            container = title_case(container)

    head_date = f"({date})." if date and t in ("newspaper", "web", "conference", "interview", "av") else f"({year})."
    parts: list[str] = []

    def head():
        if authors:
            parts.append(f"{authors} {head_date}")
        else:
            parts.append(f"{title}. {head_date}")

    if t == "journal":
        head()
        if authors:
            parts.append(f"{title}.")
        seg = container
        if e.get("volume"):
            seg += f", {e['volume']}"
            if e.get("issue"):
                seg += f"({e['issue']})"
        if e.get("pages"):
            seg += f", {e['pages']}"
        elif e.get("article_no"):
            seg += f", {e['article_no']}"
        parts.append(seg + ".")
        if e.get("doi"):
            parts.append(f"https://doi.org/{e['doi']}")
        elif e.get("url"):
            parts.append(e["url"])

    elif t == "book":
        head()
        if authors:
            seg = title
            if e.get("edition"):
                seg += f" ({e['edition']})"
            parts.append(seg + ".")
        elif e.get("edition"):
            parts.append(f"({e['edition']}).")
        if e.get("place") and e.get("publisher"):
            parts.append(f"{e['place']}: {e['publisher']}.")
        elif e.get("publisher"):
            parts.append(f"{e['publisher']}.")

    elif t == "thesis":
        head()
        if authors:
            parts.append(f"{title}.")
        deg = e.get("degree", "")
        if west:
            deg = deg or "Thesis"
        else:
            deg = deg or "학위논문"
        seg = f"{deg}, {e.get('institution', '')}".rstrip(", ")
        if west and e.get("country"):
            seg += f", {e['country']}"
        parts.append(seg + ".")

    elif t == "report":
        head()
        if authors:
            seg = title
            if e.get("report_no"):
                seg += f" ({e['report_no']})"
            parts.append(seg + ".")
        pub = e.get("publisher", "")
        if pub and (not authors or pub not in ", ".join(e.get("authors", []))):
            parts.append(pub + ".")

    elif t == "newspaper":
        head()
        if authors:
            parts.append(f"{title}.")
        seg = container
        if e.get("pages"):
            seg += f", {e['pages']}"
        if seg:
            parts.append(seg + ".")
        if e.get("url"):
            parts.append(e["url"])

    elif t == "web":
        head()
        if authors:
            parts.append(f"{title}.")
        if container:
            parts.append(container + ".")
        if e.get("url"):
            parts.append(("Available: " if west else "출처: ") + e["url"])

    elif t == "conference":
        head()
        if authors:
            parts.append(f"{title}.")
        seg = container
        if e.get("pages"):
            seg += f", {e['pages']}"
        if seg:
            parts.append(seg + ".")

    elif t == "law":
        # 번호가 비면 '법령명..'이 된다 — 번호가 있을 때만 이어 붙인다
        num = (e.get("report_no") or "").strip()
        base = title.rstrip(".")
        return f"{base}. {num}." if num else f"{base}."

    elif t == "standard":
        who = authors or title
        seg = f"{who}. ({year}). " if authors else f"{title}. ({year}). "
        body = e.get("title") if authors else (container or "")
        if authors:
            seg += f"{body}"
        if e.get("report_no"):
            seg += f" ({e['report_no']})"
        seg = seg.rstrip(".") + "."
        if e.get("url"):
            seg += f" {e['url']}"
        return re.sub(r"\s{2,}", " ", seg).strip()

    elif t in ("av", "interview"):
        head()
        if authors:
            seg = title
            if e.get("medium"):
                seg += f" {e['medium']}"
            parts.append(seg + ".")
        if e.get("place") and e.get("publisher"):
            parts.append(f"{e['place']}: {e['publisher']}.")
        elif e.get("publisher"):
            parts.append(e["publisher"] + ".")

    else:  # unknown — 최대한 재구성
        head()
        if authors and title:
            parts.append(f"{title}.")
        if container:
            parts.append(container + ".")
        if e.get("publisher"):
            place = f"{e['place']}: " if e.get("place") else ""
            parts.append(f"{place}{e['publisher']}.")
        if e.get("url"):
            parts.append(e["url"])

    # 온라인 자료의 DOI·URL 보전 — 학위논문·보고서·발표집·단행본 등은 유형별 분기에
    # 출력이 없어서, 원고에 적힌 DOI·주소가 변환 과정에서 통째로 사라지고 있었다.
    # (학술지 논문은 공통기준 Ⅱ-1)(6)에 따라 DOI가 있으면 DOI만 쓴다 — 분기에서 처리)
    if e.get("doi") and not any(e["doi"] in p for p in parts):
        parts.append(f"https://doi.org/{e['doi']}")
    elif e.get("url") and not e.get("doi") and not any(e["url"] in p for p in parts):
        parts.append(e["url"])

    s = " ".join(p for p in parts if p and p.strip())
    s = re.sub(r"\s{2,}", " ", s)
    # 문장부 정리('. .' → '.')는 주소 밖에서만 — DOI·URL에는 '..'가 합법적으로
    # 들어간다(예: 10.26589/jockle..103.202501.7). 여기서 뭉개면 링크가 깨진다(실측).
    toks = re.split(r"(https?://\S+|\b10\.\d{4,9}/\S+)", s)
    s = "".join(t if i % 2 else re.sub(r"\.\s*\.", ".", t) for i, t in enumerate(toks))
    return s.strip()


# ---------------------------------------------------------------- 형식 검증

REQUIRED_BY_TYPE = {
    "journal": ["authors", "year", "title", "container"],
    "book": ["authors", "year", "title", "publisher"],
    "thesis": ["authors", "year", "title", "degree", "institution"],
    "report": ["year", "title"],
    "newspaper": ["title", "container"],
    "web": ["title", "url"],
    "conference": ["authors", "title", "container"],
    "law": ["title"],
    "standard": ["title"],
}

FIELD_LABELS = {
    "authors": "저자명", "year": "발행연도", "title": "제목", "container": "게재지·매체명",
    "publisher": "출판사", "degree": "학위명", "institution": "수여기관",
    "pages": "면수", "url": "URL", "place": "출판지",
}


def validate_entry(e: dict) -> list[str]:
    issues = list(e.get("notes", []))
    req = REQUIRED_BY_TYPE.get(e.get("type", ""), ["title"])
    for f in req:
        v = e.get(f)
        if not v or (isinstance(v, list) and not any(v)):
            if e.get("type") in ("law", "standard") and f == "authors":
                continue
            label = FIELD_LABELS.get(f, f)
            msg = f"{label} 누락 — 확인 필요"
            if msg not in issues:
                issues.append(msg)
    if e.get("type") == "journal" and not e.get("pages") and not e.get("article_no"):
        if not any("면수" in i for i in issues):
            issues.append("면수 누락 — 확인 필요(온라인 학술지는 아티클 넘버)")
    if e.get("type") == "book" and not e.get("place"):
        issues.append("출판지 누락 — 확인 필요")
    return issues


def lost_elements(raw: str, formatted: str) -> list[str]:
    """변환이 원문의 핵심 서지 요소를 잃었는지 검사 — 잃은 요소마다 '확인 필요' 메모.

    표기 변경(재배열·구두점·대소문자)은 정상이므로, 문자 그대로 보존돼야 할 고정
    식별자만 본다: DOI·URL·ISBN·학위 종류·대학교명. 접속일자·(DOI가 있을 때의)
    중복 URL처럼 기준이 의도적으로 없애는 요소는 검사하지 않는다.
    실사용에서 AI가 배치 구조화 중 요소를 산발적으로 빠뜨린 사례의 안전망이다.
    """
    issues: list[str] = []
    if not raw or not formatted:
        return issues
    f_low = formatted.casefold()
    m = re.search(r"\b10\.\d{4,9}/[^\s\"<>]+", raw)
    if m:
        doi = m.group(0).rstrip(".,;)")
        if doi.casefold() not in f_low:
            issues.append(f"원문의 DOI({doi})가 결과에 빠짐 — 확인 필요")
    m = re.search(r"(석사|박사)(?=\s*학위\s*논문)", raw)
    if m and m.group(1) not in formatted:
        issues.append(f"원문의 '{m.group(1)}학위논문' 구분이 결과에 빠짐 — 확인 필요")
    for univ in set(re.findall(r"[가-힣]{2,20}대학교", raw)):
        if univ not in formatted:
            issues.append(f"원문의 기관명({univ})이 결과에 빠짐 — 확인 필요")
    # 영문 학위논문의 수여기관 — 학위 문구 뒤의 기관명이 통째로 사라진 사례 방어(2026-09 실측)
    m = re.search(r"(?i:dissertation|thesis)[\)\.,:]*\s+(?P<u>[A-Z][A-Za-z&.\-' ]{1,60}?)(?=\s*[,\.;]|\s*$)", raw)
    if m and m.group("u").strip().casefold() not in f_low:
        issues.append(f"원문의 기관명({m.group('u').strip()})이 결과에 빠짐 — 확인 필요")
    # 법령 번호('법률 제18547호'·'Act No. 18547') — 구조화가 놓치면 조용히 사라진다
    m = re.search(r"법률\s*제?\s*\d+호|Act\s+No\.?\s*\d+", raw, re.I)
    if m:
        law_no = re.sub(r"\s", "", m.group(0)).casefold()
        if law_no not in re.sub(r"\s", "", formatted).casefold():
            issues.append(f"원문의 법령 번호({m.group(0)})가 결과에 빠짐 — 확인 필요")
    m = re.search(r"ISBN[\s:]*([0-9Xx][0-9Xx\- ]{8,16}[0-9Xx])", raw, re.I)
    if m:
        digits = re.sub(r"[^0-9Xx]", "", m.group(1))
        if digits and digits not in re.sub(r"[^0-9Xx]", "", formatted):
            issues.append("원문의 ISBN이 결과에 빠짐 — 확인 필요")
    m = re.search(r"https?://[^\s\"<>]+", raw)
    if m and "doi.org" not in m.group(0):
        url = m.group(0).rstrip(".,;)")
        # 학술지 논문은 DOI가 있으면 URL을 쓰지 않는 것이 기준(Ⅱ-1(6)) — DOI가 있으면 생략 정상
        if url.casefold() not in f_low and not re.search(r"\b10\.\d{4,9}/", formatted):
            issues.append("원문의 URL이 결과에 빠짐 — 확인 필요")
    return issues


# ---------------------------------------------------------------- 정렬

_LANG_ORDER = {"ko": 0, "west": 1, "east": 2}


def _sort_key(e: dict):
    lang = e.get("lang", "ko")
    authors = e.get("authors") or []
    if authors:
        name = authors[0]
        if lang == "west":
            name = _west_author(name).split(",")[0].lower()
    else:
        name = (e.get("title") or "").lower()
    year = e.get("year", "")
    ym = re.match(r"(\d{4})", year or "")
    ynum = int(ym.group(1)) if ym else 9999
    # 소절 표제로 명시된 '국문 문헌의 영문 변환 표기'는 서양문헌이 아니다 —
    # 국내→서양→동양 원문 뒤에 별도 그룹으로 모아 알파벳순으로 배열한다
    order = 3 if e.get("is_en_conversion") else _LANG_ORDER.get(lang, 0)
    return (order, name, ynum, (e.get("title") or "").lower())


def _author_year_key(e: dict):
    authors = tuple(a.strip() for a in (e.get("authors") or []))
    year = re.sub(r"[a-z]$", "", e.get("year", "") or "")
    return (authors, year)


def sort_and_disambiguate(entries: list[dict]) -> list[dict]:
    """국내→서양→동양, 저자 가나다/알파벳, 연도 오름차순 정렬 후
    동일 저자·동일 연도 문헌에 a, b, c 부기."""
    ordered = sorted(entries, key=_sort_key)
    groups: dict[tuple, list[dict]] = {}
    for e in ordered:
        if not e.get("authors") or not re.match(r"\d{4}$", (e.get("year") or "")):
            continue
        groups.setdefault(_author_year_key(e), []).append(e)
    for key, group in groups.items():
        if len(group) > 1:
            group.sort(key=lambda x: (x.get("title") or "").lower())
            for i, e in enumerate(group):
                e["year"] = f"{key[1]}{chr(ord('a') + i)}"
    return sorted(entries, key=_sort_key)
