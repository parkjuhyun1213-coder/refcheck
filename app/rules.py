# -*- coding: utf-8 -*-
"""규칙 기반 서지 구조화: 참고문헌 문자열 → 구조화된 필드(dict).

AI 모드가 아닐 때 사용하는 휴리스틱 파서. AI 모드와 동일한 스키마를 반환한다.
다양한 원고 스타일(APA 변형, Chicago식 연도 후치, 국문 권·호 표기)을 최대한 흡수하고
실패한 요소는 notes에 '확인 필요'로 기록한다.
"""
import re

EMPTY_ENTRY = {
    "raw": "", "type": "unknown", "lang": "ko",
    "authors": [], "author_note": "",
    "year": "", "date": "", "orig_year": "",
    "title": "", "container": "", "editors": "",
    "volume": "", "issue": "", "pages": "", "article_no": "",
    "edition": "", "place": "", "publisher": "",
    "degree": "", "institution": "", "country": "",
    "report_no": "", "doi": "", "url": "",
    "medium": "", "notes": [],
}


def new_entry(raw: str) -> dict:
    e = {k: (list(v) if isinstance(v, list) else v) for k, v in EMPTY_ENTRY.items()}
    e["raw"] = raw
    return e


_HANGUL = re.compile(r"[가-힣]")
_CJK = re.compile(r"[一-鿿぀-ゟ゠-ヿ]")
_LATIN = re.compile(r"[A-Za-z]")
_YEAR = r"(?:18|19|20)\d{2}"


def detect_lang(text: str) -> str:
    head = text[:60]
    if _HANGUL.search(head):
        return "ko"
    if _CJK.search(head):
        return "east"
    if _LATIN.search(head):
        return "west"
    return "ko"


_DOI_RE = re.compile(r"(?:https?://(?:dx\.)?doi\.org/|doi[:\s]\s*)(10\.\d{4,9}/[^\s,;”\"]+)", re.I)
_URL_RE = re.compile(r"https?://[^\s。)\]”\"]+")
_KO_DATE = re.compile(r"\((" + _YEAR + r")\s*[\.,]\s*(\d{1,2})\s*[\.,]\s*(\d{1,2})\s*\.?\s*\)")
_EN_DATE = re.compile(r"\((" + _YEAR + r"),\s*([A-Z][a-z]+)(?:\s+(\d{1,2}))?\)")


def structure_entry(raw: str) -> dict:
    e = new_entry(raw)
    text = re.sub(r"\s{2,}", " ", raw.strip()).rstrip(".") + "."
    text = text.replace("․", "·").replace("‧", "·")  # 가운뎃점 이형 통일
    e["lang"] = detect_lang(text)

    # DOI / URL 분리
    m = _DOI_RE.search(text)
    if m:
        e["doi"] = m.group(1).rstrip(".")
        text = _DOI_RE.sub(" ", text)
    m = _URL_RE.search(text)
    if m:
        e["url"] = m.group(0).rstrip(".,)")
        text = _URL_RE.sub(" ", text)
    text = re.sub(r"(출처|Available|Retrieved from|Retrieved)\s*[::]?\s*", " ", text)
    text = re.sub(r"\s{2,}", " ", text).strip()

    # 연도/일자
    year = ""
    km = _KO_DATE.search(text)
    em = _EN_DATE.search(text)
    if km:
        year = km.group(1)
        e["date"] = f"{km.group(1)}. {int(km.group(2))}. {int(km.group(3))}."
    elif em:
        year = em.group(1)
        e["date"] = f"{em.group(1)}, {em.group(2)}" + (f" {em.group(3)}" if em.group(3) else "")
    else:
        ym = re.search(r"\((" + _YEAR + r")([a-z])?\)", text)
        if ym:
            year = ym.group(1) + (ym.group(2) or "")
            trans = re.search(r"\((" + _YEAR + r")/(" + _YEAR + r")\)", text)
            if trans:
                e["orig_year"], year = trans.group(1), trans.group(2)
        else:
            tail = re.search(r"[,\s\(](" + _YEAR + r")[a-z]?\s*[\.\):]*\s*$", text)
            mid = re.search(r"[\s,\(](" + _YEAR + r")[\)::]", text)
            if tail:
                year = tail.group(1)
            elif mid:
                year = mid.group(1)
            elif re.search(r"\(발행년불명\)", text):
                year = "발행년불명"
            elif re.search(r"\(n\.d\.\)", text, re.I):
                year = "n.d."
    e["year"] = year
    if not year:
        e["notes"].append("발행연도 확인 필요")

    e["type"] = _detect_type(text, e)
    _split_author_title(text, e)
    if e["type"] == "unknown":
        e["type"] = _refine_type_post(e)
    _fill_fields(e)
    return e


