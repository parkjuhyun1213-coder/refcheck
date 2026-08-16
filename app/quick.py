# -*- coding: utf-8 -*-
"""단건 검증(옴니박스): DOI·ISBN·URL·제목 하나로 참고문헌 1건을 표준화·검증.

첫 화면(코드 입력 전 포함)의 '참고문헌 1건 바로 검증'과, 처리 결과에서
미매칭 항목을 DOI·ISBN으로 재조회하는 두 곳이 이 모듈을 쓴다.
외부 무료 DB만 조회하며 AI(Claude)는 호출하지 않는다 — 비용 0원.
"""
import ipaddress
import re
import socket
from urllib.parse import urljoin, urlparse

import httpx

import formatter
import verify as verify_mod
import verify_kr
from http_util import LookupUnavailable

_TIMEOUT = 10
_HEADERS = {"User-Agent": "RefStd-Agent/2.0 (mailto:park51566@jnu.ac.kr)"}

_DOI_RE = re.compile(r"\b(10\.\d{4,9}/[^\s\"'<>]+)", re.I)


# ---------------------------------------------------------------- 입력 판별

def detect(q: str) -> tuple[str, str]:
    """입력 한 줄 → ('doi'|'isbn'|'url'|'title', 정규화 값).

    판별 순서가 중요하다: DOI는 URL 형태(https://doi.org/10.…)로도 오므로
    URL보다 먼저 본다. ISBN은 붙임표·공백 제거 후 자리수로 판단한다.
    """
    q = (q or "").strip()
    m = _DOI_RE.search(q)
    if m:
        return "doi", m.group(1).rstrip(".,;)]}。")
    digits = re.sub(r"[\s\-]", "", q)
    if re.fullmatch(r"(?:ISBN[:\s]*)?97[89]\d{10}", digits, re.I):
        return "isbn", re.sub(r"(?i)^ISBN[:\s]*", "", digits)
    if re.fullmatch(r"(?:ISBN[:\s]*)?\d{9}[\dXx]", digits, re.I):
        return "isbn", re.sub(r"(?i)^ISBN[:\s]*", "", digits)
    if re.match(r"^https?://", q, re.I):
        return "url", q
    return "title", q


def _is_ko(s: str) -> bool:
    return bool(re.search(r"[가-힣]", s or ""))


def _bad_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return (ip.is_private or ip.is_loopback or ip.is_link_local
            or ip.is_reserved or ip.is_multicast or ip.is_unspecified)


def _blocked_url(url: str) -> bool:
    """공개 엔드포인트의 SSRF 방어 — 내부망으로 가는 요청을 막는다.

    IP 리터럴 검사만으로는 부족하다: 공격자 도메인이 사설 IP로 해석되거나
    ('2130706433' 같은 10진 표기·'localhost.' 뒷점 포함), 정상 도메인이
    내부 주소로 리다이렉트할 수 있다. 그래서 도메인은 실제로 해석해 모든
    결과 주소를 검사하고, 리다이렉트는 _safe_get이 홉마다 다시 이 함수를 부른다.
    """
    try:
        u = urlparse(url)
    except ValueError:
        return True
    if u.scheme not in ("http", "https"):
        return True
    host = (u.hostname or "").rstrip(".").strip("[]")
    if not host or host.lower() == "localhost":
        return True
    try:
        return _bad_ip(ipaddress.ip_address(host))
    except ValueError:
        pass
    try:  # 도메인 — 해석된 모든 주소를 검사('2130706433'도 여기서 127.0.0.1로 잡힌다)
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return True  # 해석 안 되는 호스트 — 조회할 수 없다
    for _fam, _t, _p, _c, sa in infos:
        try:
            if _bad_ip(ipaddress.ip_address(sa[0])):
                return True
        except ValueError:
            return True
    return False


def _safe_get(client: httpx.Client, url: str) -> httpx.Response | None:
    """리다이렉트를 손으로 따라가며 홉마다 목적지를 다시 검사하는 GET."""
    for _ in range(5):
        if _blocked_url(url):
            return None
        try:
            r = client.get(url, headers=_HEADERS, timeout=_TIMEOUT,
                           follow_redirects=False)
        except Exception:
            return None
        loc = r.headers.get("location", "")
        if r.status_code in (301, 302, 303, 307, 308) and loc:
            url = urljoin(url, loc)
            continue
        return r
    return None


