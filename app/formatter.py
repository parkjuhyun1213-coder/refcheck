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
}


def _is_org_name(name: str) -> bool:
    """단체·기관 저자인지 — 인명 뒤집기를 건너뛸지 판단한다."""
    words = [w for w in re.split(r"[\s,]+", name.strip()) if w]
    if len(words) < 2:
        return False  # 한 낱말은 원래 뒤집지 않는다(IFLA 등)
    low = {w.lower().strip(".") for w in words}
    # 인명에는 들어가지 않는 기능어(of·on·the…)나 기관 명칭어가 있으면 단체로 본다
    return bool(low & _ORG_FUNCTION_WORDS) or bool(low & _ORG_WORDS)


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
        after_colon = i > 0 and words[i - 1].endswith((":", "："))
        if w.isupper() and len(w) >= 2:  # 약어(IFLA, DCF 등) 유지
            out.append(w)
        elif i > 0 and not after_colon and w.lower() in _SMALL_WORDS:
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
        # 콜론 뒤 첫 글자 대문자
        s = re.sub(r"(:\s*)([a-z])", lambda m: m.group(1) + m.group(2).upper(), s, count=1)
    else:
        s = s[0].upper() + s[1:] if s[0].isalpha() else s
    return s


# ---------------------------------------------------------------- 형식 변환

def format_entry(e: dict) -> str:
    """구조화된 문헌 → 문편협 기준 참고문헌 문자열."""
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
        elif t in ("book", "report", "thesis"):
            # 단독으로 간행되는 저작의 서명은 Title Case(공통기준 Ⅱ-1)(5)).
            # 학위논문도 단행본과 같이 다룬다.
            title = title_case(title)
        elif t in ("newspaper", "web", "conference"):
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
        return f"{title}. {e.get('report_no', '')}.".replace(" .", ".").strip()

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

    # 온라인 자료의 URL 보전 — 학위논문·보고서·발표집·단행본 등은 유형별 분기에 URL
    # 출력이 없어서, 원고에 적힌 주소가 변환 과정에서 통째로 사라지고 있었다.
    # (학술지 논문은 공통기준 Ⅱ-1)(6)에 따라 DOI가 있으면 DOI만 쓴다)
    if e.get("url") and not e.get("doi") and not any(e["url"] in p for p in parts):
        parts.append(e["url"])

    s = " ".join(p for p in parts if p and p.strip())
    s = re.sub(r"\s{2,}", " ", s)
    s = re.sub(r"\.\s*\.", ".", s)
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
    return (_LANG_ORDER.get(lang, 0), name, ynum, (e.get("title") or "").lower())


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