def _detect_type(text: str, e: dict) -> str:
    t = text
    if re.search(r"(석사|박사)\s*학위\s*논문|Doctoral dissertation|Master'?s thesis|Unpublished (doctoral|master)", t, re.I):
        return "thesis"
    if re.search(r"법률\s*제?\s*\d+호|공포번호|시행령|시행규칙|(^|\s)Chapter\s+[A-Z0-9][A-Z0-9\-\.]*\.?$", t):
        return "law"
    if re.search(r"(NAK\s?\d|ISO[/ ]?\d|KS\s?[A-Z]{1,2}\s?\d|IEC\s?\d|Standardization|표준)", t):
        return "standard"
    if re.search(r"\[(영화|TV\s*프로그램|CD|DVD|음반|Television|Motion picture|Film)\]", t, re.I):
        return "av"
    if re.search(r"\[(인터뷰|Interview)\]|와의\s*면담", t, re.I):
        return "interview"
    if re.search(r"(학술대회|학술발표대회|발표\s*논문집|Proceedings of|Paper presented|워크샵|워크숍|심포지엄)", t, re.I):
        return "conference"
    if re.search(r"(일보|신문|Times|Post|Herald|Tribune)[\s,\.]", t) and re.search(r"\(" + _YEAR + r"[\.,]\s*\d", t):
        return "newspaper"
    if e.get("date") and e.get("url"):
        return "web"
    if re.search(r"(연구보고서?|정책연구|조사분석|간행물\s*번호|Report No|백서|우수사례집|매뉴얼|지침서?)", t, re.I):
        return "report"
    # 학술지: 권(호) 또는 "학회지/Journal" 단서
    if re.search(r"[,\s]\d{1,4}\s*\(\d{1,3}\)\s*[,::]", t) or re.search(r"[,\s]\d{1,4}\s*[,::]\s*\d{1,5}\s*[-–]\s*\d{1,5}", t):
        return "journal"
    if re.search(r"(학회지|학보|저널|논집|논총|연구\s*[,\.]|Journal|Review|Quarterly|Research|Bulletin|Proceedings)[\s,\.]", t):
        return "journal"
    if re.search(r"제?\s*\d{1,3}\s*권\s*제?\s*\d{1,3}\s*호", t):
        return "journal"
    # 단행본: "출판지: 출판사"
    if re.search(r"[가-힣A-Za-z\.\s]{1,20}\s*[::]\s*\S", t):
        return "book"
    if e.get("url"):
        return "web"
    return "unknown"