# ---------------------------------------------------------------- 서지 완성

def _clean_authors(authors: list[str]) -> list[str]:
    """DB가 주는 저자 문자열의 군더더기 제거.

    KCI는 '이경란(충남대학교 문헌정보학과)'처럼 소속을 괄호로 붙이고,
    OpenAlex 옛 레코드는 'Wilson, T.D.; Department of …'처럼 소속이
    세미콜론 뒤에 딸려 온다(2026-08 실측). 그대로 쓰면 참고문헌에 소속이 박힌다.
    """
    out = []
    for a in authors:
        a = a.split(";")[0]
        a = re.sub(r"\s*[(（][^)）]*[)）]\s*", " ", a)
        a = re.sub(r"\s+", " ", a).strip(" ,.")
        if a and len(a) <= 60:
            out.append(a)
    return out


def _merge_meta(entry: dict, meta: dict | None):
    """검증에서 매칭된 공식 서지로 빈 필드를 채운다(있는 값은 보존)."""
    if not meta:
        return
    for f in ("title", "container", "year", "volume", "issue", "pages",
              "publisher", "doi"):
        if meta.get(f) and not entry.get(f):
            entry[f] = meta[f]
    if meta.get("authors") and not entry.get("authors"):
        entry["authors"] = _clean_authors(list(meta["authors"]))


def _guess_type(entry: dict, meta: dict | None) -> str:
    if entry.get("type") and entry["type"] != "unknown":
        return entry["type"]
    if (meta or {}).get("container") or entry.get("container"):
        return "journal"
    if (meta or {}).get("isbn") or (meta or {}).get("publisher") or entry.get("publisher"):
        return "book"
    if entry.get("url"):
        return "web"
    return "journal"


def _links(entry: dict, v: dict) -> list[dict]:
    """근거 레코드 링크 — 이용자가 직접 원본을 확인할 수 있는 주소만 싣는다."""
    out = []
    meta = v.get("meta") or {}
    doi = (entry.get("doi") or meta.get("doi") or v.get("found_doi") or "").strip()
    if doi:
        out.append({"label": "DOI 원문", "url": f"https://doi.org/{doi}"})
    if meta.get("kci_id"):
        out.append({"label": "KCI 논문 상세",
                    "url": "https://www.kci.go.kr/kciportal/ci/sereArticleSearch/"
                           f"ciSereArtiView.kci?sereArticleSearchBean.artiId={meta['kci_id']}"})
    if meta.get("isbn") and "국립중앙" in (meta.get("source") or ""):
        out.append({"label": "국립중앙도서관",
                    "url": f"https://www.nl.go.kr/NL/contents/search.do?kwd={meta['isbn']}"})
    return out


def _kr_verify_result(kr: dict, detail_prefix: str) -> dict:
    """국내 DB 매칭 dict → verify 결과 형태."""
    r = {"status": "verified", "source": kr.get("source", "국내DB"),
         "detail": detail_prefix, "found_doi": kr.get("doi", ""),
         "retraction": None, "journal": None, "preprint": None,
         "meta": verify_mod._meta_from_kr(kr)}
    if kr.get("isbn"):
        r["detail"] += f" · ISBN {kr['isbn']}"
    return r


# ---------------------------------------------------------------- 본 조회

