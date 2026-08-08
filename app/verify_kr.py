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

from aiengine import env_get

_TIMEOUT = 12
_CACHE_LOCK = threading.Lock()


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


def _xml_root(text: str):
    try:
        return ET.fromstring(text)
    except ET.ParseError:
        return None


# ---------------------------------------------------------------- KCI

def kci_article_search(client: httpx.Client, title: str, author: str = "") -> dict | None:
    """KCI 논문 검색 → 최고 유사도 매칭. 반환: {sim, meta} 또는 None."""
    key = env_get("KCI_API_KEY")
    if not key or not title or len(title) < 4:
        return None
    try:
        r = client.get(
            "https://open.kci.go.kr/po/openapi/openApiSearch.kci",
            params={"apiCode": "articleSearch", "key": key,
                    "title": title[:80], "displayCount": 10},
            timeout=_TIMEOUT)
        if r.status_code != 200:
            return None
        root = _xml_root(r.text)
        if root is None:
            return None
    except httpx.HTTPError:
        return None

    best, best_sim = None, 0.0
    for rec in root.iter("record"):
        def g(*paths):
            for p in paths:
                el = rec.find(p)
                if el is not None and (el.text or "").strip():
                    return el.text.strip()
            return ""
        art_title = g(".//article-title", ".//articleTitle", ".//title")
        sim = _sim(title, art_title)
        if sim > best_sim:
            authors = [a.text.strip() for a in rec.iter("author") if a.text and a.text.strip()]
            best_sim = sim
            best = {
                "title": art_title,
                "authors": authors,
                "container": g(".//journal-name", ".//journalName"),
                "year": re.sub(r"\D", "", g(".//pub-year", ".//pubYear", ".//issue-date"))[:4],
                "volume": g(".//volume"),
                "issue": g(".//issue"),
                "pages": "-".join(x for x in (g(".//fpage"), g(".//lpage")) if x),
                "doi": g(".//doi"),
                "uci": g(".//uci", ".//UCI"),
                "source": "KCI",
            }
    if best and best_sim >= 0.80:
        best["sim"] = best_sim
        return best
    return None


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
    try:
        r = client.get("https://open.kci.go.kr/po/openapi/openApiSearch.kci",
                       params=params, timeout=_TIMEOUT)
        if r.status_code != 200:
            return None
        root = _xml_root(r.text)
        if root is None:
            return None
    except httpx.HTTPError:
        return None

    refs: list[str] = []
    for el in root.iter():
        tag = el.tag.lower()
        # 참고문헌 원문이 한 요소에 통째로 담긴 경우
        if tag.endswith(("reference", "reference-text", "referencetext",
                         "citation", "org-reference")) and (el.text or "").strip():
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


def kci_journal_status(client: httpx.Client, journal_name: str) -> str:
    """학술지의 KCI 조회 결과: 'listed'(검색됨) / 'unlisted'(미검색) / ''(조회 불가)."""
    key = env_get("KCI_API_KEY")
    if not key or not journal_name:
        return ""
    jn = journal_name.strip()
    with _CACHE_LOCK:
        if jn in _KCI_JOURNAL_CACHE:
            return _KCI_JOURNAL_CACHE[jn]
    result = ""
    try:
        r = client.get(
            "https://open.kci.go.kr/po/openapi/openApiSearch.kci",
            params={"apiCode": "journalSearch", "key": key, "journalName": jn[:60]},
            timeout=_TIMEOUT)
        if r.status_code == 200:
            root = _xml_root(r.text)
            if root is not None:
                names = [el.text.strip() for el in root.iter() if el.tag.lower().endswith("journalname")
                         and el.text and el.text.strip()]
                if not names:
                    names = [el.text.strip() for el in root.iter("journal-name") if el.text]
                result = "listed" if any(_sim(jn, n) >= 0.85 for n in names) else "unlisted"
    except httpx.HTTPError:
        result = ""
    if result:  # 조회 실패('')는 캐시하지 않음 — 다음 기회에 재시도
        with _CACHE_LOCK:
            _KCI_JOURNAL_CACHE[jn] = result
    return result


# ---------------------------------------------------------------- 국립중앙도서관(단행본)

def nlk_book_search(client: httpx.Client, title: str, author: str = "") -> dict | None:
    key = env_get("NLK_CERT_KEY")
    if not key or not title or len(title) < 3:
        return None
    try:
        r = client.get(
            "https://www.nl.go.kr/seoji/SearchApi.do",
            params={"cert_key": key, "result_style": "json", "page_no": 1,
                    "page_size": 10, "title": title[:60]},
            timeout=_TIMEOUT)
        if r.status_code != 200:
            return None
        docs = (r.json() or {}).get("docs") or []
    except (httpx.HTTPError, ValueError):
        return None
    best, best_sim = None, 0.0
    for d in docs:
        t = d.get("TITLE") or ""
        sim = _sim(title, t.split("=")[0])
        if sim > best_sim:
            best_sim = sim
            year = re.sub(r"\D", "", d.get("PUBLISH_PREDATE") or "")[:4]
            best = {
                "title": t, "authors": [d.get("AUTHOR") or ""],
                "publisher": d.get("PUBLISHER") or "", "year": year,
                "isbn": d.get("EA_ISBN") or "", "source": "국립중앙도서관",
            }
    if best and best_sim >= 0.80:
        best["sim"] = best_sim
        return best
    return None


# ---------------------------------------------------------------- 국회도서관(학위논문 등)

def nanet_search(client: httpx.Client, title: str) -> dict | None:
    key = _service_key("NANET_API_KEY")
    if not key or not title or len(title) < 4:
        return None
    # 검색식이 '필드,검색어' 형식이므로 제목의 콤마는 공백으로 치환
    q_title = title[:60].replace(",", " ").strip()
    try:
        r = client.get(
            "http://apis.data.go.kr/9720000/searchservice/basic",
            params={"serviceKey": key, "pageno": 1, "displaylines": 10,
                    "search": f"전체,{q_title}"},
            timeout=_TIMEOUT)
        if r.status_code != 200:
            return None
        root = _xml_root(r.text)
        if root is None:
            return None
    except httpx.HTTPError:
        return None
    best, best_sim = None, 0.0
    for rec in root.iter("recode"):  # 국회도서관 API의 실제 태그명(recode)
        fields = {}
        for item in rec.iter("item"):
            name = (item.findtext("name") or "").strip()
            value = (item.findtext("value") or "").strip()
            if name:
                fields[name] = value
        t = fields.get("자료명") or fields.get("서명") or ""
        sim = _sim(title, t)
        if sim > best_sim:
            best_sim = sim
            best = {"title": t, "authors": [fields.get("저자명", "")],
                    "year": re.sub(r"\D", "", fields.get("발행년도", "") or fields.get("발행년", ""))[:4],
                    "publisher": fields.get("발행자", ""), "source": "국회도서관"}
    if best and best_sim >= 0.80:
        best["sim"] = best_sim
        return best
    return None
