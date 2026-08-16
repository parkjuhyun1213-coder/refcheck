# -*- coding: utf-8 -*-
"""실존·윤리 검증 엔진.

검증 소스(전부 무료 API):
- Crossref: DOI 조회·서지 검색 + 철회/정정/우려표명(updated-by) + 프리프린트 관계
- OpenAlex, DataCite, Semantic Scholar: Crossref 실패 시 폴백 체인
- DOAJ / OpenAlex source / KCI: 학술지 신뢰성(등재 여부) 확인
- KCI·국립중앙도서관·국회도서관(verify_kr): 국내 문헌 검증(키 설정 시)
- URL 생존 확인

결과 dict:
{status, detail, found_doi, source, retraction, journal, preprint, meta}
- status: verified|mismatch|not_found|suspect|link_ok|link_dead|skipped
- retraction: {type, label, date} | None
- journal: {flag: ok|warn|unknown, detail} | None
- preprint: {published_doi, detail} | None
- meta: 매칭된 문헌의 정규 서지(교정 제안용) | None
"""
import difflib
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import quote

import httpx

import http_util
import verify_kr
from http_util import LookupUnavailable  # 재수출 — 기존 verify.LookupUnavailable 참조 유지

_HEADERS = {"User-Agent": "RefStd-Agent/2.0 (mailto:park51566@jnu.ac.kr)"}
_TIMEOUT = 12
_CACHE_LOCK = threading.Lock()


def _get_with_retry(client: httpx.Client, url: str, *, params=None) -> httpx.Response:
    """GET + 429/5xx 재시도. 최종 실패 시 LookupUnavailable. (공통 규약은 http_util 참조)"""
    return http_util.get_with_retry(client, url, params=params,
                                    headers=_HEADERS, timeout=_TIMEOUT)

_PREPRINT_DOI_PREFIX = {
    "10.48550": "arXiv", "10.2139": "SSRN", "10.1101": "bioRxiv/medRxiv",
    "10.31219": "OSF", "10.20944": "Preprints.org", "10.21203": "Research Square",
    "10.31235": "SocArXiv",
}
_UPDATE_LABEL = {
    "retraction": "철회(Retraction)", "retracted": "철회(Retraction)",
    "withdrawal": "철회(Withdrawal)", "removal": "삭제(Removal)",
    "correction": "정정(Correction)", "corrigendum": "정정(Corrigendum)",
    "erratum": "정정(Erratum)",
    "expression_of_concern": "우려표명(Expression of Concern)",
    "new_edition": "개정판", "new_version": "새 버전",
}
_SEVERE_UPDATES = {"retraction", "retracted", "withdrawal", "removal"}


def _norm_title(s: str) -> str:
    # 한글·한자·가나·키릴·그리스 등 주요 문자 유지(비수록 문자로 제목 전체가 사라지는 것 방지)
    s = re.sub(r"[^0-9A-Za-z가-힣一-鿿぀-ゟ゠-ヿ・ー０-９Ａ-Ｚａ-ｚЀ-ӿΑ-Ωα-ω]+", " ",
               (s or "").lower())
    return re.sub(r"\s+", " ", s).strip()


def _similarity(a: str, b: str) -> float:
    na, nb = _norm_title(a), _norm_title(b)
    if not na or not nb:
        # 정규화로 전부 사라진 문자 체계 — 원문 기준으로 폴백 비교
        ra = re.sub(r"\s+", "", (a or "").casefold())
        rb = re.sub(r"\s+", "", (b or "").casefold())
        if not ra or not rb:
            return 0.0
        return difflib.SequenceMatcher(None, ra, rb).ratio()
    return difflib.SequenceMatcher(None, na, nb).ratio()


def _crossref_titles(m: dict) -> list[str]:
    """Crossref title (+subtitle 결합) 비교 후보들."""
    title = " ".join(m.get("title") or [])
    subtitle = " ".join(m.get("subtitle") or [])
    variants = [title]
    if subtitle:
        variants.append(f"{title}: {subtitle}")
    return [v for v in variants if v]


