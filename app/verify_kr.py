# -*- coding: utf-8 -*-
"""국내 문헌 검증 클라이언트 (실험적).

- KCI OpenAPI: 국내 학술지 논문 실존·서지 대조, 학술지 등재 여부
  (키 발급: https://www.kci.go.kr → Open API 신청, .env의 KCI_API_KEY)
- 국립중앙도서관 서지정보(SEOJI) API: 국문 단행본 ISBN·서지 대조
  (키 발급: https://www.nl.go.kr/seoji → 인증키 신청, .env의 NLK_CERT_KEY)
- 국회도서관 국가학술정보 API(공공데이터포털): 학위논문 등
  (키 발급: https://www.data.go.kr → '국회도서관 검색' 활용신청, .env의 NANET_API_KEY)

모든 호출은 실패 시 None을 반환하며 처리 흐름을 막지 않는다.
"""
import difflib
import re
import threading
import xml.etree.ElementTree as ET
from urllib.parse import unquote

import httpx

import http_util
from aiengine import env_get
from http_util import LookupUnavailable

_TIMEOUT = 12
_CACHE_LOCK = threading.Lock()


def _get(client: httpx.Client, url: str, params: dict) -> httpx.Response | None:
    """국내 DB GET — 재시도 포함.

    반환 None은 '질의에 맞는 자료가 없음'(4xx 등), LookupUnavailable 예외는
    '확인하지 못함'(네트워크·429·5xx)이다. 이 둘을 섞으면 실제로 존재하는
    국내 논문이 '미발견'으로 표시되므로 반드시 구분한다.
    """
    r = http_util.get_with_retry(client, url, params=params, timeout=_TIMEOUT)
    return r if r.status_code == 200 else None


def _service_key(name: str) -> str:
    """공공데이터포털 키는 인코딩 키(% 포함)일 수 있음 — httpx가 재인코딩하므로 디코딩해 전달."""
    key = env_get(name)
    if "%" in key:
        return unquote(key)
    return key


def kr_api_status() -> dict:
    return {
        "kci": bool(env_get("KCI_API_KEY")),
        "nlk": bool(env_get("NLK_CERT_KEY")),
        "nanet": bool(env_get("NANET_API_KEY")),
    }


def _norm(s: str) -> str:
    return re.sub(r"[\s::\-·,\.\?!「」『』\"'()\[\]]+", "", (s or "").lower())


def _sim(a: str, b: str) -> float:
    a, b = _norm(a), _norm(b)
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def _bare_doi(s: str) -> str:
    """'http://dx.doi.org/10.x/y' → '10.x/y'.

    KCI는 DOI를 URL 형태로 준다. Crossref 조회는 DOI를 URL 경로에 그대로 넣으므로,
    URL째 넘기면 404가 되어 철회 여부 보강이 조용히 건너뛰어진다.
    """
    return re.sub(r"^\s*(https?://)?(dx\.)?doi\.org/", "", (s or "").strip(), flags=re.I)


def _xml_root(text: str):
    try:
        return ET.fromstring(text)
    except ET.ParseError:
        return None


# ---------------------------------------------------------------- KCI

def _kci_error_msgs(root) -> list[str]:
    """KCI 응답에서 '조회 자체가 안 된' 사유만 추린다.

    KCI는 오류도 HTTP 200 + <resultMsg>로 준다. '등록되지 않은 서비스'(신청하지 않은
    apiCode), '등록되지 않은 key 입니다.'(키 폐기·IP 불일치), '필수 요청 파라미터가 없음'
    등이 여기 해당하며, 이를 결과 없음으로 읽으면 실존하는 논문이 '미발견'이 된다.

    반대로 검색 결과가 0건일 때도 <resultMsg>No Data</resultMsg>가 온다. 이것은 정상
    응답이므로 오류로 보면 진짜 미발견까지 '확인 못 함'으로 묻힌다 — 반드시 제외한다.
    """
    return [t for el in root.iter("resultMsg") if (t := (el.text or "").strip())
            and t.lower() != "no data"]