def _refine_type_post(e: dict) -> str:
    """저자·제목 분리 후 추가 판별."""
    authors = e.get("authors") or []
    rest = e.get("_rest", "") or ""
    # 말미의 연도 표기는 판별에서 제외
    rest = re.sub(r"[,\s]+\(?(?:18|19|20)\d{2}[a-z]?\)?[\.\):]*\s*$", ".", rest).strip()
    if e.get("lang") == "ko" and authors:
        if re.search(r"(도서관|위원회|연구원|연구소|재단|협회|학회|공사|공단|부|처|청|원)$", authors[0]) and len(authors) == 1:
            return "report"
    if rest and re.search(r"[::]", rest):
        return "book"
    # 서양 단행본(Chicago식): "Title. Publisher, City." 꼴
    if e.get("lang") == "west" and re.search(r"\.\s+[A-Z][^\.]*?,\s*[A-Z][a-z]+\s*\.?\s*$", rest):
        return "book"
    # 국문 단행본: 끝이 출판사류 명칭
    if e.get("lang") == "ko" and re.search(r"(출판|출판사|협회|문화사|서관|미디어|아카데미|프레스)\s*\.?\s*$", rest):
        return "book"
    return "unknown"


# ------------------------------------------------------------ 저자·제목 분리

def _first_sentence_cut(text: str) -> tuple[str, str]:
    """'저자부. 나머지' 분리 — 이니셜(T.D.)의 마침표는 건너뛰지 않고 그대로 경계로 사용."""
    m = re.search(r"\.\s+(?=[A-Z가-힣“\"'『「])", text)
    if m and 2 <= m.start() <= 70:
        return text[: m.start()].strip(), text[m.end():].strip()
    return "", text


def _split_author_title(text: str, e: dict):
    year_paren = re.search(
        r"\((" + _YEAR + r"[a-z]?|" + _YEAR + r"/" + _YEAR + r"|" + _YEAR + r"[\.,][^)]*|발행년불명|n\.d\.)\)",
        text)

    # A) 표준형: 저자 (연도). 나머지
    if year_paren and year_paren.start() <= 70:
        before = text[: year_paren.start()].strip().rstrip(".,")
        after = text[year_paren.end():].strip().lstrip(".,").strip()
        if before:
            _assign_authors(before, e)
        else:
            # 무저자: 서명. (연도). 발행사항
            e["authors"] = []
        e["_rest"] = after
        if not before:
            fm = _first_sentence_cut(text[: year_paren.start()].strip())
            e["title"] = (fm[0] or text[: year_paren.start()].strip()).rstrip(".")
        return

    # B) 인용부호 제목형(Chicago 등): 저자. "제목." 나머지 (연도) 면수
    qm = re.match(r"^(?P<auth>[^\"“『「]{2,70}?)[\.,]?\s*[\"“『「](?P<title>[^\"”』」]+)[\"”』」][\.,]?\s*(?P<rest>.*)$", text)
    if qm:
        _assign_authors(qm.group("auth").strip().rstrip(".,"), e)
        e["title"] = qm.group("title").strip().rstrip(".,")
        e["_rest"] = qm.group("rest").strip()
        e["_title_fixed"] = True
        return

    # C) 연도 후치형: 저자부. 제목. 발행사항, 연도.
    auth, rest = _first_sentence_cut(text)
    if auth and not re.search(_YEAR, auth):
        _assign_authors(auth.rstrip(".,"), e)
        e["_rest"] = rest
        return

    # D) 실패 — 전체를 제목으로
    e["notes"].append("저자·연도 구분 확인 필요")
    e["title"] = text.strip().rstrip(".")
    e["_rest"] = ""


def _assign_authors(before: str, e: dict):
    role = re.search(r"(편저|공편|엮음|옮김|번역|감독|연출|편|eds?\.|Eds?\.|ed\.|Translated by)\s*$", before)
    if role:
        e["author_note"] = role.group(1)
        before = before[: role.start()].strip().rstrip(".,")
    e["authors"] = _split_authors(before, e["lang"])
    if not e["authors"]:
        e["notes"].append("저자명 확인 필요")