def _lookup_url(client: httpx.Client, url: str) -> tuple[dict, dict]:
    """URL → (entry, verify결과). 학술 페이지의 citation_* 메타태그를 읽는다."""
    entry = {"type": "web", "lang": "ko", "url": url, "authors": [], "title": ""}
    if _blocked_url(url):
        return entry, {"status": "skipped", "detail": "내부망 주소는 조회하지 않습니다.",
                       "source": "", "found_doi": "", "retraction": None,
                       "journal": None, "preprint": None, "meta": None}
    r = _safe_get(client, url)
    head = r.text[:200000] if (r is not None and r.status_code < 400) else ""
    if head:
        def tag(name):
            m = re.search(
                r'<meta[^>]+(?:name|property)=["\']%s["\'][^>]+content=["\']([^"\']+)' % name,
                head, re.I) or re.search(
                r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:name|property)=["\']%s["\']' % name,
                head, re.I)
            return (m.group(1).strip() if m else "")

        doi = tag("citation_doi")
        if doi and _DOI_RE.search(doi):
            entry["doi"] = _DOI_RE.search(doi).group(1)
            entry["type"] = "journal"
        entry["title"] = tag("citation_title") or tag("og:title")
        if not entry["title"]:
            m = re.search(r"<title[^>]*>(.*?)</title>", head, re.I | re.S)
            entry["title"] = re.sub(r"\s+", " ", m.group(1)).strip()[:200] if m else ""
        authors = re.findall(
            r'<meta[^>]+name=["\']citation_author["\'][^>]+content=["\']([^"\']+)', head, re.I)
        if authors:
            entry["authors"] = authors[:10]
        for name, f in (("citation_journal_title", "container"),
                        ("citation_volume", "volume"), ("citation_issue", "issue"),
                        ("citation_publisher", "publisher")):
            v = tag(name)
            if v:
                entry[f] = v
        fp, lp = tag("citation_firstpage"), tag("citation_lastpage")
        if fp:
            entry["pages"] = fp + (f"-{lp}" if lp else "")
        y = re.match(r"(\d{4})", tag("citation_publication_date")
                     or tag("citation_date") or "")
        if y:
            entry["year"] = y.group(1)
        if entry.get("container"):
            entry["type"] = "journal"
    entry["lang"] = "ko" if _is_ko(entry.get("title", "")) else "west"
    if entry.get("doi") or (entry.get("type") == "journal" and entry.get("title")):
        # 서지 대조는 DOI·제목으로만 — url을 넘기면 verify 내부의 링크 확인이
        # 리다이렉트 재검사 없이 같은 주소를 또 요청한다(공개 엔드포인트라 막는다)
        probe = {k: v for k, v in entry.items() if k != "url"}
        return entry, verify_mod.verify_entry(client, probe)
    # 서지 메타태그가 없는 일반 웹페이지 — 위에서 받은 응답으로 링크 생존만 판정
    base = {"source": "", "found_doi": "", "retraction": None,
            "journal": None, "preprint": None, "meta": None}
    if r is not None and r.status_code < 400:
        return entry, {**base, "status": "link_ok",
                       "detail": f"링크 정상(HTTP {r.status_code})"}
    return entry, {**base, "status": "link_dead",
                   "detail": "링크에 접속할 수 없음 — 주소를 확인해 주세요"}


def quick_lookup(q: str) -> dict:
    """한 줄 입력 → 표준화·검증 결과 dict.

    반환: {ok, kind, status, source, detail, formatted, issues, links, entry, meta}
    ok는 '조회가 수행됨'이지 '실존 확인'이 아니다 — status로 구분한다.
    """
    kind, val = detect(q)
    out = {"ok": True, "kind": kind, "status": "skipped", "source": "",
           "detail": "", "formatted": "", "issues": [], "links": [],
           "entry": None, "meta": None}
    entry: dict = {"authors": [], "title": "", "lang": "ko", "type": "unknown"}
    try:
        with httpx.Client() as client:
            if kind == "doi":
                entry.update(type="journal", lang="west", doi=val)
                v = verify_mod.verify_entry(client, entry)
            elif kind == "isbn":
                kr = verify_kr.nlk_book_by_isbn(client, val)
                if kr:
                    entry.update(type="book", lang="ko")
                    v = _kr_verify_result(kr, "ISBN 대조 성공(국립중앙도서관)")
                else:
                    st = verify_kr.kr_api_status()
                    v = {"status": "not_found" if st["nlk"] else "skipped",
                         "detail": ("국립중앙도서관 서지에서 해당 ISBN을 찾지 못함 — "
                                    "해외서·ISBN 없는 자료는 수록되지 않습니다"
                                    if st["nlk"] else
                                    "국내 DB 검증용 API 키 미설정 — ISBN 조회를 할 수 없습니다"),
                         "source": "", "found_doi": "", "retraction": None,
                         "journal": None, "preprint": None, "meta": None}
                    entry.update(type="book", lang="ko")
            elif kind == "url":
                entry, v = _lookup_url(client, val)
            else:  # title
                lang = "ko" if _is_ko(val) else "west"
                entry.update(type="journal", lang=lang, title=val)
                v = verify_mod.verify_entry(client, entry)
                if lang == "ko" and v.get("status") != "verified":
                    # 학술지에서 못 찾으면 단행본·학위논문으로 이어서 본다
                    probe = {"authors": [], "title": val, "lang": "ko", "type": "book"}
                    v2 = verify_mod.verify_entry(client, probe)
                    if v2.get("status") == "verified":
                        entry, v = probe, v2
    except LookupUnavailable as ex:
        out.update(status="skipped",
                   detail=f"외부 DB 일시 오류 — 잠시 후 다시 시도해 주세요 ({ex})")
        return out

    _merge_meta(entry, v.get("meta"))
    if v.get("found_doi") and not entry.get("doi"):
        entry["doi"] = v["found_doi"]
    entry["type"] = _guess_type(entry, v.get("meta"))
    if _is_ko(entry.get("title", "")):
        entry["lang"] = "ko"
    out.update(status=v.get("status", "skipped"), source=v.get("source", ""),
               detail=v.get("detail", ""), meta=v.get("meta"),
               retraction=v.get("retraction"), links=_links(entry, v))
    # 제목을 아는 경우에만 형식을 만든다 — 미발견 DOI만 있는 상태로 만들면
    # '. (n.d.). https://doi.org/…' 같은 빈 서지가 나와 되레 혼란을 준다
    if entry.get("title"):
        out["formatted"] = formatter.format_entry(entry)
        out["issues"] = formatter.validate_entry(entry)
    out["entry"] = {k: entry.get(k, "") for k in
                    ("authors", "year", "title", "container", "volume", "issue",
                     "pages", "publisher", "doi", "url", "type", "lang")}
    return out