def kci_article_search(client: httpx.Client, title: str, author: str = "") -> dict | None:
    """KCI 논문 검색 → 최고 유사도 매칭. 반환: {sim, meta} 또는 None."""
    key = env_get("KCI_API_KEY")
    if not key or not title or len(title) < 4:
        return None
    params = {"apiCode": "articleSearch", "key": key,
              "title": title[:80], "displayCount": 10}
    if author:
        params["author"] = author[:40]  # API가 지원하는 검색조건 — 동명 제목의 오매칭을 줄인다
    r = _get(client, "https://open.kci.go.kr/po/openapi/openApiSearch.kci", params)
    if r is None:
        return None
    root = _xml_root(r.text)
    if root is None:
        return None
    if (errs := _kci_error_msgs(root)):
        # 키·서비스 문제를 '미발견'으로 보고하면 실존 논문이 허위로 표시된다
        raise LookupUnavailable(f"KCI articleSearch: {'; '.join(errs)}")

    best, best_sim = None, 0.0
    for rec in root.iter("record"):
        def g(*paths):
            for p in paths:
                el = rec.find(p)
                if el is not None and (el.text or "").strip():
                    return el.text.strip()
            return ""
        # 첫 <article-title>은 lang="original"(국문). 영문 제목은 lang="english"로 따로 온다.
        art_title = g(".//article-title", ".//articleTitle", ".//title")
        sim = _sim(title, art_title)
        if sim > best_sim:
            authors = [a.text.strip() for a in rec.iter("author") if a.text and a.text.strip()]
            # 저자가 KCI에 등록한 공식 영문 표기. 영문화 목록을 지어내지 않고 이것을 쓴다.
            authors_en = [a.get("english", "").strip() for a in rec.iter("author")
                          if a.get("english", "").strip()]
            title_en = next((t.text.strip() for t in rec.iter("article-title")
                             if t.get("lang") == "english" and (t.text or "").strip()), "")
            best_sim = sim
            best = {
                "title": art_title,
                "authors": authors,
                "title_en": title_en,
                "authors_en": authors_en,
                "container": g(".//journal-name", ".//journalName"),
                "year": re.sub(r"\D", "", g(".//pub-year", ".//pubYear", ".//issue-date"))[:4],
                "volume": g(".//volume"),
                "issue": g(".//issue"),
                "pages": "-".join(x for x in (g(".//fpage"), g(".//lpage")) if x),
                "doi": _bare_doi(g(".//doi")),
                "uci": g(".//uci", ".//UCI"),
                "source": "KCI",
                # articleDetail 조회용 Control Number(<articleInfo article-id="ART…">)
                "kci_id": (ai.get("article-id") or "") if (ai := rec.find(".//articleInfo")) is not None else "",
            }
    if best and best_sim >= 0.80:
        best["sim"] = best_sim
        return best
    return None


_KCI_DETAIL_CACHE: dict[str, dict] = {}


def kci_article_detail(client: httpx.Client, article_id: str) -> dict | None:
    """articleDetail — Control Number(ART…)로 상세 서지 조회.

    검색 결과에는 없는 <kci-registration>(학술지 등재 구분)·ISSN·페이지를 준다.
    학술지 등재 여부는 journalSearch로도 얻을 수 있으나 그쪽은 별도 신청 항목이므로,
    이미 승인된 articleDetail로 대신한다.
    """
    key = env_get("KCI_API_KEY")
    if not key or not article_id:
        return None
    with _CACHE_LOCK:
        if article_id in _KCI_DETAIL_CACHE:
            return _KCI_DETAIL_CACHE[article_id]
    r = _get(client, "https://open.kci.go.kr/po/openapi/openApiSearch.kci",
             {"apiCode": "articleDetail", "key": key, "id": article_id})
    if r is None:
        return None
    root = _xml_root(r.text)
    if root is None:
        return None
    if (errs := _kci_error_msgs(root)):
        raise LookupUnavailable(f"KCI articleDetail: {'; '.join(errs)}")

    def g(*paths):
        for p in paths:
            el = root.find(p)
            if el is not None and (el.text or "").strip():
                return el.text.strip()
        return ""

    out = {
        "kci_registration": g(".//kci-registration"),
        "issn": g(".//issn"),
        "container": g(".//journal-name"),
        "doi": _bare_doi(g(".//doi")),
        "pages": "-".join(x for x in (g(".//fpage"), g(".//lpage")) if x),
    }
    if not any(out.values()):
        return None
    with _CACHE_LOCK:
        _KCI_DETAIL_CACHE[article_id] = out
    return out