def _split_authors(s: str, lang: str) -> list[str]:
    s = re.sub(r"\s+and\s+", ",", s)
    s = s.replace("&", ",").replace("·", ",").replace("․", ",").replace("‧", ",")
    s = re.sub(r"\s+(와|과)\s+", ",", s)
    if lang == "west":
        parts = [p.strip() for p in s.split(",") if p.strip()]
        # "Caplan, Priscilla" — 성, 이름 꼴 단일 저자
        if len(parts) == 2 and re.fullmatch(r"[A-Z][a-z]+(?:\s[A-Z][a-z]+)?", parts[1]) \
                and not re.fullmatch(r"(?:[A-Z]\.?\s*)+", parts[1]):
            return [parts[0] + ", " + parts[1]]
        authors, i = [], 0
        while i < len(parts):
            nxt = parts[i + 1] if i + 1 < len(parts) else ""
            if re.fullmatch(r"(?:[A-Z]\.?\s*)+(?:Jr\.?|Sr\.?)?", nxt):
                authors.append(parts[i] + ", " + nxt.rstrip("."))
                i += 2
            else:
                if parts[i].lower() not in ("et al", "et al."):
                    authors.append(parts[i])
                i += 1
        return authors
    return [p.strip() for p in s.split(",") if p.strip() and p.strip() not in ("외",)]


# ------------------------------------------------------------ 세부 필드

def _norm_journal_rest(rest: str) -> str:
    """권·호·면수 표기 정규화: 제54권 제2호 → 54(2), vol. 52 → 52, pp.5-27 → 5-27."""
    rest = re.sub(r"[Vv]ol\.?\s*(\d+)", r" \1", rest)
    rest = re.sub(r"[Nn]o\.?\s*(\d+)", r"(\1)", rest)
    rest = re.sub(r"[Pp]p?\.\s*(\d+)", r"\1", rest)
    rest = re.sub(r"제?\s*(\d+)\s*권\s*,?\s*제?\s*(\d+)\s*호", r" \1(\2)", rest)
    rest = re.sub(r"제?\s*(\d+)\s*권", r" \1", rest)
    rest = re.sub(r"제?\s*(\d+)\s*집", r" \1", rest)
    rest = re.sub(r"(\d+)\s*[-–~]\s*(\d+)\s*(쪽|면|p\b)?", r"\1-\2", rest)
    rest = re.sub(r"(\d)\s*,?\s*\(\s*(\d{1,3})\s*\)", r"\1(\2)", rest)
    rest = re.sub(r"\s{2,}", " ", rest)
    return rest


# 학술지명은 마침표를 넘지 않도록 클래스에서 '.' 제외(콜론은 부제 허용)
_J_TAIL = re.compile(
    r"(?P<cont>[가-힣A-Za-z一-鿿&::\-·\s]{2,60}?)[,\s]+(?P<vol>\d{1,4})\s*(?:\((?P<iss>\d{1,3})\))?\s*[,::\s]\s*"
    r"(?P<pg>(?:paper\s*\d+|e\d{3,7}|\d{1,5}\s*[-–]\s*\d{1,5}|\d{1,5}))\s*\.?\s*$", re.I)

# 면수 없는 학술지(온라인 저널 등): 간행물명 권(호)만
_J_TAIL_NOPG = re.compile(
    r"(?P<cont>[가-힣A-Za-z一-鿿&::\-·\s]{2,60}?)[,\s]+(?P<vol>\d{1,4})\s*\((?P<iss>\d{1,3})\)\s*[,\.]?\s*$")

_KNOWN_CITIES = {
    "서울", "부산", "대구", "인천", "광주", "대전", "울산", "세종", "파주", "고양", "수원", "성남", "청주",
    "chicago", "london", "new york", "boston", "oxford", "cambridge", "berlin", "tokyo",
    "paris", "amsterdam", "philadelphia", "los angeles", "san francisco", "reading", "westport",
}


def _looks_like_city(s: str) -> bool:
    s = (s or "").strip().rstrip(".")
    return s.lower() in _KNOWN_CITIES or (len(s.split()) <= 2 and bool(re.fullmatch(r"[가-힣]{2,4}", s)))


