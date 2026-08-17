# -*- coding: utf-8 -*-
"""보완 기준·작성 제안 지식베이스.

참고문헌 작성 기준은 네 층으로 체계화된다(우선순위 순).

1. 문편협 공통기준        — formatter.py / aiengine.py에 내장 (항상 적용, 일순위)
2. 관리자 추가 기준       — admin_standards.json. 문편협 공통기준에 준하는 기준으로 변환에
                            실제 반영하되, 문편협이 명시한 규정과 충돌하면 문편협을 우선하고
                            문편협이 다루지 않는 공백만 보완한다.
3. 논문 사례를 통한 제안  — suggestions.json의 "case". 4개 학회지(한국문헌정보학회지·
                            한국도서관·정보학회지·한국비블리아학회지·정보관리학회지)의
                            2025년 이후 발행 논문 참고문헌에서 추출한 실제 관행.
                            변환 결과를 바꾸지 않고 이용자에게 '제안'으로만 표시한다.
4. 발행본 검토 제안        — suggestions.json의 "prof". 에이전트 결과와 학회지 발행본의
                            차이를 관리자가 검토·채택해 축적한 제안. 역시 표시 전용.
                            (표시 이름은 '박주현 교수의 추가 제안'에서 2026-08-18 변경.)
5. ○○학회 제안            — suggestions.json의 "org". 학회 편집위원이 채택을 요청하고
                            편집위원장(또는 관리자)이 승인한 제안. 그 학회 이용자에게만
                            표시되며, 관리자는 언제든 수정·해제할 수 있다.

요청 대기열(org_requests.json): 편집위원의 채택 요청 → 위원장·관리자 승인/반려.
"""
import json
import threading
import time
import uuid
from pathlib import Path

APP_DIR = Path(__file__).parent
SUGGESTIONS_PATH = APP_DIR / "suggestions.json"
STANDARDS_PATH = APP_DIR / "admin_standards.json"
CORPUS_PATH = APP_DIR / "case_corpus.json"
REQUESTS_PATH = APP_DIR / "org_requests.json"
_LOCK = threading.Lock()

SOURCES = ("case", "prof", "org")
SOURCE_LABEL = {
    "case": "논문 사례를 통한 제안",
    "prof": "발행본 검토 제안",
    "org": "학회 제안",
}


def source_label(source: str, org: str = "") -> str:
    if source == "org" and org:
        return f"{org} 제안"
    return SOURCE_LABEL.get(source, "제안")

# 자료 유형 코드(aiengine._ENTRY_PROPS와 동일 체계). 빈 배열이면 모든 유형에 해당.
KNOWN_TYPES = ["journal", "book", "book_chapter", "thesis", "report", "newspaper",
               "web", "conference", "law", "standard", "interview", "av", "unknown"]


def _load(path: Path, default):
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return default


