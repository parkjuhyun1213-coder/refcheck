# -*- coding: utf-8 -*-
"""처리 이력 저장소.

에이전트가 정리한 참고문헌 결과를 디스크(app/history/)에 보존한다.
이후 관리자가 학회지 발행본(최종 게재본)을 업로드하면 이 이력과 대조해
차이를 검토하고, 채택한 차이를 '박주현 교수의 추가 제안'으로 축적한다.
"""
import json
import threading
import time
import uuid
from pathlib import Path

APP_DIR = Path(__file__).parent
HISTORY_DIR = APP_DIR / "history"
MAX_RECORDS = 300  # 초과 시 오래된 기록부터 삭제
_LOCK = threading.Lock()


def _entry_slim(entry: dict) -> dict:
    """비교에 필요한 서지요소만 보존."""
    return {k: entry.get(k, "") for k in ("title", "doi", "year", "authors", "container")}


def save_result(result: dict, options: dict) -> str:
    """처리 결과 1건(파일 단위)을 이력으로 저장하고 id를 반환."""
    hid = "h_" + uuid.uuid4().hex[:10]
    rec = {
        "id": hid,
        "time": time.strftime("%Y-%m-%d %H:%M"),
        "filename": result.get("filename", ""),
        "user": options.get("user_name", ""),
        "org": options.get("org", ""),
        "style_id": options.get("style_id", ""),
        "style_name": result.get("style_name", ""),
        "engine_label": result.get("engine_label", ""),
        "total": len(result.get("items", [])),
        "items": [
            {
                "raw": it.get("raw", ""),
                "formatted": it.get("formatted", ""),
                "group": it.get("group", ""),
                "type": it.get("type", ""),
                "entry": _entry_slim(it.get("entry") or {}),
            }
            for it in result.get("items", [])
        ],
    }
    with _LOCK:
        HISTORY_DIR.mkdir(exist_ok=True)
        (HISTORY_DIR / f"{hid}.json").write_text(
            json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
        _prune_unlocked()
    return hid


def _prune_unlocked():
    files = sorted(HISTORY_DIR.glob("h_*.json"), key=lambda p: p.stat().st_mtime)
    for p in files[:-MAX_RECORDS]:
        try:
            p.unlink()
        except OSError:
            pass


def list_history() -> list[dict]:
    """이력 메타 목록(최신순) — 발행본 비교 화면의 선택 목록용."""
    if not HISTORY_DIR.exists():
        return []
    out = []
    for p in sorted(HISTORY_DIR.glob("h_*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            rec = json.loads(p.read_text(encoding="utf-8"))
            out.append({k: rec.get(k, "") for k in
                        ("id", "time", "filename", "user", "org", "style_name", "total")})
        except (json.JSONDecodeError, OSError):
            continue
    return out


def get_history(hid: str) -> dict | None:
    p = HISTORY_DIR / f"{hid}.json"
    if not p.exists() or not hid.startswith("h_"):
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def delete_history(hid: str) -> bool:
    p = HISTORY_DIR / f"{hid}.json"
    if p.exists() and hid.startswith("h_"):
        try:
            p.unlink()
            return True
        except OSError:
            pass
    return False
