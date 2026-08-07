# -*- coding: utf-8 -*-
"""편집 요구사항(이용자 피드백)과 편집 지침(관리자 확정) 저장소.

- feedback_log.json: 이용자가 제출한 편집 요구 (즉시 반영되지 않음)
- style_directives.json: 관리자가 검토 후 확정한 편집 지침 — 기준(style)별로 저장되며
  이후 처리 시 AI 변환 프롬프트에 반영된다.
"""
import json
import threading
import time
import uuid
from pathlib import Path

APP_DIR = Path(__file__).parent
FEEDBACK_PATH = APP_DIR / "feedback_log.json"
DIRECTIVES_PATH = APP_DIR / "style_directives.json"
_LOCK = threading.Lock()


def _load(path: Path, default):
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return default


def _save(path: Path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


# ---------------------------------------------------------------- 편집 요구

def list_feedback() -> list[dict]:
    return _load(FEEDBACK_PATH, [])


def add_feedback(user: str, org: str, style_id: str, style_name: str,
                 raw: str, formatted: str, request: str) -> dict:
    request = (request or "").strip()
    if len(request) < 5:
        raise ValueError("요구 내용을 5자 이상 입력해 주세요.")
    rec = {
        "id": "fb_" + uuid.uuid4().hex[:10],
        "time": time.strftime("%Y-%m-%d %H:%M"),
        "user": (user or "")[:40], "org": (org or "")[:60],
        "style_id": style_id, "style_name": style_name,
        "raw": (raw or "")[:500], "formatted": (formatted or "")[:500],
        "request": request[:1000],
        "status": "접수", "admin_note": "", "resolved_time": "",
    }
    with _LOCK:
        data = list_feedback()
        data.append(rec)
        _save(FEEDBACK_PATH, data)
    return rec


def resolve_feedback(fid: str, action: str, admin_note: str = "") -> dict | None:
    """action: '반영' 또는 '보류'."""
    with _LOCK:
        data = list_feedback()
        for rec in data:
            if rec["id"] == fid:
                rec["status"] = action
                rec["admin_note"] = (admin_note or "")[:1000]
                rec["resolved_time"] = time.strftime("%Y-%m-%d %H:%M")
                _save(FEEDBACK_PATH, data)
                return rec
    return None


# ---------------------------------------------------------------- 편집 지침

def all_directives() -> dict:
    """{style_id: [{text, time, from_feedback}]}"""
    return _load(DIRECTIVES_PATH, {})


def directives_for(style_id: str) -> list[str]:
    return [d["text"] for d in all_directives().get(style_id, [])]


def add_directive(style_id: str, text: str, from_feedback: str = "") -> dict:
    text = (text or "").strip()
    if len(text) < 5:
        raise ValueError("편집 지침을 5자 이상 입력해 주세요.")
    with _LOCK:
        data = all_directives()
        lst = data.setdefault(style_id, [])
        d = {"text": text[:500], "time": time.strftime("%Y-%m-%d %H:%M"),
             "from_feedback": from_feedback}
        lst.append(d)
        _save(DIRECTIVES_PATH, data)
    return d


def remove_directive(style_id: str, index: int) -> bool:
    with _LOCK:
        data = all_directives()
        lst = data.get(style_id, [])
        if 0 <= index < len(lst):
            lst.pop(index)
            _save(DIRECTIVES_PATH, data)
            return True
    return False