# KCI 참고문헌 레코드의 자료유형 코드(2026-08 실측) → 이 앱의 유형 코드
_KCI_REF_TYPE = {
    "01": "journal",   # 학술지(정기간행물)
    "02": "conference",  # 학술대회논문
    "03": "book",      # 단행본
    "05": "thesis",    # 학위논문
    "06": "web",       # 인터넷자원
}


def kci_article_references(client: httpx.Client, article_id: str) -> list[dict] | None:
    """articleDetail의 <referenceInfo> — 그 논문이 실제로 인용한 참고문헌 목록.

    referenceSearch와 혼동하면 안 된다. referenceSearch는 '이 논문이 남의 논문에
    인용된 형태'를 주므로(2026-08 실측: 같은 논문이 표기만 달리해 여러 건) 발행본의
    참고문헌으로 쓸 수 없다. 논문 자신의 참고문헌은 여기에만 있다.

    한계: 레코드에 **제1저자만** 담긴다(4인 공저도 한 명만). 저자 대조에 쓰면
    모든 항목이 '저자 누락'으로 잡히므로 호출하는 쪽에서 저자를 빼고 비교해야 한다.

    반환: 서지요소 dict 목록(형식 변환은 formatter가 한다).
    """
    key = env_get("KCI_API_KEY")
    if not key or not article_id:
        return None
    r = _get(client, "https://open.kci.go.kr/po/openapi/openApiSearch.kci",
             {"apiCode": "articleDetail", "key": key, "id": article_id})
    if r is None:
        return None
    root = _xml_root(r.text)
    if root is None:
        return None
    if (errs := _kci_error_msgs(root)):
        raise LookupUnavailable(f"KCI articleDetail: {'; '.join(errs)}")

    out: list[dict] = []
    for ref in root.iter("reference"):
        f = {ch.tag: (ch.text or "").strip() for ch in ref}
        title = f.get("title", "")
        if not title:
            continue
        etype = _KCI_REF_TYPE.get(ref.get("type-code", ""), "unknown")
        author = f.get("author", "")
        entry = {
            "type": etype,
            # 한글이 있으면 국내문헌 — 배열·형식이 갈린다
            "lang": "ko" if re.search(r"[가-힣]", title + author) else "west",
            "title": title,
            "authors": [author] if author else [],
            "year": re.sub(r"\D", "", f.get("pubi-year", "")
                           or f.get("registration-day", ""))[:4],
            # KCI 스키마의 오타를 그대로 따른다(isseue·pubilisher)
            "container": f.get("journal-name") or f.get("conference-name") or f.get("site-name", ""),
            "volume": f.get("volume", ""),
            "issue": f.get("isseue", ""),
            "pages": f.get("page", ""),
            "doi": _bare_doi(f.get("doi", "")),
            "url": f.get("url", ""),
            "publisher": f.get("pubilisher", ""),
            "degree": f.get("degree", ""),
            "institution": f.get("university", ""),
        }
        out.append(entry)
    return out


