# -*- coding: utf-8 -*-
"""처리 이력·원고 파일 저장소.

- app/history/  : 에이전트가 정리한 참고문헌 결과(JSON) — 투고 원고의 참고문헌 원문(raw)과
                  Agent 결과(formatted)가 쌍으로 보존된다.
- app/uploads/  : 이용자가 올린 원고 파일 원본(월별 폴더)과 발행본 파일(published/).
이후 관리자가 학회지 발행본(최종 게재본)을 업로드하면 투고 원고 → Agent → 발행본
3단계를 대조해 차이를 검토하고, 채택한 차이를 '발행본 검토 제안'으로 축적한다.
"""
import json
import re
import threading
import time
import uuid
from pathlib import Path

APP_DIR = Path(__file__).parent
HISTORY_DIR = APP_DIR / "history"
UPLOADS_DIR = APP_DIR / "uploads"
ARCHIVE_PATH = APP_DIR / "history_archive.csv"  # 300건 초과로 밀려난 이력의 영구 보존(엑셀용)
MAX_RECORDS = 300  # 초과 시 오래된 기록부터 아카이브 후 삭제
_LOCK = threading.Lock()


def _safe_name(filename: str) -> str:
    """경로 구분자·제어문자 제거한 안전한 파일명."""
    name = Path(filename or "파일").name
    return re.sub(r"[\\/:*?\"<>|\r\n]+", "_", name)[:120] or "파일"


def _update_record(hid: str, patch: dict) -> bool:
    p = HISTORY_DIR / f"{hid}.json"
    if not p.exists():
        return False
    with _LOCK:
        rec = json.loads(p.read_text(encoding="utf-8"))
        rec.update(patch)
        p.write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
    return True


def attach_file(hid: str, filename: str, data: bytes) -> str | None:
    """업로드 원고 원본을 app/uploads/YYYY-MM/에 저장하고 이력에 연결."""
    try:
        sub = UPLOADS_DIR / time.strftime("%Y-%m")
        sub.mkdir(parents=True, exist_ok=True)
        rel = f"uploads/{time.strftime('%Y-%m')}/{hid}_{_safe_name(filename)}"
        (APP_DIR / rel).write_bytes(data)
        _update_record(hid, {"file": rel, "file_name": _safe_name(filename),
                             "file_size": len(data)})
        return rel
    except OSError:
        return None


def attach_published(hid: str, filename: str, data: bytes) -> str | None:
    """발행본 파일을 app/uploads/published/에 저장하고 이력에 연결."""
    try:
        sub = UPLOADS_DIR / "published"
        sub.mkdir(parents=True, exist_ok=True)
        rel = f"uploads/published/{hid}_{_safe_name(filename)}"
        (APP_DIR / rel).write_bytes(data)
        _update_record(hid, {"published_file": rel,
                             "published_name": _safe_name(filename),
                             "compared": time.strftime("%Y-%m-%d %H:%M")})
        return rel
    except OSError:
        return None


def save_compare(hid: str, cmp_result: dict) -> bool:
    """최근 3자 비교 결과를 이력에 보존(CSV 재다운로드용)."""
    return _update_record(hid, {"last_compare": cmp_result})


def update_result_items(hid: str, items: list, summary: dict | None = None) -> bool:
    """미매칭 재조회 등으로 바뀐 items·summary를 이력에 반영.

    이걸 하지 않으면 화면에서는 '실존 확인'인데 지난 결과·재다운로드에서는
    옛 판정이 나와 서로 어긋난다.
    """
    rec = get_history(hid)
    if not rec:
        return False
    extra = dict(rec.get("result_extra") or {})
    if summary is not None:
        extra["summary"] = summary
    return _update_record(hid, {"items": items, "result_extra": extra,
                                "total": len(items)})