def _save(path: Path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


# ---------------------------------------------------------------- 작성 제안(case/prof)

def all_suggestions() -> dict:
    """{"case": [...], "prof": [...], "org": [...]}"""
    data = _load(SUGGESTIONS_PATH, {})
    return {s: data.get(s, []) for s in SOURCES}


def enabled_suggestions(org: str = "") -> list[dict]:
    """사용 설정된 제안(source·label 포함) — 파이프라인 매칭용.

    '학회 제안'(org)은 해당 학회의 원고를 처리할 때만 포함된다.
    """
    out = []
    data = all_suggestions()
    for source in SOURCES:
        for s in data[source]:
            if not s.get("enabled"):
                continue
            if source == "org" and (not org or s.get("journal") != org):
                continue
            item = dict(s)
            item["source"] = source
            item["label"] = source_label(source, s.get("journal", ""))
            out.append(item)
    return out


def add_suggestion(source: str, topic: str, types: list[str], rule: str,
                   example: str = "", evidence: str = "", journal: str = "",
                   origin: str = "manual", enabled: bool = True) -> dict:
    if source not in SOURCES:
        raise ValueError("source는 'case', 'prof', 'org' 중 하나여야 합니다.")
    rule = (rule or "").strip()
    if len(rule) < 5:
        raise ValueError("제안 내용을 5자 이상 입력해 주세요.")
    types = [t for t in (types or []) if t in KNOWN_TYPES]
    rec = {
        "id": "sg_" + uuid.uuid4().hex[:10],
        "topic": (topic or "").strip()[:60] or "일반",
        "types": types,
        "rule": rule[:600],
        "example": (example or "").strip()[:600],
        "evidence": (evidence or "").strip()[:300],
        "journal": (journal or "").strip()[:60],
        "origin": origin,           # seed | case_upload | compare | manual
        "enabled": bool(enabled),
        "time": time.strftime("%Y-%m-%d %H:%M"),
    }
    with _LOCK:
        data = all_suggestions()
        data[source].append(rec)
        _save(SUGGESTIONS_PATH, data)
    return rec


def is_duplicate_rule(source: str, rule: str, journal: str = "") -> bool:
    """같은 source(학회 제안은 같은 학회) 안에 동일 규칙이 이미 있는지."""
    key = "".join((rule or "").split())
    if not key:
        return True
    for s in all_suggestions().get(source, []):
        if source == "org" and s.get("journal") != journal:
            continue
        if "".join(s.get("rule", "").split()) == key:
            return True
    return False


def toggle_suggestion(sid: str) -> bool:
    with _LOCK:
        data = all_suggestions()
        for source in SOURCES:
            for s in data[source]:
                if s["id"] == sid:
                    s["enabled"] = not s.get("enabled")
                    _save(SUGGESTIONS_PATH, data)
                    return True
    return False


def delete_suggestion(sid: str) -> bool:
    with _LOCK:
        data = all_suggestions()
        for source in SOURCES:
            before = len(data[source])
            data[source] = [s for s in data[source] if s["id"] != sid]
            if len(data[source]) != before:
                _save(SUGGESTIONS_PATH, data)
                return True
    return False


# ---------------------------------------------------------------- 채택 요청 대기열

def list_requests(org: str = "") -> list[dict]:
    """채택 요청 목록(최신순). org를 주면 해당 학회 것만."""
    data = _load(REQUESTS_PATH, [])
    if org:
        data = [r for r in data if r.get("org") == org]
    return list(reversed(data))


def add_request(org: str, requester: str, topic: str, types: list[str], rule: str,
                example: str = "", evidence: str = "") -> dict:
    rule = (rule or "").strip()
    if len(rule) < 5:
        raise ValueError("제안 내용을 5자 이상 입력해 주세요.")
    if not org:
        raise ValueError("소속 학회를 확인할 수 없습니다.")
    rec = {
        "id": "rq_" + uuid.uuid4().hex[:10],
        "org": org,
        "requester": (requester or "")[:40],
        "topic": (topic or "").strip()[:60] or "발행본 비교",
        "types": [t for t in (types or []) if t in KNOWN_TYPES],
        "rule": rule[:600],
        "example": (example or "").strip()[:600],
        "evidence": (evidence or "").strip()[:300],
        "status": "대기",
        "time": time.strftime("%Y-%m-%d %H:%M"),
        "resolved_by": "", "resolved_time": "", "note": "", "suggestion_id": "",
    }
    with _LOCK:
        data = _load(REQUESTS_PATH, [])
        data.append(rec)
        _save(REQUESTS_PATH, data)
    return rec


def resolve_request(rid: str, action: str, resolver: str, note: str = "") -> dict | None:
    """action='승인'이면 '학회 제안'으로 등록하고, '반려'면 사유만 기록."""
    with _LOCK:
        data = _load(REQUESTS_PATH, [])
        rec = next((r for r in data if r["id"] == rid), None)
        if not rec or rec["status"] != "대기":
            return None
        rec["status"] = action
        rec["resolved_by"] = (resolver or "")[:40]
        rec["resolved_time"] = time.strftime("%Y-%m-%d %H:%M")
        rec["note"] = (note or "")[:500]
        _save(REQUESTS_PATH, data)
    if action == "승인":
        if not is_duplicate_rule("org", rec["rule"], rec["org"]):
            s = add_suggestion("org", topic=rec["topic"], types=rec["types"], rule=rec["rule"],
                               example=rec["example"], evidence=rec["evidence"],
                               journal=rec["org"], origin="org_request", enabled=True)
            with _LOCK:
                data = _load(REQUESTS_PATH, [])
                for r in data:
                    if r["id"] == rid:
                        r["suggestion_id"] = s["id"]
                _save(REQUESTS_PATH, data)
            rec["suggestion_id"] = s["id"]
    return rec


def delete_request(rid: str) -> bool:
    with _LOCK:
        data = _load(REQUESTS_PATH, [])
        after = [r for r in data if r["id"] != rid]
        if len(after) != len(data):
            _save(REQUESTS_PATH, after)
            return True
    return False


# ---------------------------------------------------------------- 관리자 추가 기준

def list_standards() -> list[dict]:
    return _load(STANDARDS_PATH, [])


def enabled_standards() -> list[dict]:
    return [s for s in list_standards() if s.get("enabled")]


def add_standard(rule: str, example: str = "") -> dict:
    rule = (rule or "").strip()
    if len(rule) < 5:
        raise ValueError("기준 내용을 5자 이상 입력해 주세요.")
    rec = {
        "id": "as_" + uuid.uuid4().hex[:10],
        "rule": rule[:600],
        "example": (example or "").strip()[:600],
        "enabled": True,
        "time": time.strftime("%Y-%m-%d %H:%M"),
    }
    with _LOCK:
        data = list_standards()
        data.append(rec)
        _save(STANDARDS_PATH, data)
    return rec


def toggle_standard(aid: str) -> bool:
    with _LOCK:
        data = list_standards()
        for s in data:
            if s["id"] == aid:
                s["enabled"] = not s.get("enabled")
                _save(STANDARDS_PATH, data)
                return True
    return False


def delete_standard(aid: str) -> bool:
    with _LOCK:
        data = list_standards()
        after = [s for s in data if s["id"] != aid]
        if len(after) != len(data):
            _save(STANDARDS_PATH, after)
            return True
    return False


# ---------------------------------------------------------------- 사례 논문 등록 기록

def list_corpus() -> list[dict]:
    return _load(CORPUS_PATH, [])


def add_corpus_record(journal: str, filename: str, n_refs: int, n_drafts: int) -> dict:
    rec = {
        "id": "cs_" + uuid.uuid4().hex[:10],
        "journal": (journal or "").strip()[:60],
        "filename": (filename or "")[:120],
        "n_refs": int(n_refs),
        "n_drafts": int(n_drafts),
        "time": time.strftime("%Y-%m-%d %H:%M"),
    }
    with _LOCK:
        data = list_corpus()
        data.append(rec)
        _save(CORPUS_PATH, data)
    return rec