def kci_reference_search(client: httpx.Client, title: str, author: str = "",
                         year: str = "") -> list[str] | None:
    """KCI referenceSearch — 해당 논문에 실린 참고문헌 목록을 문자열로 반환.

    발행본 PDF 없이도 KCI에 등재된 논문의 참고문헌을 가져와 3단 비교에 쓸 수 있다.
    응답 XML의 태그 구성이 문서에 명시돼 있지 않아, 참고문헌 성격의 요소를 폭넓게 수집한다.
    """
    key = env_get("KCI_API_KEY")
    if not key or not title or len(title) < 4:
        return None
    params = {"apiCode": "referenceSearch", "key": key, "title": title[:80]}
    if author:
        params["author"] = author[:40]
    if year and re.fullmatch(r"\d{4}", str(year)):
        params["pubiYr"] = str(year)
    r = _get(client, "https://open.kci.go.kr/po/openapi/openApiSearch.kci", params)
    if r is None:
        return None
    root = _xml_root(r.text)
    if root is None:
        return None
    if (errs := _kci_error_msgs(root)):
        raise LookupUnavailable(f"KCI referenceSearch: {'; '.join(errs)}")

    refs: list[str] = []
    for el in root.iter():
        tag = el.tag.lower()
        # 참고문헌 원문이 한 요소에 통째로 담긴 경우. 실제 응답은
        # <record article-id="...">저자(연도). 제목. 학술지…</record>처럼 요소의 자체
        # 텍스트에 원문이 들어오므로, record를 빼면 한 건도 얻지 못한다
        if tag.endswith(("reference", "reference-text", "referencetext",
                         "citation", "org-reference", "record")) and (el.text or "").strip():
            refs.append(re.sub(r"\s+", " ", el.text).strip())
    if not refs:
        # 서지요소가 나뉘어 온 경우 — 하위 필드를 조합해 한 건씩 만든다
        for rec in root.iter():
            if not rec.tag.lower().endswith(("reference", "record")):
                continue
            parts = [re.sub(r"\s+", " ", (c.text or "").strip())
                     for c in rec if (c.text or "").strip()]
            line = " ".join(parts).strip()
            if len(line) >= 15:
                refs.append(line)
    # 중복 제거(순서 유지)
    seen, out = set(), []
    for x in refs:
        if len(x) >= 15 and x not in seen:
            seen.add(x)
            out.append(x)
    return out or None


_KCI_JOURNAL_CACHE: dict[str, str] = {}
# journalSearch는 인증키에 승인된 apiCode가 아니면 매번 '등록되지 않은 서비스'로
# 돌아온다. 실패('')는 캐시하지 않으므로 그대로 두면 등재구분 없는 국내 문헌마다
# KCI 쿼터만 소모하는 헛호출이 반복된다 — 미승인이 확인되면 프로세스가 사는 동안
# 호출을 멈춘다. (나중에 KCI에 journalSearch를 추가 신청·승인받으면 재시작만 하면 됨)
_JOURNAL_SEARCH_OFF = False


def kci_journal_status(client: httpx.Client, journal_name: str) -> str:
    """학술지의 KCI 조회 결과: 'listed'(검색됨) / 'unlisted'(미검색) / ''(조회 불가)."""
    global _JOURNAL_SEARCH_OFF
    key = env_get("KCI_API_KEY")
    if not key or not journal_name or _JOURNAL_SEARCH_OFF:
        return ""
    jn = journal_name.strip()
    with _CACHE_LOCK:
        if jn in _KCI_JOURNAL_CACHE:
            return _KCI_JOURNAL_CACHE[jn]
    result = ""
    try:
        # 학술지 신뢰도는 부가 정보이므로, 조회 실패는 예외로 올리지 않고 ''(조회 불가)로 둔다
        # 검색어 파라미터는 title — journalName은 API가 무시한다(inputData에 되돌아오지 않음)
        r = _get(client, "https://open.kci.go.kr/po/openapi/openApiSearch.kci",
                 {"apiCode": "journalSearch", "key": key, "title": jn[:60]})
        if r is not None:
            root = _xml_root(r.text)
            if root is not None:
                errs = _kci_error_msgs(root)
                if any("등록되지 않은 서비스" in e for e in errs):
                    _JOURNAL_SEARCH_OFF = True
                    print("[KCI] journalSearch 미승인 apiCode — 이후 호출 생략(등재 확인은 articleDetail 값만 사용)")
                elif not errs:
                    names = [el.text.strip() for el in root.iter() if el.tag.lower().endswith("journalname")
                             and el.text and el.text.strip()]
                    if not names:
                        names = [el.text.strip() for el in root.iter("journal-name") if el.text]
                    result = "listed" if any(_sim(jn, n) >= 0.85 for n in names) else "unlisted"
    except LookupUnavailable:
        result = ""
    if result:  # 조회 실패('')는 캐시하지 않음 — 다음 기회에 재시도
        with _CACHE_LOCK:
            _KCI_JOURNAL_CACHE[jn] = result
    return result