def _looks_like_publisher(s: str) -> bool:
    return bool(re.search(r"(협회|출판|문화사|서관|미디어|아카데미|정보|Press|Publish|Association|Books|House|사)$",
                          (s or "").strip().rstrip(".")))


def _swap_place_publisher(e: dict):
    """'출판사: 도시' 로 뒤집혀 파싱된 경우 교정."""
    place, pub = e.get("place", ""), e.get("publisher", "")
    if place and pub and not _looks_like_city(place) and _looks_like_city(pub) and _looks_like_publisher(place):
        e["place"], e["publisher"] = pub, place


def _fill_fields(e: dict):
    rest = (e.pop("_rest", "") or "").strip()
    title_fixed = e.pop("_title_fixed", False)
    t = e["type"]
    year = re.sub(r"[a-z]$", "", e.get("year", ""))
    if year and re.match(r"\d{4}$", year):
        # 나머지 텍스트에서 이미 파악된 연도 표기는 제거(중복 방지)
        rest = re.sub(r"[,\s\(]+" + year + r"[a-z]?[\)\.::]?\s*", " ", rest).strip()
        rest = re.sub(r"\s{2,}", " ", rest)

    if t == "journal":
        rest_n = _norm_journal_rest(rest)
        m = _J_TAIL.search(rest_n)
        m2 = None if m else _J_TAIL_NOPG.search(rest_n)
        if m or m2:
            mm = m or m2
            head = rest_n[: mm.start()].strip().rstrip(".,")
            if not title_fixed:
                e["title"] = head or e.get("title", "")
            e["container"] = mm.group("cont").strip().rstrip(".,").lstrip(".,")
            e["volume"] = mm.group("vol") or ""
            e["issue"] = mm.group("iss") or ""
            if m:
                pg = m.group("pg")
                if re.match(r"(paper|e\d)", pg, re.I):
                    e["article_no"] = pg
                else:
                    e["pages"] = re.sub(r"\s*[-–]\s*", "-", pg)
            else:
                e["notes"].append("면수 확인 필요(온라인 학술지는 아티클 넘버)")
        else:
            if not title_fixed:
                parts = re.split(r"[\.\?]\s+", rest_n, maxsplit=1)
                e["title"] = (parts[0] if parts else rest_n).strip().rstrip(".,")
                if len(parts) > 1:
                    e["container"] = parts[1].strip().rstrip(".")
            else:
                e["container"] = rest_n.strip().rstrip(".")
            e["notes"].append("권·호·면수 확인 필요")

    elif t == "book":
        m = re.match(
            r"(?P<title>.+?)[\.\?]?\s*(?:\((?P<ed>[^)]*(?:판|ed\.)[^)]*)\))?\s*[\.]?\s*"
            r"(?P<place>[가-힣A-Za-z\.\s]{1,25}?)\s*[::]\s*(?P<pub>[^\.]+)\.?$", rest)
        if m:
            if not title_fixed:
                e["title"] = m.group("title").strip().rstrip(".,")
            e["edition"] = (m.group("ed") or "").strip()
            e["place"] = m.group("place").strip()
            e["publisher"] = m.group("pub").strip()
            _swap_place_publisher(e)
        else:
            # Chicago식: 제목. 출판사, 출판지(, 연도 제거됨)
            if not title_fixed:
                cut_title, remainder = _first_sentence_cut(rest)
                e["title"] = (cut_title or rest.split(".")[0]).strip().rstrip(".,")
            else:
                remainder = rest
            remainder = (remainder or "").strip().rstrip(".,")
            if title_fixed:
                remainder = rest.strip().rstrip(".,")
            pm = re.match(r"(?P<a>[^,::]+),\s*(?P<b>[^,::]+)$", remainder)
            if pm and len(pm.group("b").split()) <= 2:
                e["publisher"] = pm.group("a").strip()
                e["place"] = pm.group("b").strip()
            elif remainder and remainder != e.get("title", ""):
                e["publisher"] = remainder
                e["notes"].append("출판지·출판사 확인 필요")
            else:
                e["notes"].append("출판지·출판사 확인 필요")

    elif t == "thesis":
        m = re.search(
            r"(?P<deg>석사\s*학위\s*논문|박사\s*학위\s*논문|Doctoral dissertation|Master'?s thesis)\s*,?\s*"
            r"(?P<inst>[^,\.]+)?(?:,\s*(?P<country>[^\.]+))?", rest, re.I)
        if m:
            head = rest[: m.start()].strip().rstrip(".,")
            if not title_fixed and head:
                e["title"] = head
            deg = m.group("deg")
            e["degree"] = re.sub(r"\s", "", deg) if _HANGUL.search(deg) else deg
            e["institution"] = (m.group("inst") or "").strip()
            e["country"] = (m.group("country") or "").strip()
            if not e["institution"]:
                e["notes"].append("학위수여기관 확인 필요")
        else:
            if not title_fixed:
                e["title"] = rest.split(".")[0].strip()
            e["notes"].append("학위명·수여기관 확인 필요")

    elif t == "report":
        m = re.match(r"(?P<title>.+?)\s*(?:\((?P<no>[^)]+)\))?\s*\.\s*(?P<pub>[^\.]+)?\.?$", rest)
        if m:
            if not title_fixed:
                e["title"] = m.group("title").strip().rstrip(".")
            e["report_no"] = (m.group("no") or "").strip()
            e["publisher"] = (m.group("pub") or "").strip()
        elif not title_fixed:
            e["title"] = rest.strip().rstrip(".")

    elif t in ("newspaper", "web"):
        parts = re.split(r"[\.\?]\s+", rest)
        if parts and not title_fixed:
            e["title"] = parts[0].strip()
        if len(parts) > 1:
            cont = parts[1].strip().rstrip(".,")
            pgm = re.fullmatch(r"(.+?),\s*([\dA-Z\-–, ]+)", cont)
            if pgm:
                e["container"] = pgm.group(1).strip()
                e["pages"] = pgm.group(2).strip()
            else:
                e["container"] = cont

    elif t == "conference":
        m = re.match(r"(?P<title>.+?)[\.\?]\s*(?P<cont>.+)$", rest)
        if m:
            if not title_fixed:
                e["title"] = m.group("title").strip()
            e["container"] = m.group("cont").strip().rstrip(".")
            pm = re.search(r"[,\s](\d{1,5}\s*[-–]\s*\d{1,5})\s*\.?\s*$", e["container"])
            if pm:
                e["pages"] = re.sub(r"\s*[-–]\s*", "-", pm.group(1))
                e["container"] = e["container"][: pm.start()].strip().rstrip(".,")
        elif not title_fixed:
            e["title"] = rest.strip().rstrip(".")

    elif t == "law":
        e["title"] = e["raw"].split(".")[0].strip()
        m = re.search(r"(법률\s*제?\s*\d+호|Chapter\s+[A-Z0-9\-\.]+)", e["raw"])
        if m:
            e["report_no"] = m.group(1)

    elif t == "standard":
        if not title_fixed:
            parts = re.split(r"[\.\?]\s+", rest, maxsplit=1)
            e["title"] = (parts[0] if parts else rest).strip().rstrip(".")
        m = re.search(r"\(([^)]*(?:NAK|ISO|KS|IEC)[^)]*)\)", e["raw"])
        if m:
            e["report_no"] = m.group(1)

    else:  # unknown
        if not title_fixed:
            parts = re.split(r"[\.\?]\s+", rest, maxsplit=1)
            if parts and parts[0].strip():
                e["title"] = parts[0].strip().rstrip(".")
            if len(parts) > 1:
                e["container"] = parts[1].strip().rstrip(".")
        else:
            e["container"] = rest.strip().rstrip(".")

    if not e.get("title"):
        if "제목 확인 필요" not in e["notes"]:
            e["notes"].append("제목 확인 필요")