def _best_sim(entry_title: str, m: dict) -> float:
    return max((_similarity(entry_title, v) for v in _crossref_titles(m)), default=0.0)


def _base_result() -> dict:
    return {"status": "skipped", "detail": "", "found_doi": "", "source": "",
            "retraction": None, "journal": None, "preprint": None, "meta": None}


# ================================================================ 소스별 클라이언트

def _crossref_by_doi(client: httpx.Client, doi: str) -> dict | None:
    try:
        r = _get_with_retry(client, f"https://api.crossref.org/works/{quote(doi, safe='')}")
        if r.status_code == 200:
            return r.json().get("message")
    except ValueError:
        pass
    return None


def _crossref_search(client: httpx.Client, entry: dict, ignore_year: bool = False,
                     threshold: float = 0.82) -> dict | None:
    title = entry.get("title", "")
    if not title or len(title) < 8:
        return None
    authors = entry.get("authors") or []
    q = title + (" " + authors[0] if authors else "")
    try:
        r = _get_with_retry(client, "https://api.crossref.org/works",
                            params={"query.bibliographic": q, "rows": 5})
        if r.status_code != 200:
            return None
        items = r.json().get("message", {}).get("items", [])
    except ValueError:
        return None
    year_m = re.match(r"(\d{4})", entry.get("year") or "")
    want_year = int(year_m.group(1)) if year_m else None
    want_vol = (entry.get("volume") or "").strip()
    want_iss = (entry.get("issue") or "").strip()
    want_fp = re.match(r"\d+", (entry.get("pages") or "").strip())
    want_fp = want_fp.group(0) if want_fp else ""

    def _anchors(it) -> tuple[int, int]:
        """(대조 가능한 권·호·면수 개수, 일치 개수) — 동일 제목 서신·정정과 원 논문 구별용."""
        anchors = hits = 0
        if want_vol:
            anchors += 1
            hits += (it.get("volume") or "") == want_vol
        if want_iss:
            anchors += 1
            hits += (it.get("issue") or "") == want_iss
        if want_fp:
            anchors += 1
            it_fp = re.match(r"\d+", (it.get("page") or ""))
            hits += bool(it_fp) and it_fp.group(0) == want_fp
        return anchors, hits

    best, best_score, best_sim = None, 0.0, 0.0
    for it in items:
        sim = _best_sim(title, it)
        if sim < threshold:
            continue
        if want_year and not ignore_year:
            parts = (it.get("issued") or {}).get("date-parts") or [[None]]
            it_year = parts[0][0]
            if it_year and abs(int(it_year) - want_year) > 1:
                continue
        anchors, hits = _anchors(it)
        if ignore_year and anchors >= 2 and hits == 0:
            continue  # 연도도 다르고 권·면수도 전부 다르면 다른 문서로 간주
        score = sim + 0.05 * hits
        if score > best_score:
            best, best_score, best_sim = it, score, sim
    if best:
        best["_sim"] = best_sim
        return best
    return None


def _openalex_by_doi(client: httpx.Client, doi: str) -> dict | None:
    try:
        r = _get_with_retry(client, f"https://api.openalex.org/works/doi:{quote(doi, safe='')}")
        if r.status_code == 200:
            return r.json()
    except ValueError:
        pass
    return None


def _openalex_search(client: httpx.Client, entry: dict) -> dict | None:
    title = entry.get("title", "")
    if not title or len(title) < 8:
        return None
    try:
        r = _get_with_retry(client, "https://api.openalex.org/works",
                            params={"search": title[:200], "per-page": 5})
        if r.status_code != 200:
            return None
        items = r.json().get("results", [])
    except ValueError:
        return None
    year_m = re.match(r"(\d{4})", entry.get("year") or "")
    want_year = int(year_m.group(1)) if year_m else None
    best, best_sim = None, 0.0
    for it in items:
        sim = _similarity(title, it.get("title") or "")
        if want_year and it.get("publication_year") and abs(it["publication_year"] - want_year) > 1:
            continue
        if sim > best_sim:
            best, best_sim = it, sim
    if best and best_sim >= 0.85:
        best["_sim"] = best_sim
        return best
    return None