# ---------------------------------------------------------------- 국립중앙도서관(단행본)

_NLK_URL = "https://www.nl.go.kr/seoji/SearchApi.do"


def _nlk_docs(client: httpx.Client, params: dict) -> list[dict] | None:
    """SEOJI 호출 → docs 목록. 오류 응답은 LookupUnavailable로 올린다.

    SEOJI도 KCI처럼 오류를 HTTP 200으로 준다(2026-08 실측).
        {"RESULT":"ERROR","ERR_CODE":"011","ERR_MESSAGE":"유효하지 않은 인증키 값입니다."}
    이때 docs 키가 아예 없으므로, 그냥 읽으면 '자료 없음'과 구별되지 않아
    키가 죽은 동안 실존 단행본이 전부 '미발견'으로 표시된다.
    자료가 0건일 때는 정상 응답으로 {"TOTAL_COUNT":"0", ..., "docs":[]}가 온다.
    """
    r = _get(client, _NLK_URL, params)
    if r is None:
        return None
    try:
        j = r.json() or {}
    except ValueError:  # 점검 페이지 등 — 조회 못 한 것이지 자료가 없는 게 아니다
        raise LookupUnavailable("SEOJI: 응답이 JSON이 아님")
    if str(j.get("RESULT", "")).upper() == "ERROR":
        raise LookupUnavailable(
            f"SEOJI {j.get('ERR_CODE', '')}: {j.get('ERR_MESSAGE', '')}".strip())
    return j.get("docs") or []


def _nlk_main_title(t: str) -> str:
    """등록 서명에서 대역서명·판차 부기·부제를 떼어낸 주서명."""
    t = re.sub(r"\s+", " ", (t or "")).strip()
    t = t.split(" = ")[0]                        # '국문서명 = English title'
    t = re.sub(r"[(（\[].*?[)）\]]", " ", t)       # '(전면개정판)' 등 부기
    t = re.split(r"\s*[:：]\s*", t)[0]            # 부제
    return re.sub(r"\s+", " ", t).strip(" .,:;-–—")


def _nlk_queries(title: str) -> list[str]:
    """검색어 후보 — 넓은 것 순으로 최대 3개.

    SEOJI의 title 검색은 등록 서명에 검색어가 통째로 들어 있어야 걸리는
    부분 문자열 방식이다(2026-08 실측: '서비스론' 85건, '정보서비스론' 11건).
    그래서 원고 서명이 등록 서명보다 길면 — 부제·판차 부기가 붙었거나 띄어쓰기
    표기가 다르면 — 실존 단행본도 0건이 된다
    ('정보서비스론: 이론과 실제' 0건 / '정보서비스론' 11건).
    걸릴 때까지 줄여 재시도하되, 넓힌 검색어로 얻은 후보도 판정은 원 서명과의
    유사도로 하므로 엉뚱한 책이 통과하지는 않는다.
    """
    out: list[str] = []
    for cand in (title, _nlk_main_title(title)):
        c = re.sub(r"\s+", " ", (cand or "")).strip()
        if len(_norm(c)) >= 3 and c not in out:
            out.append(c)
    words = out[-1].split() if out else []
    for n in (3, 2):                              # 앞 n어절까지 축약
        if len(words) > n:
            c = " ".join(words[:n])
            # 너무 짧은 검색어는 후보만 폭증시키고 정답을 밀어낸다
            if len(_norm(c)) >= 6 and c not in out:
                out.append(c)
    return out[:3]


