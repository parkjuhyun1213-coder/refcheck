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
4. 박주현 교수의 추가 제안 — suggestions.json의 "prof". 에이전트 결과와 학회지 발행본의
                            차이를 관리자가 검토·채택해 축적한 제안. 역시 표시 전용.
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
_LOCK = threading.Lock()

SOURCE_LABEL = {
    "case": "논문 사례를 통한 제안",
    "prof": "박주현 교수의 추가 제안",
}

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
    """{"case": [...], "prof": [...]}"""
    data = _load(SUGGESTIONS_PATH, {})
    return {"case": data.get("case", []), "prof": data.get("prof", [])}


def enabled_suggestions() -> list[dict]:
    """사용 설정된 제안 전체(source·label 필드 포함) — 파이프라인 매칭용."""
    out = []
    data = all_suggestions()
    for source in ("case", "prof"):
        for s in data[source]:
            if s.get("enabled"):
                item = dict(s)
                item["source"] = source
                item["label"] = SOURCE_LABEL[source]
                out.append(item)
    return out


def add_suggestion(source: str, topic: str, types: list[str], rule: str,
                   example: str = "", evidence: str = "", journal: str = "",
                   origin: str = "manual", enabled: bool = True) -> dict:
    if source not in ("case", "prof"):
        raise ValueError("source는 'case' 또는 'prof'여야 합니다.")
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


def is_duplicate_rule(source: str, rule: str) -> bool:
    """같은 source 안에 동일·유사(공백 무시 완전 일치) 규칙이 이미 있는지."""
    key = "".join((rule or "").split())
    if not key:
        return True
    return any("".join(s.get("rule", "").split()) == key
               for s in all_suggestions().get(source, []))


def toggle_suggestion(sid: str) -> bool:
    with _LOCK:
        data = all_suggestions()
        for source in ("case", "prof"):
            for s in data[source]:
                if s["id"] == sid:
                    s["enabled"] = not s.get("enabled")
                    _save(SUGGESTIONS_PATH, data)
                    return True
    return False


def delete_suggestion(sid: str) -> bool:
    with _LOCK:
        data = all_suggestions()
        for source in ("case", "prof"):
            before = len(data[source])
            data[source] = [s for s in data[source] if s["id"] != sid]
            if len(data[source]) != before:
                _save(SUGGESTIONS_PATH, data)
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