def _datacite_by_doi(client: httpx.Client, doi: str) -> dict | None:
    try:
        r = _get_with_retry(client, f"https://api.datacite.org/dois/{quote(doi, safe='')}")
        if r.status_code == 200:
            return (r.json().get("data") or {}).get("attributes")
    except ValueError:
        pass
    return None


def _s2_match(client: httpx.Client, entry: dict) -> dict | None:
    title = entry.get("title", "")
    if not title or len(title) < 8:
        return None
    try:
        r = _get_with_retry(client, "https://api.semanticscholar.org/graph/v1/paper/search/match",
                            params={"query": title[:200],
                                    "fields": "title,year,externalIds,venue"})
        if r.status_code != 200:
            return None
        data = r.json().get("data") or []
    except ValueError:
        return None
    if not data:
        return None
    best = data[0]
    sim = _similarity(title, best.get("title") or "")
    if sim >= 0.85:
        best["_sim"] = sim
        return best
    return None


# ================================================================ 메타데이터 정규화

def _meta_from_crossref(m: dict) -> dict:
    parts = (m.get("issued") or {}).get("date-parts") or [[None]]
    isbns = m.get("ISBN") or []
    # 저자는 교정 제안 대상이 아니라 단건 검증(quick)의 서지 완성용 — 화면 대조표는
    # META_FIELDS만 읽으므로 여기 실어도 기존 표시에는 영향이 없다.
    authors = []
    # 대형 공동연구는 저자가 수천 명이다 — 서지 완성 용도로는 앞쪽이면 충분하다
    for a in (m.get("author") or [])[:30]:
        if a.get("family"):
            authors.append(", ".join(x for x in (a.get("family"), a.get("given")) if x))
        elif a.get("name"):  # 단체 저자
            authors.append(a["name"])
    return {
        "title": " ".join(m.get("title") or []),
        "container": " ".join(m.get("container-title") or []),
        "year": str(parts[0][0] or ""),
        "volume": m.get("volume", "") or "",
        "issue": m.get("issue", "") or "",
        "pages": (m.get("page", "") or "").replace("--", "-"),
        "doi": m.get("DOI", "") or "",
        "publisher": m.get("publisher", "") or "",
        "isbn": (isbns[0] if isbns else ""),
        "authors": authors,
        "source": "Crossref",
    }


def _meta_from_openalex(w: dict) -> dict:
    biblio = w.get("biblio") or {}
    loc = (w.get("primary_location") or {}).get("source") or {}
    pages = ""
    if biblio.get("first_page"):
        pages = biblio["first_page"] + (f"-{biblio['last_page']}" if biblio.get("last_page") else "")
    return {
        "title": w.get("title") or "",
        "container": loc.get("display_name") or "",
        "year": str(w.get("publication_year") or ""),
        "volume": biblio.get("volume") or "",
        "issue": biblio.get("issue") or "",
        "pages": pages,
        "doi": (w.get("doi") or "").replace("https://doi.org/", ""),
        # OpenAlex의 host_organization은 학술지 발행처라 단행본 출판사와 뜻이 달라 쓰지 않는다
        "publisher": "", "isbn": "",
        "authors": [(au.get("author") or {}).get("display_name", "")
                    for au in (w.get("authorships") or [])[:30]
                    if (au.get("author") or {}).get("display_name")],
        "source": "OpenAlex",
    }