def nlk_book_by_isbn(client: httpx.Client, isbn: str) -> dict | None:
    """SEOJI ISBN 직접 조회 — 단건 검증(quick)·미매칭 재조회용.

    제목 검색과 달리 ISBN은 유일키라 유사도 판정이 필요 없다.
    붙임표·공백이 섞인 입력을 받으므로 숫자만 남겨 보낸다.
    """
    key = env_get("NLK_CERT_KEY")
    isbn = re.sub(r"[^0-9Xx]", "", isbn or "")
    if not key or len(isbn) not in (10, 13):
        return None
    docs = _nlk_docs(client, {"cert_key": key, "result_style": "json",
                              "page_no": 1, "page_size": 5, "isbn": isbn})
    if not docs:
        return None
    d = docs[0]
    year = re.sub(r"\D", "", (d.get("PUBLISH_PREDATE") or "")
                  or (d.get("REAL_PUBLISH_DATE") or ""))[:4]
    return {
        "title": (d.get("TITLE") or "").strip(),
        "authors": [d.get("AUTHOR") or ""],
        "publisher": d.get("PUBLISHER") or "", "year": year,
        "isbn": (d.get("EA_ISBN") or "") or (d.get("SET_ISBN") or ""),
        "source": "국립중앙도서관", "sim": 1.0,
    }


def nlk_book_search(client: httpx.Client, title: str, author: str = "",
                    year: str = "") -> dict | None:
    key = env_get("NLK_CERT_KEY")
    if not key or not title or len(title) < 3:
        return None
    want_year = re.sub(r"\D", "", year or "")[:4]
    # 저자는 AND 조건이며 부분 일치다. 맞으면 동명 서명의 오매칭을 줄여 주지만
    # 표기가 다르면 실존 단행본도 0건이 되므로, 결과가 없으면 저자를 빼고 다시 본다.
    au = re.sub(r"\s+", " ", (author or "")).strip()
    if len(au) > 20 or re.search(r"\d", au):
        au = ""

    q_main = _nlk_main_title(title)
    use_main = len(_norm(q_main)) >= 6          # 주서명이 너무 짧으면 오매칭 위험

    def pick(docs: list[dict]) -> tuple[dict | None, float]:
        best, best_key = None, (0.0, 0)
        for d in docs:
            raw = (d.get("TITLE") or "").strip()
            sim = _sim(title, raw.split(" = ")[0])
            if use_main:
                # 원고에만 부제가 붙은 경우를 살린다 — 주서명끼리도 대조
                sim = max(sim, _sim(q_main, _nlk_main_title(raw)))
            # 발행예정일이 비어 있는 자료가 있어 실제 발행일로 보완한다
            d_year = re.sub(r"\D", "", (d.get("PUBLISH_PREDATE") or "")
                            or (d.get("REAL_PUBLISH_DATE") or ""))[:4]
            # 같은 서명의 판이 여럿이면(예: '디지털도서관 운영론' 2008·2026)
            # 제목 유사도만으로는 구분되지 않는다. 원고 연도와 맞는 판을 골라야
            # 맞게 쓴 발행연도를 다른 판의 연도로 '교정'하라고 하지 않는다.
            key = (round(sim, 3), 1 if want_year and d_year == want_year else 0)
            if key > best_key:
                best_key = key
                best = {
                    "title": raw, "authors": [d.get("AUTHOR") or ""],
                    "publisher": d.get("PUBLISHER") or "", "year": d_year,
                    "isbn": (d.get("EA_ISBN") or "") or (d.get("SET_ISBN") or ""),
                    "source": "국립중앙도서관",
                }
        return best, best_key[0]

    for i, q in enumerate(_nlk_queries(title)):
        params = {"cert_key": key, "result_style": "json", "page_no": 1,
                  "page_size": 20, "title": q[:60]}
        if au and i == 0:
            params["author"] = au
        docs = _nlk_docs(client, params)
        if docs is None:
            return None
        if not docs and au and i == 0:          # 저자 표기 차이 — 저자를 빼고 재시도
            docs = _nlk_docs(client, {k: v for k, v in params.items() if k != "author"})
            if docs is None:
                return None
        best, best_sim = pick(docs)
        if best and best_sim >= 0.80:
            best["sim"] = best_sim
            return best
    return None