# ---------------------------------------------------------------- 미매칭 재조회

def verify_with_identifier(entry: dict, identifier: str) -> tuple[dict | None, str]:
    """처리 결과의 한 항목을 DOI·ISBN으로 재검증. (verify결과, 오류메시지) 반환.

    제목 검색이 실패한 항목의 수동 구제 통로다 — 식별자는 유일키라
    제목 유사도 문턱에 걸리지 않는다.
    """
    kind, val = detect(identifier)
    try:
        with httpx.Client() as client:
            if kind == "doi":
                probe = dict(entry)
                probe["doi"] = val
                v = verify_mod.verify_entry(client, probe)
                if v.get("status") in ("verified", "mismatch"):
                    # verify의 DOI 분기는 found_doi를 채우지 않는다 — 여기서 채워야
                    # 확인된 DOI가 항목(entry.doi)과 RIS·BibTeX 내보내기에 반영된다
                    if not v.get("found_doi"):
                        v["found_doi"] = val
                    return v, ""
                return None, v.get("detail") or "해당 DOI를 찾지 못했습니다."
            if kind == "isbn":
                kr = verify_kr.nlk_book_by_isbn(client, val)
                if kr:
                    v = _kr_verify_result(kr, "ISBN 대조 성공(국립중앙도서관)")
                    # DOI 경로처럼 제목을 대조한다 — 무관한 도서의 ISBN을 넣어도
                    # 경고 없이 '실존 확인'이 되면 잘못된 연도 교정까지 제안하게 된다
                    title = (entry.get("title") or "").strip()
                    kr_title = (kr.get("title") or "").split(" = ")[0]
                    sim = verify_kr._sim(title, kr_title) if title else 1.0
                    if title and sim < 0.60:
                        v["status"] = "mismatch"
                        v["detail"] = (f"ISBN 자료는 확인되나 제목 불일치({sim:.0%}) — "
                                       f"국립중앙도서관: “{kr_title[:60]}” · 확인 필요")
                    return v, ""
                if not verify_kr.kr_api_status()["nlk"]:
                    return None, "국내 DB 검증용 API 키가 설정되지 않아 ISBN 조회를 할 수 없습니다."
                return None, "국립중앙도서관 서지에서 해당 ISBN을 찾지 못했습니다."
    except LookupUnavailable as ex:
        return None, f"외부 DB 일시 오류 — 잠시 후 다시 시도해 주세요 ({ex})"
    return None, "DOI(10.…) 또는 ISBN을 입력해 주세요."