def _meta_from_kr(m: dict) -> dict:
    return {
        "title": m.get("title", ""), "container": m.get("container", ""),
        "year": m.get("year", ""), "volume": m.get("volume", ""),
        "issue": m.get("issue", ""), "pages": m.get("pages", ""),
        "doi": m.get("doi", ""),
        # 단행본은 출판사가 서지의 핵심 요소다(문편협 기준 필수 항목).
        # ISBN은 원고에 적지 않는 항목이라 교정 대상이 아니라 확인용으로만 싣는다.
        "publisher": m.get("publisher", ""), "isbn": m.get("isbn", ""),
        # KCI에 저자가 등록한 공식 영문 제목·저자명 — 영문화 목록을 지어내지 않게 한다.
        # 화면 대조표는 META_FIELDS만 읽으므로 여기 실어도 표시에 영향이 없다.
        "title_en": m.get("title_en", ""), "authors_en": m.get("authors_en") or [],
        "authors": m.get("authors") or [],
        # KCI 논문 상세 페이지 링크용 Control Number — 화면이 '근거 레코드' 링크를 만든다
        "kci_id": m.get("kci_id", ""),
        "source": m.get("source", ""),
    }


# ================================================================ 부가 검사

def _check_retraction(crossref_meta: dict) -> dict | None:
    """Crossref updated-by 필드로 철회·정정·우려표명 확인."""
    updates = crossref_meta.get("updated-by") or []
    if not updates:
        return None
    picked = None
    for u in updates:
        utype = (u.get("type") or "").lower().replace("-", "_")
        if utype in _SEVERE_UPDATES:
            picked = (utype, u)
            break
        if picked is None and utype in _UPDATE_LABEL:
            picked = (utype, u)
    if not picked:
        return None
    utype, u = picked
    date = ""
    parts = (u.get("updated") or {}).get("date-parts") or []
    if parts and parts[0] and parts[0][0]:
        date = "-".join(str(x) for x in parts[0])
    return {"type": utype, "label": _UPDATE_LABEL.get(utype, utype),
            "severe": utype in _SEVERE_UPDATES, "date": date}


_DOAJ_CACHE: dict[str, bool | None] = {}


def _journal_reliability(client: httpx.Client, entry: dict,
                         kci_registration: str = "") -> dict | None:
    """학술지 신뢰성: KCI(국내) / DOAJ·OpenAlex(해외) 대조."""
    if entry.get("type") != "journal":
        return None
    jname = (entry.get("container") or "").strip()
    if not jname or len(jname) < 3:
        return None

    if entry.get("lang") == "ko":
        # KCI가 논문 상세로 알려준 등재 구분이 있으면 그대로 쓴다(학술지명 유사도 추정보다 정확)
        if kci_registration:
            flag = "ok" if "등재" in kci_registration else "warn"
            return {"flag": flag, "detail": f"KCI {kci_registration} 학술지"}
        st = verify_kr.kci_journal_status(client, jname)
        if st == "listed":
            return {"flag": "ok", "detail": "KCI 조회 확인 학술지"}
        if st == "unlisted":
            return {"flag": "warn", "detail": "KCI에서 학술지명 미확인 — 등재 여부 확인 권장"}
        return None  # KCI 키 없음/조회 불가

    key = jname.lower()
    with _CACHE_LOCK:
        cached = _DOAJ_CACHE.get(key, "miss")
    if cached == "miss":
        found = None
        try:
            r = _get_with_retry(client,
                                "https://doaj.org/api/search/journals/" + quote(jname, safe=""))
            if r.status_code == 200:
                results = r.json().get("results") or []
                found = any(_similarity(jname, ((it.get("bibjson") or {}).get("title") or "")) >= 0.9
                            for it in results)
        except (LookupUnavailable, ValueError):
            found = None
        if found is not None:  # 조회 실패(None)는 캐시하지 않음 — 다음 기회에 재시도
            with _CACHE_LOCK:
                _DOAJ_CACHE[key] = found
        cached = found
    if cached:
        return {"flag": "ok", "detail": "DOAJ 등재 오픈액세스 학술지"}
    return None  # DOAJ 미등재는 정상 구독지도 많으므로 경고하지 않음