def save_result(result: dict, options: dict) -> str:
    """처리 결과 1건(파일 단위)을 전체 저장하고 id를 반환.

    items는 검증·제안 포함 원본 그대로, 그 외 결과 필드는 result_extra에 보존해
    나중에 처리 직후와 동일한 화면·다운로드를 재현할 수 있게 한다.
    """
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
        "cost": result.get("cost") or {},  # 이 원고 처리에 든 API 비용(관리자만 열람)
        "items": result.get("items", []),
        "result_extra": {k: result.get(k) for k in
                         ("summary", "warnings", "crosscheck", "health",
                          "english_list", "verify_enabled",
                          "checked_at", "app_version") if result.get(k) is not None},
    }
    with _LOCK:
        HISTORY_DIR.mkdir(exist_ok=True)
        (HISTORY_DIR / f"{hid}.json").write_text(
            json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
        _prune_unlocked()
    return hid


def result_view(hid: str) -> dict | None:
    """저장된 이력을 처리 직후의 result 형태로 복원(열람·재다운로드용)."""
    rec = get_history(hid)
    if not rec:
        return None
    res = dict(rec.get("result_extra") or {})
    res["filename"] = rec.get("filename", "")
    res["style_name"] = rec.get("style_name", "")
    res["engine_label"] = rec.get("engine_label", "")
    res["items"] = rec.get("items") or []
    res.setdefault("summary", {})
    res.setdefault("warnings", [])
    res.setdefault("error", "")
    return res


def _archive_record_unlocked(rec: dict):
    """밀려나는 이력을 아카이브 CSV(엑셀용)에 영구 기록 — 메타 + 최종 참고문헌 목록."""
    import csv
    refs_lines = []
    group = None
    for it in rec.get("items", []):
        if it.get("group") and it["group"] != group:
            group = it["group"]
            refs_lines.append(f"[{group}]")
        refs_lines.append(it.get("formatted", ""))
    refs = "\n".join(refs_lines)[:30000]  # 엑셀 셀 한도(32,767자) 보호
    new = not ARCHIVE_PATH.exists()
    with ARCHIVE_PATH.open("a", encoding="utf-8-sig" if new else "utf-8", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["처리일시", "파일명", "이용자", "학회", "적용 기준",
                        "문헌 수", "최종 참고문헌 목록"])
        w.writerow([rec.get("time", ""), rec.get("filename", ""), rec.get("user", ""),
                    rec.get("org", ""), rec.get("style_name", ""), rec.get("total", 0), refs])


def _prune_unlocked():
    files = sorted(HISTORY_DIR.glob("h_*.json"), key=lambda p: p.stat().st_mtime)
    for p in files[:-MAX_RECORDS]:
        try:
            rec = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            rec = {}
        try:
            if rec:
                _archive_record_unlocked(rec)
        except OSError:
            pass  # 아카이브 실패가 처리 흐름을 막지 않도록
        # 보관 파일(원본·발행본)도 함께 정리 — 고아 파일로 디스크가 차는 것 방지
        for key in ("file", "published_file"):
            rel = rec.get(key, "")
            if rel and rel.startswith("uploads/") and ".." not in rel:
                try:
                    (APP_DIR / rel).unlink(missing_ok=True)
                except OSError:
                    pass
        try:
            p.unlink()
        except OSError:
            pass


def list_history() -> list[dict]:
    """이력 메타 목록(최신순) — 발행본 비교·원고 관리 화면용."""
    if not HISTORY_DIR.exists():
        return []
    out = []
    for p in sorted(HISTORY_DIR.glob("h_*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            rec = json.loads(p.read_text(encoding="utf-8"))
            meta = {k: rec.get(k, "") for k in
                    ("id", "time", "filename", "user", "org", "style_name", "total",
                     "file_size", "compared")}
            meta["cost_usd"] = round((rec.get("cost") or {}).get("usd", 0.0), 6)
            meta["has_file"] = bool(rec.get("file"))
            meta["has_published"] = bool(rec.get("published_file"))
            meta["has_compare"] = bool(rec.get("last_compare"))
            out.append(meta)
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
    """이력 레코드와 연결된 원본·발행본 파일까지 삭제."""
    p = HISTORY_DIR / f"{hid}.json"
    if not p.exists() or not hid.startswith("h_"):
        return False
    try:
        rec = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        rec = {}
    for key in ("file", "published_file"):
        rel = rec.get(key, "")
        # 경로 이탈 방지 — uploads/ 하위만 삭제 허용
        if rel and rel.startswith("uploads/") and ".." not in rel:
            try:
                (APP_DIR / rel).unlink(missing_ok=True)
            except OSError:
                pass
    try:
        p.unlink()
        return True
    except OSError:
        return False


def file_path(hid: str, kind: str = "orig") -> tuple[Path, str] | None:
    """다운로드용 (절대경로, 표시 파일명). kind: 'orig' | 'published'."""
    rec = get_history(hid)
    if not rec:
        return None
    key, name_key = (("file", "file_name") if kind == "orig"
                     else ("published_file", "published_name"))
    rel = rec.get(key, "")
    if not rel or not rel.startswith("uploads/") or ".." in rel:
        return None
    p = APP_DIR / rel
    if not p.exists():
        return None
    return p, rec.get(name_key) or p.name