# ---------------------------------------------------------------- 국회도서관(학위논문 등)

def _nanet_clean(v: str) -> str:
    """국회도서관 응답값에서 검색어 강조용 HTML을 걷어낸다.

    검색어와 겹치는 글자에 태그를 입혀서 준다(2026-08 실측):
        발행자: 숭의여자대<font color="red">학교</font> 문헌정보과
    XML상에는 이스케이프돼 있어 파서를 통과하므로, 그대로 두면 태그가 붙은 채로
    화면과 교정 제안에까지 흘러간다.
    """
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", v or "")).strip()


def nanet_search(client: httpx.Client, title: str, year: str = "") -> dict | None:
    key = _service_key("NANET_API_KEY")
    if not key or not title or len(title) < 4:
        return None
    want_year = re.sub(r"\D", "", year or "")[:4]
    # 검색식이 '필드,검색어' 형식이므로 제목의 콤마는 공백으로 치환
    q_title = title[:60].replace(",", " ").strip()
    # 검색항목은 '전체'가 아니라 '자료명'을 쓴다. '전체'는 느슨하게 매칭되어
    # 짧은 서명일수록 수만 건이 걸리고 정답이 상위 10건에 들지 못한다
    # (예: '참고정보서비스론' → 전체 58,024건·매칭 실패 / 자료명 3건·매칭 성공).
    r = _get(client, "https://apis.data.go.kr/9720000/searchservice/basic",
             {"serviceKey": key, "pageno": 1, "displaylines": 10,
              "search": f"자료명,{q_title}"})
    if r is None:
        return None
    root = _xml_root(r.text)
    if root is None:
        return None
    best, best_key = None, (0.0, 0)
    for rec in root.iter("recode"):  # 국회도서관 API의 실제 태그명(recode)
        fields = {}
        for item in rec.iter("item"):
            name = _nanet_clean(item.findtext("name") or "")
            value = _nanet_clean(item.findtext("value") or "")
            if name:
                fields[name] = value
        # 제목 필드명이 자료 유형마다 다르다(2026-08 실측): 학술기사 '기사명',
        # 학위논문 '논문명', 도서 '자료명'. 도서만 걸리던 기존 코드로는
        # 기사·학위논문이 전부 매칭 실패했다.
        t = (fields.get("기사명") or fields.get("논문명")
             or fields.get("자료명") or fields.get("서명") or "")
        # '국문제목 = English title' 대역 제목과 도서의 저자사항 구분자 '/'를 떼어낸다
        t = t.split(" = ")[0].split(" /")[0].strip().rstrip("/").strip()
        sim = _sim(title, t)
        d_year = re.sub(r"\D", "", fields.get("발행년도", "")
                        or fields.get("학위년도", "")
                        or fields.get("발행년", ""))[:4]
        # 같은 제목의 자료가 여러 건일 때 원고 연도에 맞는 것을 고른다. 학위논문이
        # 학술기사로도 실리면 제목이 100% 같은 레코드가 여러 해에 걸쳐 나오는데
        # (변회균 논문: 2014년 학회지 · 2017년 연구지), 앞의 것을 집으면 맞게 쓴
        # 발행연도를 다른 자료의 연도로 '교정'하라고 하게 된다.
        key = (round(sim, 3), 1 if want_year and d_year == want_year else 0)
        if key > best_key:
            best_key = key
            best = {"title": t, "authors": [fields.get("저자명", "")],
                    "year": d_year, "publisher": fields.get("발행자", ""),
                    "source": "국회도서관"}
    if best and best_key[0] >= 0.80:
        best["sim"] = best_key[0]
        return best
    return None