def _check_preprint(client: httpx.Client, entry: dict, crossref_meta: dict | None) -> dict | None:
    """프리프린트 인용 식별 + 정식 출판본 제안."""
    doi = (entry.get("doi") or "").lower()
    url = (entry.get("url") or "").lower()
    prefix = doi.split("/")[0] if doi else ""
    server = _PREPRINT_DOI_PREFIX.get(prefix)
    if not server:
        if "arxiv.org" in url:
            server = "arXiv"
        elif "ssrn.com" in url:
            server = "SSRN"
        elif "biorxiv.org" in url or "medrxiv.org" in url:
            server = "bioRxiv/medRxiv"
    if not server:
        # Crossref subtype으로도 식별
        if crossref_meta and crossref_meta.get("subtype") == "preprint":
            server = "프리프린트"
        else:
            return None

    published_doi = ""
    # 1) Crossref relation
    if crossref_meta:
        rel = (crossref_meta.get("relation") or {}).get("is-preprint-of") or []
        for r in rel:
            if r.get("id-type") == "doi":
                published_doi = r.get("id", "")
                break
    # 2) OpenAlex 병합 레코드(정식 출판본 DOI가 canonical로 잡힘)
    if not published_doi and doi:
        try:
            w = _openalex_by_doi(client, doi)
        except LookupUnavailable:
            w = None
        if w:
            canon = (w.get("doi") or "").replace("https://doi.org/", "").lower()
            if canon and canon != doi:
                published_doi = canon
    detail = f"{server} 프리프린트 인용"
    if published_doi:
        detail += f" — 정식 출판본 존재: https://doi.org/{published_doi} 로 교체 권장"
    else:
        detail += " — 정식 출판본 발행 여부 확인 권장"
    return {"published_doi": published_doi, "detail": detail}


def _enrich_from_crossref(client: httpx.Client, result: dict, doi: str):
    """폴백 소스(OpenAlex·S2)에서 DOI를 얻은 경우 Crossref로 정규 서지·철회 여부 보강."""
    if not doi:
        return
    try:
        meta = _crossref_by_doi(client, doi)
    except LookupUnavailable:
        return
    if not meta:
        return
    if not result.get("meta"):
        result["meta"] = _meta_from_crossref(meta)
    if result.get("retraction") is None:
        result["retraction"] = _check_retraction(meta)
        if result["retraction"]:
            lab = result["retraction"]["label"]
            d = result["retraction"]["date"]
            result["detail"] += f" · ⚠ {lab}{'(' + d + ')' if d else ''} 문헌"


def _check_url(client: httpx.Client, url: str) -> tuple[str, str]:
    try:
        r = client.get(url, headers=_HEADERS, timeout=_TIMEOUT, follow_redirects=True)
        if r.status_code < 400:
            return "ok", f"링크 정상(HTTP {r.status_code})"
        return "dead", f"링크 오류(HTTP {r.status_code}) — 확인 필요"
    except Exception:  # InvalidURL 등 httpx.HTTPError 이외 예외 포함
        return "dead", "링크에 접속할 수 없음 — 확인 필요"


def _safe(fn, *args, **kw):
    """(결과, 일시오류여부) — 일시 오류를 '미발견'과 구분."""
    try:
        return fn(*args, **kw), False
    except LookupUnavailable:
        return None, True


# ================================================================ 본 검증

def _mark_lookup_failed(result: dict):
    result.update(status="skipped",
                  detail="외부 DB 일시 오류(재시도 제한) — 잠시 후 다시 검증해 주세요")


def verify_entry(client: httpx.Client, entry: dict) -> dict:
    result = _base_result()
    etype = entry.get("type", "")
    lang = entry.get("lang", "ko")
    doi = (entry.get("doi") or "").strip()
    lookup_err = False

    # ---- 1) DOI가 있는 경우: Crossref → DataCite → OpenAlex
    if doi:
        meta, e1 = _safe(_crossref_by_doi, client, doi)
        lookup_err |= e1
        if meta:
            cr_title = " ".join(meta.get("title") or [])
            sim = _best_sim(entry.get("title", ""), meta)
            kci = None
            if sim < 0.75 and lang == "ko" and entry.get("title"):
                # 국내 학술지는 Crossref에 영문 제목만 등록하는 곳이 많다. 국문 제목과
                # 대조하면 유사도가 10%대로 떨어져, 제대로 쓴 참고문헌이 '제목 불일치 ·
                # 확인 필요'로 뜬다. 같은 DOI를 KCI에서 국문 제목으로 확인되면 정상이다.
                kci, e_kci = _safe(verify_kr.kci_article_search, client,
                                   entry["title"], (entry.get("authors") or [""])[0])
                lookup_err |= e_kci
                if not (kci and kci.get("doi", "").lower() == doi.lower()):
                    kci = None
            if sim >= 0.75 or not entry.get("title"):
                result.update(status="verified", source="Crossref",
                              detail=f"DOI 확인됨 · Crossref 제목 일치({sim:.0%})",
                              meta=_meta_from_crossref(meta))
            elif kci:
                result.update(status="verified", source="KCI",
                              detail=f"DOI 확인됨 · KCI 국문 제목 일치({kci.get('sim', 0):.0%})"
                                     f" · Crossref에는 영문 제목으로 등록됨",
                              meta=_meta_from_kr(kci))
            else:
                result.update(status="mismatch", source="Crossref",
                              detail=f"DOI는 존재하나 제목 불일치({sim:.0%}) — Crossref: “{cr_title[:80]}” · 확인 필요",
                              meta=_meta_from_crossref(meta))
            result["retraction"] = _check_retraction(meta)
            if result["retraction"]:
                lab = result["retraction"]["label"]
                d = result["retraction"]["date"]
                result["detail"] += f" · ⚠ {lab}{'(' + d + ')' if d else ''} 문헌"
            result["preprint"] = _check_preprint(client, entry, meta)
            result["journal"] = _journal_reliability(client, entry)
            return result
        dc, e2 = _safe(_datacite_by_doi, client, doi)
        lookup_err |= e2
        if dc:
            titles = dc.get("titles") or [{}]
            dc_title = titles[0].get("title", "") if titles else ""
            sim = _similarity(entry.get("title", ""), dc_title)
            if entry.get("title") and dc_title and sim < 0.60:
                result.update(status="mismatch", source="DataCite",
                              detail=f"DOI는 존재하나(DataCite) 제목 불일치({sim:.0%}) — “{dc_title[:80]}” · 확인 필요")
            else:
                sim_txt = f" · 제목 일치({sim:.0%})" if entry.get("title") and dc_title else ""
                result.update(status="verified", source="DataCite",
                              detail=f"DOI 확인됨(DataCite — 데이터셋/리포지터리류){sim_txt}")
            result["preprint"] = _check_preprint(client, entry, None)
            return result
        w, e3 = _safe(_openalex_by_doi, client, doi)
        lookup_err |= e3
        if w:
            oa_title = w.get("title") or ""
            sim = _similarity(entry.get("title", ""), oa_title)
            if entry.get("title") and oa_title and sim < 0.75:
                result.update(status="mismatch", source="OpenAlex",
                              detail=f"DOI는 존재하나(OpenAlex) 제목 불일치({sim:.0%}) — “{oa_title[:80]}” · 확인 필요")
            else:
                result.update(status="verified", source="OpenAlex",
                              detail=f"DOI 확인됨(OpenAlex) · 제목 일치({sim:.0%})",
                              meta=_meta_from_openalex(w))
            result["preprint"] = _check_preprint(client, entry, None)
            return result
        if lookup_err:
            _mark_lookup_failed(result)
        else:
            result.update(status="not_found",
                          detail="DOI를 Crossref·DataCite·OpenAlex에서 찾을 수 없음 — DOI 오기 가능성, 확인 필요")
        return result

    # ---- 2) 국내 문헌: KCI(논문) / 국회도서관(학위논문) / 국립중앙도서관(단행본)
    if lang == "ko" and etype in ("journal", "thesis", "book", "report"):
        kr = None
        title = entry.get("title", "")
        authors = entry.get("authors") or []
        # 국내 DB도 해외와 같은 규약 — 일시 오류는 '미발견'이 아니라 '확인 못 함'으로 모은다
        if etype == "journal":
            kr, e_k = _safe(verify_kr.kci_article_search, client, title,
                            authors[0] if authors else "")
            lookup_err |= e_k
        elif etype == "thesis":
            kr, e_k = _safe(verify_kr.nanet_search, client, title, entry.get("year", ""))
            lookup_err |= e_k
        elif etype in ("book", "report"):
            kr, e_k = _safe(verify_kr.nlk_book_search, client, title,
                            authors[0] if authors else "", entry.get("year", ""))
            lookup_err |= e_k
            if not kr:
                kr, e_k2 = _safe(verify_kr.nanet_search, client, title,
                                 entry.get("year", ""))
                lookup_err |= e_k2
        if kr:
            # KCI 검색 결과에는 DOI·페이지·등재구분이 빠져 있어 상세 조회로 보강한다
            reg = ""
            if kr.get("kci_id"):
                detail, e_d = _safe(verify_kr.kci_article_detail, client, kr["kci_id"])
                lookup_err |= e_d
                if detail:
                    reg = detail.get("kci_registration", "")
                    for f in ("doi", "pages"):
                        if detail.get(f) and not kr.get(f):
                            kr[f] = detail[f]
            detail = f"{kr.get('source')} 대조 성공(제목 일치 {kr.get('sim', 0):.0%})"
            if kr.get("isbn"):
                # 같은 서명의 다른 판과 헷갈릴 때 이용자가 손으로 확인할 수 있는 유일한 값
                detail += f" · ISBN {kr['isbn']}"
            result.update(status="verified", source=kr.get("source", "국내DB"),
                          detail=detail, meta=_meta_from_kr(kr))
            if kr.get("doi"):
                result["found_doi"] = kr["doi"]
                _enrich_from_crossref(client, result, kr["doi"])  # 철회 여부 보강
            if etype == "journal":
                result["journal"] = _journal_reliability(client, entry, reg)
            return result
        # 국내 학술지 논문은 Crossref에도 상당수 등재 — 이어서 시도
        if etype == "journal":
            best, e_kr = _safe(_crossref_search, client, entry)
            lookup_err |= e_kr
            if best:
                result.update(status="verified", source="Crossref",
                              detail=f"Crossref 대조 성공(제목 일치 {best.get('_sim', 0):.0%})",
                              found_doi=best.get("DOI", ""), meta=_meta_from_crossref(best))
                result["retraction"] = _check_retraction(best)
                if result["retraction"]:
                    lab = result["retraction"]["label"]
                    d = result["retraction"]["date"]
                    result["detail"] += f" · ⚠ {lab}{'(' + d + ')' if d else ''} 문헌"
                result["journal"] = _journal_reliability(client, entry)
                return result
        st = verify_kr.kr_api_status()
        used = {"journal": st["kci"], "thesis": st["nanet"], "book": st["nlk"] or st["nanet"],
                "report": st["nlk"] or st["nanet"]}.get(etype, False)
        if used and lookup_err:
            # 조회 자체가 실패한 경우 — '없는 문헌'으로 오해하게 두지 않는다
            _mark_lookup_failed(result)
        elif used:
            detail = "국내 DB·Crossref에서 일치 문헌을 찾지 못함 — 서지사항 확인 필요"
            if etype in ("book", "report"):
                # 국립중앙도서관 서지정보는 ISBN이 붙은 도서만 담고 있어, 비매품
                # 기관 발간물·정부간행물은 실제로 존재해도 걸리지 않는다. 이를 알려
                # 주지 않으면 '없는 문헌'으로 오해해 멀쩡한 참고문헌을 지우게 된다.
                detail += " (ISBN 없는 비매품·기관 발간물은 국내 DB에 수록되지 않아, 실제로 존재해도 여기서는 확인되지 않습니다)"
            result.update(status="not_found", detail=detail)
        else:
            result.update(status="skipped",
                          detail="국내 문헌 — 국내 DB 검증용 API 키 미설정(관리자 설정 참조), KCI·RISS에서 확인 권장")
        if entry.get("url"):
            stt, det = _check_url(client, entry["url"])
            result["detail"] += f" · {det}"
        return result

    # ---- 3) 해외 학술지 논문: Crossref → (연도 무시 재검색) → OpenAlex → Semantic Scholar
    if etype == "journal":
        best, e1 = _safe(_crossref_search, client, entry)
        lookup_err |= e1
        if not best and not e1:
            # 연도 오기 가능성: 연도 조건 없이 고유사도 재검색(교정 제안으로 이어짐)
            best, e1b = _safe(_crossref_search, client, entry, ignore_year=True, threshold=0.93)
            lookup_err |= e1b
        if best:
            result.update(status="verified", source="Crossref",
                          detail=f"Crossref 대조 성공(제목 일치 {best.get('_sim', 0):.0%})"
                                 + (f" · DOI 발견: {best.get('DOI', '')}" if best.get("DOI") else ""),
                          found_doi=best.get("DOI", ""), meta=_meta_from_crossref(best))
            result["retraction"] = _check_retraction(best)
            if result["retraction"]:
                lab = result["retraction"]["label"]
                d = result["retraction"]["date"]
                result["detail"] += f" · ⚠ {lab}{'(' + d + ')' if d else ''} 문헌"
            result["journal"] = _journal_reliability(client, entry)
            return result
        w, e2 = _safe(_openalex_search, client, entry)
        lookup_err |= e2
        if w:
            oa_doi = (w.get("doi") or "").replace("https://doi.org/", "")
            result.update(status="verified", source="OpenAlex",
                          detail=f"OpenAlex 대조 성공(제목 일치 {w.get('_sim', 0):.0%})"
                                 + (f" · DOI 발견: {oa_doi}" if oa_doi else ""),
                          found_doi=oa_doi, meta=_meta_from_openalex(w))
            _enrich_from_crossref(client, result, oa_doi)
            result["journal"] = _journal_reliability(client, entry)
            return result
        s2, e3 = _safe(_s2_match, client, entry)
        lookup_err |= e3
        if s2:
            s2_doi = ((s2.get("externalIds") or {}).get("DOI") or "")
            result.update(status="verified", source="Semantic Scholar",
                          detail=f"Semantic Scholar 대조 성공(제목 일치 {s2.get('_sim', 0):.0%})"
                                 + (f" · DOI 발견: {s2_doi}" if s2_doi else ""),
                          found_doi=s2_doi)
            _enrich_from_crossref(client, result, s2_doi)
            return result
        if lookup_err:
            _mark_lookup_failed(result)  # 일시 오류를 '실존 의심'으로 오판하지 않음
        elif entry.get("lang") == "west":
            result.update(status="suspect",
                          detail="Crossref·OpenAlex·Semantic Scholar 3개 DB 모두 미발견 — "
                                 "실존 의심(AI 생성 인용·서지 오류 가능성), 반드시 확인 필요")
        else:
            result.update(status="skipped", detail="다중 DB 미발견 — 원문 DB에서 확인 권장")
        return result

    # ---- 4) URL만 있는 자료
    url = (entry.get("url") or "").strip()
    if url:
        st, detail = _check_url(client, url)
        result.update(status="link_ok" if st == "ok" else "link_dead", detail=detail)
        return result

    result.update(detail="검증 대상 아님(오프라인 자료)")
    return result


def verify_entries(entries: list[dict], progress_cb=None) -> list[dict]:
    results: list[dict | None] = [None] * len(entries)
    with httpx.Client() as client:
        with ThreadPoolExecutor(max_workers=6) as pool:
            futures = {pool.submit(verify_entry, client, e): i for i, e in enumerate(entries)}
            done = 0
            for fut in as_completed(futures):
                i = futures[fut]
                try:
                    results[i] = fut.result()
                except Exception as ex:
                    r = _base_result()
                    r["detail"] = f"검증 중 오류: {ex}"
                    results[i] = r
                done += 1
                if progress_cb:
                    progress_cb(done, len(entries))
    return [r or _base_result() for r in results]
