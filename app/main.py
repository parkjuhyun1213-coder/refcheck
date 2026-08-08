# -*- coding: utf-8 -*-
"""파일 기반 참고문헌 표준화·검증 에이전트 — FastAPI 서버.

실행:  python -m uvicorn main:app --port 8765   (run.bat 참조)
접속:  http://localhost:8765
"""
import hashlib
import io
import re
import secrets
import threading
import time
import uuid
import zipfile
from pathlib import Path

from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, Response, JSONResponse

import aiengine
import compare as compare_mod
import cost as cost_mod
import crosscheck as cc_mod
import extract
import feedback as feedback_mod
import formatter
import history as history_mod
import parsing
import report
import rules
import styles as styles_mod
import suggestions as suggestions_mod
import verify as verify_mod

app = FastAPI(title="파일 기반 참고문헌 표준화·검증 에이전트")

# 화면(index.html)과 프로그램의 버전이 어긋난 채 배포되면 새 기능이 조용히 무시된다.
# 두 파일에 같은 값을 두고 /api/status에서 대조해 관리자 화면에 경고를 띄운다.
# 기능을 추가·변경할 때 main.py와 index.html의 APP_VERSION을 함께 올릴 것.
APP_VERSION = "2026.08.08-9"

APP_DIR = Path(__file__).parent
JOBS: dict[str, dict] = {}
_JOBS_LOCK = threading.Lock()

GROUP_LABEL = {"ko": "국내문헌", "west": "서양문헌", "east": "동양문헌"}


# ================================================================ 관리자 인증
# 관리자 계정은 프로젝트 루트 .env의 ADMIN_ID / ADMIN_PASSWORD 로 설정한다.
ADMIN_SESSIONS: dict[str, float] = {}
SESSION_TTL = 12 * 3600  # 12시간
_PLACEHOLDER_PW = {"", "바꿔주세요", "changeme", "password", "1234"}


def _admin_creds() -> tuple[str, str]:
    return aiengine.env_get("ADMIN_ID").strip(), aiengine.env_get("ADMIN_PASSWORD").strip()


def admin_configured() -> bool:
    aid, pw = _admin_creds()
    return bool(aid) and pw not in _PLACEHOLDER_PW


def is_admin(request: Request) -> bool:
    tok = request.cookies.get("admin_token", "")
    exp = ADMIN_SESSIONS.get(tok)
    if not tok or not exp:
        return False
    if time.time() > exp:
        ADMIN_SESSIONS.pop(tok, None)
        return False
    return True


def _cookie_secure() -> bool:
    """HTTPS 정식 운영 환경에서는 .env의 COOKIE_SECURE=1 로 쿠키를 HTTPS 전용으로 보호."""
    return aiengine.env_get("COOKIE_SECURE") == "1"


# ---------------------------------------------------------------- 이용자 접근 코드
# 외부 공개 운영 시 무단 사용(API 비용 발생)을 막기 위한 접근 코드.
# - 학회별 코드: config.json의 access_codes {학회명: 코드} — 코드 입력만으로 소속 학회가
#   자동 식별되어 통계에 기록된다. 관리자 ⚙ 설정에서 관리.
# - 공통 코드: config.json의 access_code → .env의 ACCESS_CODE (학회 구분 없는 예비 코드).
# 모두 비어 있으면 접근 제한 없음(로컬 사용 기본값).

def _access_code() -> str:
    return (aiengine.load_config().get("access_code") or aiengine.env_get("ACCESS_CODE")).strip()


ROLE_LABEL = {"user": "이용자", "editor": "편집위원", "chair": "편집위원장"}
ROLE_RANK = {"": 0, "user": 1, "editor": 2, "chair": 3}


def _org_access_codes() -> dict[str, str]:
    """학회별 이용자 코드 {학회명: 코드}."""
    raw = aiengine.load_config().get("access_codes") or {}
    out = {}
    if isinstance(raw, dict):
        for org, code in raw.items():
            org, code = str(org).strip(), str(code).strip()
            if org and code:
                out[org] = code
    return out


def _role_codes() -> dict[str, dict[str, str]]:
    """역할별 코드 {"editor": {학회명: 코드}, "chair": {학회명: 코드}}."""
    cfg = aiengine.load_config()
    out: dict[str, dict[str, str]] = {}
    for role in ("editor", "chair"):
        raw = cfg.get(f"{role}_codes") or {}
        codes = {}
        if isinstance(raw, dict):
            for org, code in raw.items():
                org, code = str(org).strip(), str(code).strip()
                if org and code:
                    codes[org] = code
        out[role] = codes
    return out


def access_required() -> bool:
    rc = _role_codes()
    return bool(_access_code() or _org_access_codes() or rc["editor"] or rc["chair"])


def _access_hash(code: str) -> str:
    return hashlib.sha256(("refstd-access:" + code).encode("utf-8")).hexdigest()


def _valid_hashes() -> dict[str, tuple[str, str]]:
    """{쿠키 해시: (학회명, 역할)} — 코드가 바뀌면 기존 쿠키는 자동 무효.

    입장 방식은 두 가지를 모두 허용한다.
    - 학회 코드 + 역할 코드(2칸): 편집위원·위원장은 자기 학회 코드에 역할 코드를 더해 입력
    - 코드 하나: 역할 코드만으로도 학회·역할이 식별되므로 단독 입력도 받아들인다
    """
    out: dict[str, tuple[str, str]] = {}

    def put(code: str, org: str, role: str):
        h = _access_hash(code)
        prev = out.get(h)
        if not prev or ROLE_RANK[role] > ROLE_RANK[prev[1]]:
            out[h] = (org, role)

    common = _access_code()
    if common:
        put(common, "", "user")
    org_codes = _org_access_codes()
    for org, code in org_codes.items():
        put(code, org, "user")
    rc = _role_codes()
    for role in ("editor", "chair"):
        for org, code in rc[role].items():
            base = org_codes.get(org)
            if base:
                # 역할 코드는 자기 학회 코드와 짝을 이룰 때만 유효
                # (다른 학회 코드 + 남의 역할 코드 조합을 막는다)
                put(f"{base}|{code}", org, role)
            else:
                # 그 학회에 이용자 코드가 없으면 역할 코드 단독 입장 허용(잠김 방지)
                put(code, org, role)
    return out


def _resolve_codes(code: str, role_code: str) -> tuple[str, tuple[str, str]] | None:
    """입력한 코드(들)을 검증해 (쿠키값, (학회, 역할))을 반환. 실패 시 None."""
    valid = _valid_hashes()
    code, role_code = code.strip(), role_code.strip()
    if role_code:
        for candidate in (f"{code}|{role_code}",  # 학회 코드 + 역할 코드
                          role_code):             # 이용자 코드가 없는 학회의 역할 코드 단독
            h = _access_hash(candidate)
            if h in valid:
                return h, valid[h]
        return None
    h = _access_hash(code)
    return (h, valid[h]) if h in valid else None


def _access_info(request: Request) -> tuple[str, str]:
    """(학회명, 역할) — 미인증이면 ('', '')."""
    return _valid_hashes().get(request.cookies.get("access_token", ""), ("", ""))


def has_access(request: Request) -> bool:
    if not access_required() or is_admin(request):
        return True
    return request.cookies.get("access_token", "") in _valid_hashes()


def access_org(request: Request) -> str:
    """접근 코드로 식별된 학회명(공통 코드·미인증·관리자는 '')."""
    return _access_info(request)[0]


def access_role(request: Request) -> str:
    """역할: admin | chair | editor | user | '' (미인증)."""
    if is_admin(request):
        return "admin"
    return _access_info(request)[1]


def is_editor(request: Request) -> bool:
    """자기 학회 관리 권한(편집위원 이상)."""
    return access_role(request) in ("editor", "chair", "admin")


def require_editor(request: Request) -> tuple[str, str]:
    """편집위원 이상 확인 후 (학회명, 역할) 반환. 관리자는 학회명 ''(전체)."""
    role = access_role(request)
    if role == "admin":
        return "", "admin"
    if role not in ("editor", "chair"):
        raise HTTPException(403, "편집위원 전용 기능입니다. 학회 편집위원 코드로 입장해 주세요.")
    org = access_org(request)
    if not org:
        raise HTTPException(403, "소속 학회를 확인할 수 없습니다.")
    return org, role


def require_access(request: Request):
    if not has_access(request):
        raise HTTPException(401, "접근 코드가 필요합니다. 첫 화면에서 접근 코드를 입력해 주세요.")


@app.post("/api/access")
def enter_access(code: str = Form(""), role_code: str = Form("")):
    """학회 코드(+ 편집위원·위원장은 역할 코드)로 입장."""
    if not access_required():
        return {"ok": True, "org": "", "role": "", "role_label": ""}
    found = _resolve_codes(code, role_code)
    if found:
        token, (org_name, role) = found
        resp = JSONResponse({"ok": True, "org": org_name, "role": role,
                             "role_label": ROLE_LABEL.get(role, "이용자")})
        resp.set_cookie("access_token", token, httponly=True,
                        samesite="lax", max_age=30 * 24 * 3600, secure=_cookie_secure())
        return resp
    time.sleep(0.8)  # 무차별 대입 지연
    msg = ("학회 코드와 역할 코드가 맞지 않습니다. 역할 코드는 소속 학회의 코드와 함께 입력해 주세요."
           if role_code.strip() else "접근 코드가 올바르지 않습니다.")
    return JSONResponse({"ok": False, "message": msg}, status_code=401)


@app.post("/api/access/logout")
def leave_access():
    """접근 코드 해제 — 다른 코드로 다시 입장할 때 사용."""
    resp = JSONResponse({"ok": True})
    resp.delete_cookie("access_token")
    return resp


def require_admin(request: Request):
    if not is_admin(request):
        raise HTTPException(403, "관리자 모드가 필요합니다. 화면 오른쪽 위 '관리자'에서 로그인해 주세요.")


@app.post("/api/admin/login")
def admin_login(admin_id: str = Form(""), password: str = Form("")):
    if not admin_configured():
        return JSONResponse(
            {"ok": False,
             "message": "관리자 계정이 아직 설정되지 않았습니다. .env 파일의 ADMIN_ID와 ADMIN_PASSWORD 값을 원하는 계정으로 바꾼 뒤 다시 시도해 주세요."},
            status_code=400)
    aid, pw = _admin_creds()
    # UTF-8 바이트 비교(compare_digest는 비ASCII 문자열을 지원하지 않음)
    if secrets.compare_digest(admin_id.strip().encode("utf-8"), aid.encode("utf-8")) \
            and secrets.compare_digest(password.encode("utf-8"), pw.encode("utf-8")):
        tok = secrets.token_urlsafe(32)
        ADMIN_SESSIONS[tok] = time.time() + SESSION_TTL
        resp = JSONResponse({"ok": True})
        resp.set_cookie("admin_token", tok, httponly=True, samesite="lax",
                        max_age=SESSION_TTL, secure=_cookie_secure())
        return resp
    return JSONResponse({"ok": False, "message": "아이디 또는 비밀번호가 올바르지 않습니다."}, status_code=401)


@app.post("/api/admin/logout")
def admin_logout(request: Request):
    ADMIN_SESSIONS.pop(request.cookies.get("admin_token", ""), None)
    resp = JSONResponse({"ok": True})
    resp.delete_cookie("admin_token")
    return resp


# ================================================================ 처리 파이프라인

def _norm_for_compare(s: str) -> str:
    return re.sub(r"[\s\.,;::()\[\]“”\"'·]+", "", s or "")


_SUGGEST_FIELDS = [("year", "연도"), ("volume", "권"), ("issue", "호"), ("pages", "면수")]


def _build_suggestions(entry: dict, meta: dict | None) -> list[dict]:
    """검증에서 매칭된 정규 서지(meta)와 파싱 결과의 차이 → 필드별 수정 제안."""
    if not meta:
        return []
    out = []
    for f, label in _SUGGEST_FIELDS:
        cur = (entry.get(f) or "").strip()
        new = (meta.get(f) or "").strip().replace("–", "-")
        if f == "year":
            cur = re.sub(r"[a-z]$", "", cur)
            if not re.fullmatch(r"\d{4}", new):
                continue
        if not new or new == cur:
            continue
        if f == "pages" and cur and _norm_for_compare(cur) == _norm_for_compare(new):
            continue
        out.append({"field": f, "label": label, "current": cur or "(없음)",
                    "suggested": new, "source": meta.get("source", "")})
    return out


def _apply_suggestions(entry: dict, suggestions: list[dict]) -> list[str]:
    """자동 교정 적용. 적용 내역 문자열 목록 반환."""
    applied = []
    for s in suggestions:
        entry[s["field"]] = s["suggested"]
        applied.append(f"자동 교정: {s['label']} {s['current']}→{s['suggested']} ({s['source']})")
    return applied


def _title_sim(a: str, b: str) -> float:
    import difflib
    na = re.sub(r"[^0-9a-z가-힣]", "", (a or "").lower())
    nb = re.sub(r"[^0-9a-z가-힣]", "", (b or "").lower())
    if not na or not nb:
        return 0.0
    return difflib.SequenceMatcher(None, na, nb).ratio()


RECENT_YEARS = 10  # 최근 문헌 기준 연한


def _health_report(entries: list[dict], user_name: str) -> dict:
    """참고문헌 건전성 리포트: 연도 분포·중복·자기인용·저널 편중."""
    cur_year = time.localtime().tm_year
    years = []
    for e in entries:
        m = re.match(r"(\d{4})", e.get("year") or "")
        if m:
            years.append(int(m.group(1)))
    year_dist: dict[str, int] = {}
    for y in sorted(years):
        year_dist[str(y)] = year_dist.get(str(y), 0) + 1
    recent = sum(1 for y in years if y >= cur_year - (RECENT_YEARS - 1))

    # 중복 검출: 동일 DOI 또는 제목 유사도 0.92 이상
    duplicates = []
    for i in range(len(entries)):
        for j in range(i + 1, len(entries)):
            a, b = entries[i], entries[j]
            same_doi = a.get("doi") and a.get("doi") == b.get("doi")
            sim = _title_sim(a.get("title", ""), b.get("title", ""))
            if same_doi or sim >= 0.92:
                duplicates.append({
                    "a": (a.get("raw") or "")[:80], "b": (b.get("raw") or "")[:80],
                    "reason": "동일 DOI" if same_doi else f"제목 유사({sim:.0%})",
                })

    self_cites = 0
    if user_name:
        for e in entries:
            if any(user_name in (a or "") for a in e.get("authors") or []):
                self_cites += 1

    journals: dict[str, int] = {}
    for e in entries:
        if e.get("type") == "journal" and e.get("container"):
            jn = e["container"].strip()
            journals[jn] = journals.get(jn, 0) + 1
    top_journals = sorted(journals.items(), key=lambda kv: -kv[1])[:5]
    j_total = sum(journals.values())

    types: dict[str, int] = {}
    for e in entries:
        types[e.get("type", "unknown")] = types.get(e.get("type", "unknown"), 0) + 1

    return {
        "total": len(entries),
        "year_min": min(years) if years else None,
        "year_max": max(years) if years else None,
        "year_dist": year_dist,
        "recent_years": RECENT_YEARS,
        "recent_count": recent,
        "recent_ratio": round(recent / len(years), 3) if years else None,
        "duplicates": duplicates,
        "self_cites": self_cites,
        "top_journals": [{"name": n, "count": c,
                          "share": round(c / j_total, 3) if j_total else 0}
                         for n, c in top_journals],
        "types": types,
    }


def process_file(filename: str, data: bytes, options: dict, progress) -> dict:
    """단일 원고 파일 처리 — API 비용을 건별로 집계하고 이력에 저장한다."""
    cost_mod.start_job()
    try:
        result = _process_file(filename, data, options, progress)
    finally:
        spent = cost_mod.end_job()
    if spent:
        result["cost"] = spent
    # 처리 이력 저장(발행본 비교·지난 결과 열람용) — 실패해도 처리 자체를 막지 않음
    if result.get("items"):
        try:
            result["history_id"] = history_mod.save_result(result, options)
        except Exception:
            pass
    return result


def _process_file(filename: str, data: bytes, options: dict, progress) -> dict:
    """실제 처리 파이프라인. options: {style_id, verify, crosscheck, english}"""
    result = {"filename": filename, "error": "", "warnings": [], "items": [],
              "summary": {}, "verify_enabled": bool(options.get("verify")),
              "crosscheck": None, "english_list": None}

    style = styles_mod.get_style(options.get("style_id") or "munpyeonhyeop")
    if not style:
        result["error"] = "선택한 참고문헌 작성 기준을 찾을 수 없습니다."
        return result
    result["style_name"] = style["name"]
    builtin = bool(style.get("builtin"))
    directives = feedback_mod.directives_for(style["id"])
    # 이용자가 관리자 추가 규칙(추가 기준·편집 지침) 적용을 끈 경우
    apply_extra = options.get("apply_extra", True)
    if not apply_extra:
        n_extra = len(directives) + (len(suggestions_mod.enabled_standards()) if builtin else 0)
        if n_extra:
            result["warnings"].append(
                f"이용자 선택에 따라 관리자 추가 규칙 {n_extra}건(추가 기준·편집 지침)을 적용하지 않았습니다.")
        directives = []

    use_ai = aiengine.is_configured()
    result["engine_label"] = f"AI({aiengine.get_model()}) + 규칙" if use_ai else "규칙 기반"

    if not builtin and not use_ai:
        result["error"] = ("사용자 정의 기준 적용은 AI 모드가 필요합니다. "
                           "설정에서 Claude API 키를 등록하거나, 기본 기준(문편협)을 선택해 주세요.")
        return result

    # 1) 파싱
    progress("파일 파싱", filename)
    try:
        text = parsing.extract_text(filename, data)
    except parsing.ParseError as ex:
        result["error"] = str(ex)
        return result

    # 2) 참고문헌 구역 탐지
    progress("참고문헌 구역 탐지", filename)
    body, section = extract.find_reference_section(text)
    if not section:
        # 파일 전체가 참고문헌 목록인 경우(목록만 담긴 파일) 감지
        probe = extract.split_entries(text)
        if len(probe) >= 3 and sum(1 for p in probe if re.search(r"\(?\d{4}", p)) >= len(probe) * 0.6:
            section, body = text, ""
            result["warnings"].append("참고문헌 표제를 찾지 못해 파일 전체를 참고문헌 목록으로 처리했습니다.")
        else:
            result["error"] = "참고문헌 구역을 찾을 수 없습니다. 원고에 '참고문헌' 또는 'References' 표제가 있는지 확인해 주세요."
            return result

    # 3) 문헌 건별 분리
    progress("문헌 추출·분리", filename)
    raws: list[str] = []
    if use_ai:
        try:
            raws = aiengine.split_entries_ai(section)
        except aiengine.AIError as ex:
            result["warnings"].append(f"AI 분리 실패({ex}) — 규칙 엔진으로 대체")
    if not raws:
        raws = extract.split_entries(section)
    if not raws:
        result["error"] = "참고문헌 구역에서 문헌을 추출하지 못했습니다."
        return result

    # 4) 서지 구조화
    progress(f"서지 구조화 ({len(raws)}건)", filename)
    entries: list[dict] = []
    if use_ai:
        try:
            entries = aiengine.structure_entries_ai(raws)
        except aiengine.AIError as ex:
            result["warnings"].append(f"AI 구조화 실패({ex}) — 규칙 엔진으로 대체")
    if not entries:
        entries = [rules.structure_entry(r) for r in raws]

    # 5) 실존·윤리 검증(형식 변환 전에 수행해 발견된 DOI·교정을 반영)
    verify_results = None
    suggestions_by_idx: dict[int, list[dict]] = {}
    autofix_notes_by_idx: dict[int, list[str]] = {}
    if options.get("verify"):
        progress(f"실존·윤리 검증 (Crossref·OpenAlex·국내DB, {len(entries)}건)", filename)
        verify_results = verify_mod.verify_entries(entries)
        for i, (e, v) in enumerate(zip(entries, verify_results)):
            if v.get("status") != "verified":
                continue  # mismatch 등 불확실 매칭의 서지는 교정·DOI 반영에 사용하지 않음
            if v.get("found_doi") and not e.get("doi"):
                e["doi"] = v["found_doi"]
            sugg = _build_suggestions(e, v.get("meta"))
            if sugg:
                if options.get("autofix"):
                    autofix_notes_by_idx[i] = _apply_suggestions(e, sugg)
                    for s in sugg:
                        s["applied"] = True
                suggestions_by_idx[i] = sugg

    # 6) 형식 변환·정렬
    progress("형식 변환·정렬", filename)
    items: list[dict] = []
    if builtin:
        order = formatter.sort_and_disambiguate(entries)
        idx_of = {id(e): i for i, e in enumerate(entries)}
        for e in order:
            i = idx_of[id(e)]
            formatted = formatter.format_entry(e)
            issues = formatter.validate_entry(e) + autofix_notes_by_idx.get(i, [])
            items.append({
                "raw": e.get("raw", ""), "formatted": formatted,
                "group": GROUP_LABEL.get(e.get("lang", "ko"), "국내문헌"),
                "issues": issues, "type": e.get("type", ""),
                "changed": _norm_for_compare(e.get("raw")) != _norm_for_compare(formatted),
                "verify": verify_results[i] if verify_results else None,
                "suggestions": suggestions_by_idx.get(i, []),
                "entry": {k: e.get(k, "") for k in
                          ("authors", "year", "title", "container", "volume", "issue",
                           "pages", "publisher", "place", "doi", "url", "degree",
                           "institution", "edition", "lang")},
            })
    else:
        try:
            refs, order_note = aiengine.format_custom_style_ai(entries, style, directives)
        except aiengine.AIError as ex:
            result["error"] = f"사용자 기준 변환 실패: {ex}"
            return result
        if order_note:
            result["warnings"].append(f"적용된 배열 규칙: {order_note}")
        # 그룹 등장 순서 유지, 그룹 내에서는 변환 결과 문자열순(저자명 선두 가정)
        seen_groups: list[str] = []
        for r in refs:
            g = r.get("group") or "전체"
            if g not in seen_groups:
                seen_groups.append(g)
        refs_sorted = sorted(refs, key=lambda r: (seen_groups.index(r.get("group") or "전체"),
                                                  (r.get("formatted") or "").lower()))
        for r in refs_sorted:
            i = r.get("index", 0)
            e = entries[i] if 0 <= i < len(entries) else {}
            items.append({
                "raw": e.get("raw", ""), "formatted": r.get("formatted", ""),
                "group": r.get("group") or "전체",
                "issues": (r.get("issues") or []) + list(e.get("notes") or []) + autofix_notes_by_idx.get(i, []),
                "type": e.get("type", ""),
                "changed": _norm_for_compare(e.get("raw")) != _norm_for_compare(r.get("formatted")),
                "verify": verify_results[i] if verify_results and 0 <= i < len(verify_results) else None,
                "suggestions": suggestions_by_idx.get(i, []),
                "entry": {k: e.get(k, "") for k in
                          ("authors", "year", "title", "container", "volume", "issue",
                           "pages", "publisher", "place", "doi", "url", "degree",
                           "institution", "edition", "lang")},
            })
    # 6-1) 관리자 추가 기준(문편협에 준함 — 공백 보완) + 관리자 확정 편집 지침 반영
    #      문편협 공통기준이 일순위: 명시 규정과 충돌하는 추가 기준은 적용하지 않고 경고로 알림.
    admin_standards = suggestions_mod.enabled_standards() if (builtin and apply_extra) else []
    if builtin and (directives or admin_standards):
        if use_ai:
            progress(f"관리자 기준·지침 반영 (기준 {len(admin_standards)}·지침 {len(directives)}건)", filename)
            try:
                fixed = aiengine.apply_standards_ai(
                    [it["formatted"] for it in items], admin_standards, directives, style["name"])
                for it, r in zip(items, fixed):
                    nf = r.get("formatted", "")
                    if nf and nf != it["formatted"]:
                        it["formatted"] = nf
                        it["changed"] = _norm_for_compare(it["raw"]) != _norm_for_compare(nf)
                        note = r.get("note") or "관리자 기준·지침 반영"
                        it["issues"] = list(it["issues"]) + [f"반영: {note}"]
                    if r.get("conflict"):
                        result["warnings"].append(
                            f"관리자 추가 기준이 문편협 공통 기준과 충돌해 적용하지 않음 — {r['conflict']}")
            except aiengine.AIError as ex:
                result["warnings"].append(f"관리자 기준·지침 반영 실패({ex}) — 미적용 상태로 출력")
        else:
            result["warnings"].append(
                f"관리자 추가 기준 {len(admin_standards)}건·편집 지침 {len(directives)}건이 있으나 "
                "AI 모드에서만 적용됩니다.")

    # 6-2) 작성 제안 매칭 — 사례·교수 제안 + 해당 학회의 '학회 제안' (표시 전용)
    if builtin:
        sugg_pool = suggestions_mod.enabled_suggestions(options.get("org", ""))
        if sugg_pool:
            by_id = {s["id"]: s for s in sugg_pool}
            tips_by_idx: dict[int, list[str]] = {}
            if use_ai:
                progress(f"작성 제안 대조 ({len(sugg_pool)}건 기준)", filename)
                try:
                    payload = [{"index": i, "formatted": it["formatted"],
                                "type": it.get("type", ""), "issues": it.get("issues") or []}
                               for i, it in enumerate(items)]
                    tips_by_idx = aiengine.match_suggestions_ai(payload, sugg_pool)
                except aiengine.AIError as ex:
                    result["warnings"].append(f"작성 제안 대조 실패({ex}) — 유형 기준으로 표시")
            if not tips_by_idx and not use_ai:
                # 규칙 모드 폴백: 자료 유형이 일치하는 제안을 표시
                for i, it in enumerate(items):
                    ids = [s["id"] for s in sugg_pool
                           if s.get("types") and it.get("type", "") in s["types"]]
                    if ids:
                        tips_by_idx[i] = ids
            for i, ids in tips_by_idx.items():
                if 0 <= i < len(items):
                    items[i]["tips"] = [
                        {"id": sid, "source": by_id[sid]["source"], "label": by_id[sid]["label"],
                         "topic": by_id[sid].get("topic", ""), "rule": by_id[sid].get("rule", ""),
                         "example": by_id[sid].get("example", "")}
                        for sid in ids if sid in by_id]
    result["items"] = items

    # 7) 본문 인용 대조
    if options.get("crosscheck") and body:
        progress("본문 인용 ↔ 목록 대조", filename)
        result["crosscheck"] = cc_mod.cross_check(body, entries)
    elif options.get("crosscheck"):
        result["warnings"].append("본문이 없어(목록 전용 파일) 본문-목록 대조를 건너뛰었습니다.")

    # 8) 영문 변환 목록(문편협 기준 9·10항 — AI 모드)
    if options.get("english") and builtin:
        ko_entries = [e for e in entries if e.get("lang") == "ko"]
        if ko_entries and use_ai:
            progress(f"영문 변환 목록 생성 ({len(ko_entries)}건)", filename)
            try:
                eng = aiengine.translate_to_english_ai(ko_entries)
                lines = sorted((r.get("formatted", "") for r in eng if r.get("formatted")),
                               key=str.lower)
                result["english_list"] = lines
            except aiengine.AIError as ex:
                result["warnings"].append(f"영문 변환 실패({ex})")
        elif ko_entries:
            result["warnings"].append("영문 변환 목록은 AI 모드(API 키 등록)에서만 생성됩니다.")
    elif options.get("english") and not builtin:
        result["warnings"].append("영문 변환 목록은 문편협 기준에서만 제공됩니다.")

    # 건전성 리포트
    progress("건전성 리포트 집계", filename)
    result["health"] = _health_report(entries, options.get("user_name", ""))

    # 요약
    result["summary"] = {
        "total": len(items),
        "changed": sum(1 for it in items if it["changed"]),
        "needs_check": sum(1 for it in items if it["issues"]),
        "verified": sum(1 for it in items if (it.get("verify") or {}).get("status") in ("verified", "link_ok")),
        "retracted": sum(1 for it in items
                         if ((it.get("verify") or {}).get("retraction") or {}).get("severe")),
        "suspect": sum(1 for it in items if (it.get("verify") or {}).get("status") == "suspect"),
        "suggestions": sum(len(it.get("suggestions") or []) for it in items),
        "tips": sum(len(it.get("tips") or []) for it in items),
        "crosscheck_issues": (len(result["crosscheck"]["cited_not_listed"]) +
                              len(result["crosscheck"]["listed_not_cited"])) if result.get("crosscheck") else 0,
    }

    return result


# ================================================================ 사용 기록(통계)

USAGE_PATH = APP_DIR / "usage_log.json"
_USAGE_LOCK = threading.Lock()
DEFAULT_ORGS = ["한국도서관정보학회", "한국문헌정보학회", "한국비블리아학회", "한국정보관리학회"]


def _load_usage_unlocked() -> list[dict]:
    if USAGE_PATH.exists():
        try:
            import json
            return json.loads(USAGE_PATH.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []


def _load_usage() -> list[dict]:
    with _USAGE_LOCK:
        return _load_usage_unlocked()


def _append_usage(record: dict):
    import json
    import os
    with _USAGE_LOCK:
        data = _load_usage_unlocked()
        data.append(record)
        tmp = USAGE_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, USAGE_PATH)  # 원자적 교체 — 기록 유실 방지


def _record_job_usage(job: dict, options: dict):
    """잡 완료 시 사용 기록 저장."""
    try:
        refs = sum((r.get("summary") or {}).get("total", 0) for r in job["results"] if r)
        _append_usage({
            "time": time.strftime("%Y-%m-%d %H:%M"),
            "user": options.get("user_name", ""),
            "org": options.get("org", ""),
            "mode": job.get("mode", ""),
            "files": job.get("done_files", 0),
            "refs": refs,
            "style": options.get("style_id", ""),
            "ai": aiengine.is_configured(),
            "verify": bool(options.get("verify")),
            "secs": max(1, int(time.time() - job.get("created", time.time()))),
        })
    except Exception:
        pass  # 통계 기록 실패가 처리 자체를 막지 않도록


def _estimate_secs(n_files: int, options: dict) -> tuple[int, int, str]:
    """과거 사용 통계로 예상 처리 시간(초) 추정 — 4편씩 동시 처리(웨이브) 기준.

    (전체 예상초, 첫 묶음(최대 4편) 예상초, 근거 설명) 반환.
    """
    import math
    ai = aiengine.is_configured()
    verify = bool(options.get("verify"))
    samples: list[float] = []
    for r in reversed(_load_usage()):  # 최신 기록부터 동일 조건 표본 수집
        if not r.get("secs") or not r.get("files"):
            continue
        if bool(r.get("ai")) != ai or bool(r.get("verify", verify)) != verify:
            continue
        waves = max(1, math.ceil(r["files"] / MAX_PARALLEL))
        samples.append(r["secs"] / waves)
        if len(samples) >= 30:
            break
    if samples:
        samples.sort()
        per_wave = samples[len(samples) // 2]  # 중앙값 — 극단값에 강함
        basis = f"지난 {len(samples)}회 사용 통계 기반"
    else:
        per_wave = (150 if verify else 80) if ai else (25 if verify else 8)
        basis = "기본 추정 (사용 기록이 쌓이면 자동으로 정확해집니다)"
    total = per_wave * math.ceil(max(1, n_files) / MAX_PARALLEL)
    return max(15, int(total)), max(15, int(per_wave)), basis


@app.get("/api/orgs")
def get_orgs():
    return {"orgs": DEFAULT_ORGS}


def _agg_by_period(data: list[dict], width: int) -> list[dict]:
    """기간별 집계. width=7 → 월(YYYY-MM), width=10 → 일(YYYY-MM-DD). 최신순."""
    agg: dict[str, dict] = {}
    for r in data:
        key = (r.get("time") or "")[:width]
        if len(key) < width:
            continue
        a = agg.setdefault(key, {"period": key, "uses": 0, "files": 0, "refs": 0, "users": set()})
        a["uses"] += 1
        a["files"] += r.get("files", 0)
        a["refs"] += r.get("refs", 0)
        if r.get("user"):
            a["users"].add(r["user"])
    return [{"period": a["period"], "uses": a["uses"], "files": a["files"],
             "refs": a["refs"], "users": len(a["users"])}
            for a in sorted(agg.values(), key=lambda x: x["period"], reverse=True)]


@app.get("/api/admin/stats")
def admin_stats(request: Request):
    """사용 통계 — 관리자는 전체, 편집위원·위원장은 자기 학회분만."""
    scope_org, _role = require_editor(request)
    data = _load_usage()
    if scope_org:
        data = [r for r in data if r.get("org") == scope_org]
    by_org: dict[str, dict] = {}
    for r in data:
        org = r.get("org") or "(미입력)"
        agg = by_org.setdefault(org, {"org": org, "uses": 0, "files": 0, "refs": 0, "users": set()})
        agg["uses"] += 1
        agg["files"] += r.get("files", 0)
        agg["refs"] += r.get("refs", 0)
        if r.get("user"):
            agg["users"].add(r["user"])
    org_rows = []
    for agg in sorted(by_org.values(), key=lambda a: -a["uses"]):
        org_rows.append({"org": agg["org"], "uses": agg["uses"], "files": agg["files"],
                         "refs": agg["refs"], "users": len(agg["users"])})
    return {"scope_org": scope_org, "total_uses": len(data),
            "total_files": sum(r.get("files", 0) for r in data),
            "total_refs": sum(r.get("refs", 0) for r in data),
            "by_org": org_rows,
            "by_month": _agg_by_period(data, 7),
            "by_day": _agg_by_period(data, 10)[:60],
            "recent": list(reversed(data[-50:]))}


# ================================================================ 잡 관리

_JOB_TTL = 2 * 3600  # 완료된 잡 보존 시간


def _new_job(mode: str) -> dict:
    job = {"id": uuid.uuid4().hex[:12], "mode": mode, "status": "running",
           "stage": "대기", "current_file": "", "done_files": 0, "total_files": 0,
           "results": [], "error": "", "output_dir": "", "created": time.time()}
    with _JOBS_LOCK:
        # 오래된 완료 잡 정리(메모리 무한 증가 방지)
        cutoff = time.time() - _JOB_TTL
        for jid in [k for k, v in JOBS.items()
                    if v.get("status") in ("done", "error") and v.get("created", 0) < cutoff]:
            JOBS.pop(jid, None)
        JOBS[job["id"]] = job
    return job


MAX_PARALLEL = 4  # 동시 처리 논문 수 — API 호출 한도·서버 사양(1GB) 보호


def _run_parallel(job: dict, files: list[tuple[str, bytes]], options: dict,
                  on_done=None):
    """여러 논문을 MAX_PARALLEL 편씩 동시 처리. 결과는 입력 순서대로 job['results']에."""
    from concurrent.futures import ThreadPoolExecutor
    job["total_files"] = len(files)
    job["results"] = [None] * len(files)
    job["files"] = [{"name": n, "status": "대기", "stage": ""} for n, _ in files]

    def work(i: int, name: str, data: bytes):
        st = job["files"][i]
        st["status"] = "처리 중"

        def progress(stage, filename):
            st["stage"] = stage
            done = sum(1 for r in job["results"] if r is not None)
            running = sum(1 for f in job["files"] if f["status"] == "처리 중")
            job["stage"] = f"{done}/{job['total_files']} 완료 · 동시 처리 {running}건"
            job["current_file"] = filename

        try:
            res = process_file(name, data, options, progress)
        except Exception as ex:
            res = {"filename": name, "error": f"처리 중 오류: {ex}",
                   "warnings": [], "items": [], "summary": {}}
        job["results"][i] = res
        st["status"] = "오류" if res.get("error") else "완료"
        st["stage"] = ""
        if res.get("history_id"):  # 원본 파일 보관(이력에 연결)
            try:
                history_mod.attach_file(res["history_id"], name, data)
            except Exception:
                pass
        if on_done:
            try:
                on_done(i, name, res)
            except Exception:
                pass
        job["done_files"] = sum(1 for r in job["results"] if r is not None)

    with ThreadPoolExecutor(max_workers=MAX_PARALLEL) as pool:
        futures = [pool.submit(work, i, n, d) for i, (n, d) in enumerate(files)]
        for f in futures:
            f.result()


def _run_upload_job(job: dict, files: list[tuple[str, bytes]], options: dict):
    try:
        _run_parallel(job, files, options)
        job["status"] = "done"
        job["stage"] = "완료"
        _record_job_usage(job, options)
    except Exception as ex:
        job["status"] = "error"
        job["error"] = f"처리 중 오류: {ex}"


def _run_folder_job(job: dict, folder: Path, options: dict):
    try:
        targets = [p for p in sorted(folder.iterdir())
                   if p.is_file() and p.suffix.lower() in parsing.SUPPORTED_EXTS
                   and not p.name.startswith("~$")]
        if not targets:
            job["status"] = "error"
            job["error"] = "폴더에서 지원 형식(HWPX·DOCX·PDF·TXT) 파일을 찾지 못했습니다."
            return
        out_dir = folder / "참고문헌_정리결과"
        out_dir.mkdir(exist_ok=True)
        job["output_dir"] = str(out_dir)

        oversized = [p for p in targets if p.stat().st_size > 10 * 1024 * 1024]
        targets = [p for p in targets if p not in oversized]

        def save_docx(i, name, res):
            if not res.get("error"):
                (out_dir / f"{Path(name).stem}_참고문헌정리.docx").write_bytes(
                    report.build_result_docx(res))

        _run_parallel(job, [(p.name, p.read_bytes()) for p in targets], options,
                      on_done=save_docx)
        for p in oversized:  # 10MB 초과 파일은 오류로 안내
            job["results"].append({"filename": p.name, "warnings": [], "items": [], "summary": {},
                                   "error": "파일이 10MB를 넘습니다. PDF로 변환하거나 그림 해상도를 낮춰 다시 시도해 주세요."})
            job["files"].append({"name": p.name, "status": "오류", "stage": ""})
            job["total_files"] += 1
            job["done_files"] += 1
        summary = report.build_batch_report_docx([r for r in job["results"] if r], str(folder))
        (out_dir / "종합리포트.docx").write_bytes(summary)
        job["status"] = "done"
        job["stage"] = "완료"
        _record_job_usage(job, options)
    except Exception as ex:
        job["status"] = "error"
        job["error"] = f"처리 중 오류: {ex}"


def _run_case_job(job: dict, journal: str, files: list[tuple[str, bytes]]):
    """사례 논문 등록: 발행 논문에서 참고문헌을 추출해 관행 패턴을 분석하고
    '논문 사례를 통한 제안' 초안(미사용 상태)으로 축적한다."""
    def progress(stage, filename):
        job["stage"], job["current_file"] = stage, filename
    try:
        job["total_files"] = len(files)
        for name, data in files:
            res = {"filename": name, "error": "", "n_refs": 0, "n_drafts": 0}
            try:
                progress("파일 파싱", name)
                text = parsing.extract_text(name, data)
                body, section = extract.find_reference_section(text)
                if not section:
                    probe = extract.split_entries(text)
                    if len(probe) >= 3:
                        section = text
                    else:
                        raise ValueError("참고문헌 구역을 찾을 수 없습니다.")
                progress("문헌 추출·분리", name)
                raws = []
                try:
                    raws = aiengine.split_entries_ai(section)
                except aiengine.AIError:
                    pass
                if not raws:
                    raws = extract.split_entries(section)
                if not raws:
                    raise ValueError("참고문헌을 추출하지 못했습니다.")
                res["n_refs"] = len(raws)
                progress(f"관행 패턴 분석 ({len(raws)}건)", name)
                existing = [s.get("rule", "") for s in suggestions_mod.all_suggestions()["case"]]
                drafts = aiengine.analyze_case_refs_ai(journal, raws, existing)
                added = 0
                for d in drafts:
                    if suggestions_mod.is_duplicate_rule("case", d.get("rule", "")):
                        continue
                    suggestions_mod.add_suggestion(
                        "case", topic=d.get("topic", ""), types=d.get("types") or [],
                        rule=d.get("rule", ""), example=d.get("example", ""),
                        evidence=f"{journal} 발행 논문 '{name}' 참고문헌 {len(raws)}건 분석",
                        journal=journal, origin="case_upload", enabled=False)
                    added += 1
                res["n_drafts"] = added
                suggestions_mod.add_corpus_record(journal, name, len(raws), added)
            except (parsing.ParseError, ValueError, aiengine.AIError) as ex:
                res["error"] = str(ex)
            job["results"].append(res)
            job["done_files"] += 1
        job["status"] = "done"
        job["stage"] = "완료"
    except Exception as ex:
        job["status"] = "error"
        job["error"] = f"사례 분석 중 오류: {ex}"


def _run_compare_job(job: dict, hid: str, filename: str, data: bytes,
                     kci_refs: list[str] | None = None):
    """발행본 비교: 처리 이력의 에이전트 결과와 발행본 참고문헌을 짝지어 차이를 찾는다.

    kci_refs가 주어지면 파일 대신 KCI에서 가져온 참고문헌 목록을 발행본으로 사용한다.
    """
    def progress(stage, fn):
        job["stage"], job["current_file"] = stage, fn
    try:
        job["total_files"] = 1
        rec = history_mod.get_history(hid)
        if not rec:
            job["status"] = "error"
            job["error"] = "선택한 처리 이력을 찾을 수 없습니다."
            return
        use_ai = aiengine.is_configured()
        if kci_refs is not None:
            progress("KCI 발행본 참고문헌 사용", filename)
            raws = kci_refs
        else:
            progress("발행본 참고문헌 추출", filename)
            try:
                raws = compare_mod.extract_published_refs(
                    filename, data, split_ai=aiengine.split_entries_ai if use_ai else None)
            except (parsing.ParseError, ValueError) as ex:
                job["status"] = "error"
                job["error"] = str(ex)
                return
        progress(f"항목 대조 (에이전트 {len(rec['items'])}건 ↔ 발행본 {len(raws)}건)", filename)
        cmp = compare_mod.align(rec["items"], raws)
        diffs = [p for p in cmp["pairs"] if not p["same"]]
        drafts: dict[int, dict] = {}
        if diffs and use_ai:
            progress(f"차이 분석·제안 초안 작성 ({len(diffs)}건)", filename)
            try:
                drafts = aiengine.draft_rules_from_diffs_ai(diffs[:40])
            except aiengine.AIError:
                drafts = {}
        for p in diffs:
            p["draft"] = drafts.get(p["pair_id"]) or compare_mod.fallback_draft(p)
            p["draft"]["evidence"] = (f"발행본 비교: {rec.get('filename', '')} ↔ {filename} "
                                      f"({time.strftime('%Y-%m-%d')})")
        # 발행본 파일 보관 + 비교 결과 저장(CSV 재다운로드용)
        if data:
            history_mod.attach_published(hid, filename, data)
        history_mod.save_compare(hid, {"published_filename": filename,
                                       "time": time.strftime("%Y-%m-%d %H:%M"),
                                       **cmp})
        job["results"].append({
            "filename": filename, "error": "",
            "history": {k: rec.get(k, "") for k in ("id", "time", "filename", "user", "org")},
            "compare": cmp,
        })
        job["done_files"] = 1
        job["status"] = "done"
        job["stage"] = "완료"
    except Exception as ex:
        job["status"] = "error"
        job["error"] = f"발행본 비교 중 오류: {ex}"


def _job_public(job: dict) -> dict:
    return {k: v for k, v in job.items() if k != "created"}


# ================================================================ API

@app.get("/", response_class=HTMLResponse)
def index():
    return (APP_DIR / "static" / "index.html").read_text(encoding="utf-8")


@app.get("/api/status")
def status(request: Request):
    cfg = aiengine.load_config()
    admin = is_admin(request)
    out = {"version": APP_VERSION,
           "ai": aiengine.is_configured(), "model": aiengine.get_model(),
           "models": aiengine.ALLOWED_MODELS,
           "admin": admin, "admin_configured": admin_configured(),
           "access_required": access_required(), "access_ok": has_access(request),
           "access_org": access_org(request),
           "role": access_role(request),
           "role_label": ROLE_LABEL.get(access_role(request), ""),
           "is_editor": is_editor(request)}
    if admin:  # 키 관련 정보는 관리자에게만 노출
        out["key_source"] = cfg.get("key_source", "user" if cfg.get("api_key") else "")
        out["key_hint"] = cfg.get("api_key", "")[:10] + "…" if cfg.get("api_key") else ""
        out["access_code"] = _access_code()
        out["monthly_budget_usd"] = cfg.get("monthly_budget_usd", "")
        out["usd_krw"] = cfg.get("usd_krw", 1400)
        out["access_codes"] = _org_access_codes()
        rc = _role_codes()
        out["editor_codes"] = rc["editor"]
        out["chair_codes"] = rc["chair"]
        out["default_orgs"] = DEFAULT_ORGS
        import verify_kr
        out["kr_apis"] = verify_kr.kr_api_status()
    return out


@app.post("/api/settings")
def save_settings(request: Request, api_key: str = Form(""), model: str = Form(aiengine.DEFAULT_MODEL),
                  clear: str = Form("0"), access_code: str | None = Form(None),
                  access_codes: str | None = Form(None),
                  editor_codes: str | None = Form(None),
                  chair_codes: str | None = Form(None),
                  monthly_budget_usd: str | None = Form(None),
                  usd_krw: str | None = Form(None)):
    require_admin(request)
    cfg = aiengine.load_config()
    if clear == "1":
        cfg.pop("api_key", None)
        cfg.pop("key_source", None)
    elif api_key.strip():
        cfg["api_key"] = api_key.strip()
        cfg["key_source"] = "user"
    cfg["model"] = model if model in aiengine.ALLOWED_MODELS else aiengine.DEFAULT_MODEL
    if access_code is not None:  # 필드가 전송된 경우에만 변경(빈 값 = 공통 코드 해제)
        cfg["access_code"] = access_code.strip()[:60]
    for field, raw in (("monthly_budget_usd", monthly_budget_usd), ("usd_krw", usd_krw)):
        if raw is None:
            continue
        raw = raw.strip().replace(",", "")
        if not raw:
            cfg.pop(field, None)
            continue
        try:
            cfg[field] = max(0.0, float(raw))
        except ValueError:
            return JSONResponse({"ok": False, "message": "예산·환율은 숫자로 입력해 주세요."},
                                status_code=400)
    # 학회별·역할별 코드 {학회명: 코드} JSON — 전송된 필드만 갱신
    import json
    for key, raw in (("access_codes", access_codes),
                     ("editor_codes", editor_codes),
                     ("chair_codes", chair_codes)):
        if raw is None:
            continue
        try:
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise ValueError
        except (json.JSONDecodeError, ValueError):
            return JSONResponse({"ok": False, "message": "코드 형식이 올바르지 않습니다."},
                                status_code=400)
        cleaned = {}
        for org, code in data.items():
            org, code = str(org).strip()[:60], str(code).strip()[:60]
            if org and code:
                cleaned[org] = code
        cfg[key] = cleaned
    # 전체 코드 중복 검사 — 같은 코드가 두 역할·두 학회에 쓰이면 소속·권한이 모호해짐
    all_codes = []
    if cfg.get("access_code", "").strip():
        all_codes.append(cfg["access_code"].strip())
    for key in ("access_codes", "editor_codes", "chair_codes"):
        all_codes += [c for c in (cfg.get(key) or {}).values() if c]
    if len(set(all_codes)) != len(all_codes):
        return JSONResponse(
            {"ok": False, "message": "겹치는 코드가 있습니다. 모든 접근 코드(학회별·역할별·공통)는 서로 달라야 합니다."},
            status_code=400)
    aiengine.save_config(cfg)
    return {"ok": True, "ai": aiengine.is_configured(), "model": aiengine.get_model(),
            "access_required": access_required()}


@app.post("/api/settings/test")
def test_settings(request: Request, api_key: str = Form(""), model: str = Form(aiengine.DEFAULT_MODEL)):
    require_admin(request)
    key = api_key.strip() or aiengine.load_config().get("api_key", "")
    if not key:
        return {"ok": False, "message": "API 키를 입력해 주세요."}
    ok, msg = aiengine.test_key(key, model)
    return {"ok": ok, "message": msg}


@app.get("/api/styles")
def get_styles():
    return {"styles": styles_mod.list_styles()}


@app.get("/api/sources")
def get_sources():
    """검증에 사용하는 정보원(국내·해외) 목록과 현재 연결 상태 — 이용자에게 공개."""
    import verify_kr
    kr = verify_kr.kr_api_status()
    return {
        "domestic": [
            {"name": "KCI (한국학술지인용색인)",
             "role": "국내 학술지 논문 실존·서지 대조, 학술지 등재 여부, 발행본 참고문헌 조회",
             "state": "on" if kr.get("kci") else "off"},
            {"name": "국립중앙도서관 서지정보(SEOJI)", "role": "국내 단행본 ISBN·서지 대조",
             "state": "on" if kr.get("nlk") else "off"},
            {"name": "국회도서관 국가학술정보", "role": "학위논문 등 국내 자료 대조",
             "state": "on" if kr.get("nanet") else "off"},
        ],
        "overseas": [
            {"name": "Crossref", "role": "DOI 조회·서지 대조, 철회(Retraction)·정정 정보", "state": "on"},
            {"name": "OpenAlex", "role": "Crossref 미등록 문헌 보조 대조", "state": "on"},
            {"name": "Semantic Scholar", "role": "제목·저자 기반 논문 매칭", "state": "on"},
            {"name": "DataCite", "role": "데이터셋·보고서 등 비학술지 DOI 대조", "state": "on"},
            {"name": "DOAJ", "role": "오픈액세스 학술지 등재 여부(학술지 신뢰성)", "state": "on"},
            {"name": "URL 접속 확인", "role": "웹 자원 링크 유효성 점검", "state": "on"},
        ],
        "note": ("국내 문헌은 KCI·국립중앙도서관·국회도서관에서, 해외 문헌은 Crossref를 시작으로 "
                 "OpenAlex·Semantic Scholar·DataCite 순서로 대조합니다. "
                 "국내 문헌이라도 DOI가 있으면 해외 정보원에서도 함께 확인합니다."),
    }


@app.get("/api/rules")
def get_rules(style_id: str = "munpyeonhyeop"):
    """선택한 기준에 추가로 적용되는 규칙(관리자 추가 기준·편집 지침) — 이용자 확인용."""
    builtin = style_id == styles_mod.BUILTIN_STYLE["id"]
    standards = suggestions_mod.enabled_standards() if builtin else []
    return {"style_id": style_id,
            "standards": [{"rule": s["rule"], "example": s.get("example", "")} for s in standards],
            "directives": feedback_mod.directives_for(style_id)}


@app.post("/api/styles")
async def add_style(request: Request, name: str = Form(""), url: str = Form(""), text: str = Form(""),
                    file: UploadFile | None = File(None)):
    require_admin(request)
    try:
        if file is not None and file.filename:
            data = await file.read()
            st = styles_mod.add_style_from_file(name or Path(file.filename).stem, file.filename, data)
        elif url.strip():
            st = styles_mod.add_style_from_url(name, url.strip())
        elif text.strip():
            st = styles_mod.add_style_from_text(name, text)
        else:
            raise ValueError("기준 파일, URL, 직접 입력 중 하나를 제공해 주세요.")
    except ValueError as ex:
        return JSONResponse({"ok": False, "message": str(ex)}, status_code=400)
    return {"ok": True, "style": {"id": st["id"], "name": st["name"]}}


@app.delete("/api/styles/{style_id}")
def remove_style(request: Request, style_id: str):
    require_admin(request)
    if style_id == styles_mod.BUILTIN_STYLE["id"]:
        raise HTTPException(400, "기본 기준은 삭제할 수 없습니다.")
    return {"ok": styles_mod.delete_style(style_id)}


def _parse_options(style_id: str, verify: str, crosscheck: str, english: str,
                   user_name: str = "", org: str = "", org_etc: str = "",
                   autofix: str = "0", apply_extra: str = "1") -> dict:
    org = org.strip()
    if org == "기타":
        org = org_etc.strip() or "기타(미기입)"
    if not user_name.strip():
        raise HTTPException(400, "사용자 이름을 입력해 주세요.")
    if not org:
        raise HTTPException(400, "사용 기관(학회)을 선택해 주세요.")
    return {"style_id": style_id or "munpyeonhyeop",
            "verify": verify == "1", "crosscheck": crosscheck == "1", "english": english == "1",
            "autofix": autofix == "1" and verify == "1",
            "apply_extra": apply_extra == "1",
            "user_name": user_name.strip()[:40], "org": org[:60]}


@app.post("/api/process")
async def process_upload(request: Request, files: list[UploadFile] = File(...),
                         style_id: str = Form("munpyeonhyeop"),
                         verify: str = Form("1"), crosscheck: str = Form("1"),
                         english: str = Form("0"), autofix: str = Form("0"),
                         apply_extra: str = Form("1"),
                         user_name: str = Form(""), org: str = Form(""), org_etc: str = Form("")):
    require_access(request)
    payload = []
    for f in files:
        data = await f.read()
        if len(data) > 10 * 1024 * 1024:
            raise HTTPException(
                400, f"{f.filename}: 파일이 10MB를 넘습니다. 참고문헌 추출에는 텍스트만 필요하므로 "
                     "PDF로 변환하거나 그림 해상도를 낮춰 다시 올려 주세요.")
        payload.append((f.filename, data))
    if not payload:
        raise HTTPException(400, "업로드된 파일이 없습니다.")
    options = _parse_options(style_id, verify, crosscheck, english, user_name, org, org_etc,
                             autofix, apply_extra)
    a_org = access_org(request)
    if a_org:  # 학회별 접근 코드로 인증된 경우 — 코드가 식별한 학회를 우선(통계 신뢰성)
        options["org"] = a_org
    eta, eta_first, eta_basis = _estimate_secs(len(payload), options)
    job = _new_job("upload")
    threading.Thread(target=_run_upload_job, args=(job, payload, options), daemon=True).start()
    return {"job_id": job["id"], "eta_secs": eta, "eta_first": eta_first,
            "eta_basis": eta_basis, "n_files": len(payload)}


@app.post("/api/process_folder")
def process_folder(request: Request, path: str = Form(...), style_id: str = Form("munpyeonhyeop"),
                   verify: str = Form("1"), crosscheck: str = Form("1"),
                   english: str = Form("0"), autofix: str = Form("0"),
                   apply_extra: str = Form("1"),
                   user_name: str = Form(""), org: str = Form(""), org_etc: str = Form("")):
    # 서버 컴퓨터의 로컬 폴더를 읽는 기능이므로 관리자 전용(외부 공개 시 경로 노출 방지)
    require_admin(request)
    folder = Path(path.strip().strip('"'))
    if not folder.is_dir():
        raise HTTPException(400, f"폴더를 찾을 수 없습니다: {folder}")
    options = _parse_options(style_id, verify, crosscheck, english, user_name, org, org_etc,
                             autofix, apply_extra)
    try:
        n_files = len([p for p in folder.iterdir()
                       if p.is_file() and p.suffix.lower() in parsing.SUPPORTED_EXTS
                       and not p.name.startswith("~$")])
    except OSError:
        n_files = 1
    eta, eta_first, eta_basis = _estimate_secs(n_files, options)
    job = _new_job("folder")
    threading.Thread(target=_run_folder_job, args=(job, folder, options), daemon=True).start()
    return {"job_id": job["id"], "eta_secs": eta, "eta_first": eta_first,
            "eta_basis": eta_basis, "n_files": n_files}


@app.get("/api/jobs/{job_id}")
def job_status(job_id: str, request: Request):
    require_access(request)
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "작업을 찾을 수 없습니다.")
    return _job_public(job)


def _get_result(job_id: str, index: int) -> dict:
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "작업을 찾을 수 없습니다.")
    if index < 0 or index >= len(job["results"]) or job["results"][index] is None:
        raise HTTPException(404, "결과를 찾을 수 없습니다(아직 처리 중일 수 있습니다).")
    return job["results"][index]


def _result_download_response(res: dict, fmt: str) -> Response:
    stem = Path(res.get("filename", "결과")).stem
    if fmt == "txt":
        content = report.build_result_txt(res).encode("utf-8-sig")
        return Response(content, media_type="text/plain; charset=utf-8",
                        headers={"Content-Disposition":
                                 f"attachment; filename*=UTF-8''{_quote(stem + '_refs.txt')}"})
    if fmt == "ris":
        content = report.build_ris(res).encode("utf-8-sig")
        return Response(content, media_type="application/x-research-info-systems",
                        headers={"Content-Disposition":
                                 f"attachment; filename*=UTF-8''{_quote(stem + '.ris')}"})
    if fmt == "bib":
        content = report.build_bibtex(res).encode("utf-8")
        return Response(content, media_type="application/x-bibtex",
                        headers={"Content-Disposition":
                                 f"attachment; filename*=UTF-8''{_quote(stem + '.bib')}"})
    content = report.build_result_docx(res)
    return Response(
        content,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition":
                 f"attachment; filename*=UTF-8''{_quote(stem + '_참고문헌정리.docx')}"})


@app.get("/api/jobs/{job_id}/download/{index}")
def download_result(job_id: str, index: int, request: Request, fmt: str = "docx"):
    require_access(request)
    return _result_download_response(_get_result(job_id, index), fmt)


# ================================================================ 지난 결과 열람
# 이용자: 학회별 접근 코드로 식별된 자기 학회의 결과만 / 관리자: 전체.

def _viewer_org(request: Request) -> str | None:
    """열람 권한 범위 — None: 전체(관리자), '학회명': 해당 학회만, '': 열람 불가."""
    if is_admin(request):
        return None
    return access_org(request) or ""


@app.get("/api/results")
def list_results(request: Request):
    require_access(request)
    org = _viewer_org(request)
    if org == "":
        return {"org": "", "results": [],
                "message": "지난 결과 열람은 학회별 접근 코드로 입장한 경우에만 가능합니다. "
                           "소속 학회의 접근 코드는 학회 편집위원회에 문의해 주세요."}
    rows = history_mod.list_history()
    if org is not None:
        rows = [h for h in rows if h.get("org") == org]
    return {"org": org or "", "admin": org is None, "results": rows[:200]}


def _get_viewable_record(hid: str, request: Request) -> dict:
    rec = history_mod.get_history(hid)
    if not rec:
        raise HTTPException(404, "저장된 결과를 찾을 수 없습니다.")
    org = _viewer_org(request)
    if org == "" or (org is not None and rec.get("org") != org):
        raise HTTPException(403, "이 결과를 열람할 권한이 없습니다.")
    return rec


@app.get("/api/results/{hid}")
def view_result(hid: str, request: Request):
    require_access(request)
    rec = _get_viewable_record(hid, request)
    return {"meta": {k: rec.get(k, "") for k in ("id", "time", "filename", "user", "org")},
            "result": history_mod.result_view(hid)}


@app.get("/api/results/{hid}/download")
def download_saved_result(hid: str, request: Request, fmt: str = "docx"):
    require_access(request)
    _get_viewable_record(hid, request)
    res = history_mod.result_view(hid)
    if not res:
        raise HTTPException(404, "저장된 결과를 찾을 수 없습니다.")
    return _result_download_response(res, fmt)


@app.get("/api/jobs/{job_id}/download_zip")
def download_zip(job_id: str, request: Request):
    require_access(request)
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "작업을 찾을 수 없습니다.")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for res in job["results"]:
            if not res or res.get("error"):
                continue
            stem = Path(res.get("filename", "결과")).stem
            zf.writestr(f"{stem}_참고문헌정리.docx", report.build_result_docx(res))
            zf.writestr(f"{stem}_refs.txt", report.build_result_txt(res).encode("utf-8-sig"))
            zf.writestr(f"{stem}.ris", report.build_ris(res).encode("utf-8-sig"))
            zf.writestr(f"{stem}.bib", report.build_bibtex(res).encode("utf-8"))
        if len(job["results"]) > 1:
            zf.writestr("종합리포트.docx",
                        report.build_batch_report_docx([r for r in job["results"] if r],
                                                       "업로드 일괄 처리"))
    return Response(buf.getvalue(), media_type="application/zip",
                    headers={"Content-Disposition":
                             "attachment; filename*=UTF-8''%s" % _quote("참고문헌_정리결과.zip")})


def _quote(s: str) -> str:
    from urllib.parse import quote
    return quote(s)


# ================================================================ 편집 요구·편집 지침

@app.post("/api/feedback")
def submit_feedback(request: Request, user_name: str = Form(""), org: str = Form(""),
                    style_id: str = Form(""), style_name: str = Form(""),
                    raw: str = Form(""), formatted: str = Form(""),
                    request_text: str = Form("")):
    """이용자의 편집 요구 제출 — 즉시 반영되지 않고 관리자 검토 대기열에 저장."""
    require_access(request)
    try:
        rec = feedback_mod.add_feedback(user_name, org, style_id, style_name,
                                        raw, formatted, request_text)
    except ValueError as ex:
        return JSONResponse({"ok": False, "message": str(ex)}, status_code=400)
    return {"ok": True, "id": rec["id"],
            "message": "편집 요구가 접수되었습니다. 관리자 검토 후 작성 규정에 반영됩니다."}


@app.get("/api/admin/feedback")
def admin_feedback(request: Request):
    require_admin(request)
    styles_by_id = {s["id"]: s["name"] for s in styles_mod.list_styles()}
    return {"feedback": list(reversed(feedback_mod.list_feedback())),
            "directives": feedback_mod.all_directives(),
            "style_names": styles_by_id}


@app.post("/api/admin/feedback/{fid}/resolve")
def admin_resolve_feedback(fid: str, request: Request,
                           action: str = Form(...), directive_text: str = Form(""),
                           style_id: str = Form("")):
    """action='반영'이면 directive_text를 해당 기준의 편집 지침으로 확정 추가."""
    require_admin(request)
    if action not in ("반영", "보류"):
        raise HTTPException(400, "action은 '반영' 또는 '보류'여야 합니다.")
    if action == "반영":
        if not directive_text.strip():
            return JSONResponse({"ok": False, "message": "반영하려면 편집 지침 내용을 입력해 주세요."},
                                status_code=400)
        try:
            feedback_mod.add_directive(style_id or "munpyeonhyeop", directive_text, from_feedback=fid)
        except ValueError as ex:
            return JSONResponse({"ok": False, "message": str(ex)}, status_code=400)
    rec = feedback_mod.resolve_feedback(fid, action, directive_text)
    if not rec:
        raise HTTPException(404, "해당 편집 요구를 찾을 수 없습니다.")
    return {"ok": True}


@app.post("/api/admin/directives")
def admin_add_directive(request: Request, style_id: str = Form(...), text: str = Form(...)):
    """피드백 없이 관리자가 직접 편집 지침 추가."""
    require_admin(request)
    try:
        feedback_mod.add_directive(style_id, text)
    except ValueError as ex:
        return JSONResponse({"ok": False, "message": str(ex)}, status_code=400)
    return {"ok": True}


@app.delete("/api/admin/directives/{style_id}/{index}")
def admin_remove_directive(style_id: str, index: int, request: Request):
    require_admin(request)
    return {"ok": feedback_mod.remove_directive(style_id, index)}


# ================================================================ 관리자 대시보드

def _cost_summary() -> dict:
    """월 예산·환율 설정을 반영한 비용 요약(관리자 전용)."""
    cfg = aiengine.load_config()
    try:
        budget = float(cfg.get("monthly_budget_usd") or 0)
    except (TypeError, ValueError):
        budget = 0.0
    try:
        rate = float(cfg.get("usd_krw") or 1400)
    except (TypeError, ValueError):
        rate = 1400.0
    return cost_mod.summary(budget, rate)


@app.get("/api/admin/costs")
def admin_costs(request: Request):
    """API 사용 비용 상세 — 관리자 전용. 건별 내역은 처리 이력에서 가져온다."""
    require_admin(request)
    out = _cost_summary()
    out["per_job"] = [
        {k: h.get(k, "") for k in ("id", "time", "filename", "user", "org", "total", "cost_usd")}
        for h in history_mod.list_history()[:100]
    ]
    priced = [j for j in out["per_job"] if j.get("cost_usd")]
    out["avg_job_usd"] = round(sum(j["cost_usd"] for j in priced) / len(priced), 6) if priced else 0.0
    out["prices"] = {m: {"input": p[0], "output": p[1]} for m, p in cost_mod.PRICES.items()}
    return out


@app.get("/api/admin/dashboard")
def admin_dashboard(request: Request):
    """관리자 첫 화면 요약 — 확인할 일·이번 달 사용 현황·시스템 상태."""
    require_admin(request)
    pending_fb = sum(1 for f in feedback_mod.list_feedback() if f.get("status") == "접수")
    sugg = suggestions_mod.all_suggestions()
    draft_sugg = sum(1 for s in sugg["case"] + sugg["prof"] if not s.get("enabled"))
    pending_req = sum(1 for r in suggestions_mod.list_requests() if r.get("status") == "대기")
    hist = history_mod.list_history()
    not_compared = sum(1 for h in hist if h.get("has_file") and not h.get("has_published"))

    month = time.strftime("%Y-%m")
    usage = [r for r in _load_usage() if (r.get("time") or "").startswith(month)]
    by_org: dict[str, int] = {}
    for r in usage:
        o = r.get("org") or "(미입력)"
        by_org[o] = by_org.get(o, 0) + 1

    import verify_kr
    return {
        "month": month,
        "todo": {"feedback": pending_fb, "draft_suggestions": draft_sugg,
                 "not_compared": not_compared, "org_requests": pending_req},
        "usage": {"uses": len(usage),
                  "files": sum(r.get("files", 0) for r in usage),
                  "refs": sum(r.get("refs", 0) for r in usage),
                  "by_org": sorted(by_org.items(), key=lambda kv: -kv[1])},
        "cost": _cost_summary(),
        "system": {"ai": aiengine.is_configured(), "model": aiengine.get_model(),
                   "org_codes": len(_org_access_codes()), "common_code": bool(_access_code()),
                   "editor_codes": len(_role_codes()["editor"]),
                   "chair_codes": len(_role_codes()["chair"]),
                   "kr_apis": verify_kr.kr_api_status()},
        "history_total": len(hist),
    }


# ================================================================ 기준·제안 관리 (관리자)

@app.get("/api/admin/knowledge")
def admin_knowledge(request: Request):
    """작성 제안(case/prof/org)·관리자 추가 기준·사례 등록 기록 일괄 조회."""
    require_admin(request)
    data = suggestions_mod.all_suggestions()
    return {"case": list(reversed(data["case"])),
            "prof": list(reversed(data["prof"])),
            "org": list(reversed(data["org"])),
            "standards": list(reversed(suggestions_mod.list_standards())),
            "corpus": list(reversed(suggestions_mod.list_corpus()))[:50],
            "labels": suggestions_mod.SOURCE_LABEL}


@app.post("/api/admin/suggestions")
def admin_add_suggestion(request: Request, source: str = Form(...), topic: str = Form(""),
                         types: str = Form(""), rule: str = Form(...), example: str = Form("")):
    require_admin(request)
    try:
        type_list = [t.strip() for t in types.split(",") if t.strip()]
        rec = suggestions_mod.add_suggestion(source, topic, type_list, rule, example,
                                             origin="manual", enabled=True)
    except ValueError as ex:
        return JSONResponse({"ok": False, "message": str(ex)}, status_code=400)
    return {"ok": True, "id": rec["id"]}


@app.post("/api/admin/suggestions/{sid}/toggle")
def admin_toggle_suggestion(sid: str, request: Request):
    require_admin(request)
    return {"ok": suggestions_mod.toggle_suggestion(sid)}


@app.delete("/api/admin/suggestions/{sid}")
def admin_delete_suggestion(sid: str, request: Request):
    require_admin(request)
    return {"ok": suggestions_mod.delete_suggestion(sid)}


@app.post("/api/admin/standards")
def admin_add_standard(request: Request, rule: str = Form(...), example: str = Form("")):
    require_admin(request)
    try:
        rec = suggestions_mod.add_standard(rule, example)
    except ValueError as ex:
        return JSONResponse({"ok": False, "message": str(ex)}, status_code=400)
    return {"ok": True, "id": rec["id"]}


@app.post("/api/admin/standards/{aid}/toggle")
def admin_toggle_standard(aid: str, request: Request):
    require_admin(request)
    return {"ok": suggestions_mod.toggle_standard(aid)}


@app.delete("/api/admin/standards/{aid}")
def admin_delete_standard(aid: str, request: Request):
    require_admin(request)
    return {"ok": suggestions_mod.delete_standard(aid)}


@app.get("/api/admin/history")
def admin_history(request: Request):
    """원고·처리 이력 목록 — 관리자는 전체, 편집위원은 자기 학회분만."""
    scope_org, role = require_editor(request)
    rows = history_mod.list_history()
    if scope_org:
        rows = [h for h in rows if h.get("org") == scope_org]
    return {"history": rows, "scope_org": scope_org, "role": role,
            "can_delete": role in ("admin", "chair")}


@app.post("/api/admin/cases")
async def admin_add_cases(request: Request, journal: str = Form(...),
                          files: list[UploadFile] = File(...)):
    """사례 논문 등록 — 발행 논문에서 참고문헌 관행을 분석해 제안 초안으로 축적."""
    require_admin(request)
    if not aiengine.is_configured():
        raise HTTPException(400, "사례 논문 분석은 AI 모드(API 키 등록)에서만 가능합니다.")
    journal = journal.strip()
    if not journal:
        raise HTTPException(400, "학회지명을 선택하거나 입력해 주세요.")
    payload = []
    for f in files:
        data = await f.read()
        if len(data) > 50 * 1024 * 1024:
            raise HTTPException(400, f"{f.filename}: 파일이 너무 큽니다(50MB 이하).")
        payload.append((f.filename, data))
    if not payload:
        raise HTTPException(400, "업로드된 파일이 없습니다.")
    job = _new_job("cases")
    threading.Thread(target=_run_case_job, args=(job, journal, payload), daemon=True).start()
    return {"job_id": job["id"]}


@app.post("/api/admin/compare")
async def admin_compare(request: Request, history_id: str = Form(...),
                        file: UploadFile = File(...)):
    """발행본 비교 — 처리 이력과 학회지 발행본의 참고문헌 차이 분석(편집위원 이상)."""
    _require_org_record(history_id, request)
    data = await file.read()
    if len(data) > 50 * 1024 * 1024:
        raise HTTPException(400, "파일이 너무 큽니다(50MB 이하).")
    job = _new_job("compare")
    threading.Thread(target=_run_compare_job,
                     args=(job, history_id, file.filename, data), daemon=True).start()
    return {"job_id": job["id"]}


# ================================================================ 원고 관리 (관리자)

def _require_org_record(hid: str, request: Request) -> tuple[dict, str]:
    """편집위원 이상 + 자기 학회 자료인지 확인. (레코드, 역할) 반환."""
    scope_org, role = require_editor(request)
    rec = history_mod.get_history(hid)
    if not rec:
        raise HTTPException(404, "해당 자료를 찾을 수 없습니다.")
    if scope_org and rec.get("org") != scope_org:
        raise HTTPException(403, "다른 학회의 자료입니다.")
    return rec, role


@app.get("/api/admin/history/{hid}/file")
def admin_history_file(hid: str, request: Request, kind: str = "orig"):
    """보관된 원고 원본(orig) 또는 발행본(published) 다운로드."""
    _require_org_record(hid, request)
    found = history_mod.file_path(hid, "published" if kind == "published" else "orig")
    if not found:
        raise HTTPException(404, "보관된 파일이 없습니다.")
    p, name = found
    return Response(p.read_bytes(), media_type="application/octet-stream",
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{_quote(name)}"})


@app.delete("/api/admin/history/{hid}")
def admin_delete_history(hid: str, request: Request):
    """처리 이력과 보관 파일(원본·발행본) 삭제 — 관리자·편집위원장만."""
    _rec, role = _require_org_record(hid, request)
    if role not in ("admin", "chair"):
        raise HTTPException(403, "삭제는 편집위원장 또는 관리자만 할 수 있습니다.")
    return {"ok": history_mod.delete_history(hid)}


def _csv_response(rows: list[list], filename: str) -> Response:
    import csv
    buf = io.StringIO()
    csv.writer(buf).writerows(rows)
    return Response(buf.getvalue().encode("utf-8-sig"),  # BOM — 엑셀 한글 호환
                    media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{_quote(filename)}"})


@app.get("/api/admin/history_csv")
def admin_history_csv(request: Request):
    """업로드·처리 이력 목록 CSV(엑셀용) — 편집위원은 자기 학회분만."""
    scope_org, _role = require_editor(request)
    hist = history_mod.list_history()
    if scope_org:
        hist = [h for h in hist if h.get("org") == scope_org]
    rows = [["처리일시", "파일명", "이용자", "학회", "적용 기준", "문헌 수",
             "원본 보관", "발행본 보관", "비교일시"]]
    for h in hist:
        rows.append([h.get("time", ""), h.get("filename", ""), h.get("user", ""),
                     h.get("org", ""), h.get("style_name", ""), h.get("total", ""),
                     "O" if h.get("has_file") else "", "O" if h.get("has_published") else "",
                     h.get("compared", "")])
    return _csv_response(rows, f"원고처리이력_{time.strftime('%Y%m%d')}.csv")


@app.get("/api/admin/history_archive")
def admin_history_archive(request: Request):
    """300건을 넘어 밀려난 이력의 영구 아카이브 CSV(엑셀용) 다운로드."""
    require_admin(request)
    if not history_mod.ARCHIVE_PATH.exists():
        raise HTTPException(404, "아직 아카이브된 이력이 없습니다. 이력이 300건을 넘으면 자동으로 생성됩니다.")
    return Response(history_mod.ARCHIVE_PATH.read_bytes(),
                    media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition":
                             f"attachment; filename*=UTF-8''{_quote('이력아카이브.csv')}"})


@app.get("/api/admin/history/{hid}/compare_csv")
def admin_compare_csv(hid: str, request: Request):
    """투고 원고 → Agent → 발행본 3단 비교 결과 CSV(엑셀용)."""
    rec, _role = _require_org_record(hid, request)
    if not rec.get("last_compare"):
        raise HTTPException(404, "저장된 비교 결과가 없습니다. 먼저 발행본 비교를 실행해 주세요.")
    c = rec["last_compare"]
    rows = [["구분", "일치 여부", "투고 원고(편집 단계)", "Agent 결과", "최종 발행본"]]
    for p in c.get("pairs", []):
        rows.append(["짝지음", "일치" if p.get("same") else "차이",
                     p.get("raw", ""), p.get("agent", ""), p.get("published", "")])
    for a in c.get("agent_only", []):
        rows.append(["발행본에 없음", "", a.get("raw", ""), a.get("agent", ""), ""])
    for pub in c.get("published_only", []):
        rows.append(["발행본에만 있음", "", "", "", pub])
    stem = Path(rec.get("filename", "결과")).stem
    return _csv_response(rows, f"{stem}_3단비교_{time.strftime('%Y%m%d')}.csv")


# ================================================================ 학회 채택 요청 (2단계)
# 편집위원이 발행본 비교에서 찾은 차이를 '채택 요청' → 편집위원장(또는 관리자)이 승인하면
# 그 학회의 '○○학회 제안'으로 등록되어 해당 학회 이용자에게 표시된다.

@app.get("/api/org/requests")
def org_requests(request: Request):
    """채택 요청 목록 — 편집위원은 자기 학회, 관리자는 전체."""
    scope_org, role = require_editor(request)
    return {"requests": suggestions_mod.list_requests(scope_org),
            "scope_org": scope_org, "role": role,
            "can_resolve": role in ("chair", "admin")}


@app.post("/api/org/requests")
async def org_add_request(request: Request):
    """편집위원 이상이 규칙 채택을 요청(여러 건 일괄 가능)."""
    scope_org, role = require_editor(request)
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(400, "요청 형식이 올바르지 않습니다.")
    org = scope_org or (payload.get("org") or "").strip()  # 관리자는 대상 학회 지정
    if not org:
        raise HTTPException(400, "대상 학회를 지정해 주세요.")
    requester = (payload.get("requester") or "").strip() or suggestions_mod.SOURCE_LABEL.get("org", "")
    added, skipped = 0, 0
    for it in (payload.get("items") or [])[:50]:
        try:
            suggestions_mod.add_request(
                org, requester or ROLE_LABEL.get(role, ""), it.get("topic", ""),
                [t for t in (it.get("types") or []) if isinstance(t, str)],
                it.get("rule", ""), it.get("example", ""), it.get("evidence", ""))
            added += 1
        except ValueError:
            skipped += 1
    return {"ok": True, "added": added, "skipped": skipped}


@app.post("/api/org/requests/{rid}/resolve")
def org_resolve_request(rid: str, request: Request, action: str = Form(...),
                        note: str = Form(""), resolver: str = Form("")):
    """편집위원장(자기 학회) 또는 관리자가 요청을 승인·반려."""
    scope_org, role = require_editor(request)
    if role not in ("chair", "admin"):
        raise HTTPException(403, "승인·반려는 편집위원장 또는 관리자만 할 수 있습니다.")
    if action not in ("승인", "반려"):
        raise HTTPException(400, "action은 '승인' 또는 '반려'여야 합니다.")
    target = next((r for r in suggestions_mod.list_requests() if r["id"] == rid), None)
    if not target:
        raise HTTPException(404, "요청을 찾을 수 없습니다.")
    if scope_org and target.get("org") != scope_org:
        raise HTTPException(403, "다른 학회의 요청입니다.")
    rec = suggestions_mod.resolve_request(
        rid, action, resolver.strip() or ROLE_LABEL.get(role, ""), note)
    if not rec:
        return JSONResponse({"ok": False, "message": "이미 처리된 요청입니다."}, status_code=400)
    return {"ok": True, "status": rec["status"], "suggestion_id": rec.get("suggestion_id", "")}


@app.delete("/api/org/requests/{rid}")
def org_delete_request(rid: str, request: Request):
    """요청 삭제 — 편집위원장(자기 학회) 또는 관리자."""
    scope_org, role = require_editor(request)
    if role not in ("chair", "admin"):
        raise HTTPException(403, "삭제는 편집위원장 또는 관리자만 할 수 있습니다.")
    target = next((r for r in suggestions_mod.list_requests() if r["id"] == rid), None)
    if not target:
        raise HTTPException(404, "요청을 찾을 수 없습니다.")
    if scope_org and target.get("org") != scope_org:
        raise HTTPException(403, "다른 학회의 요청입니다.")
    return {"ok": suggestions_mod.delete_request(rid)}


@app.get("/api/admin/kci/references")
def admin_kci_references(request: Request, title: str = "", author: str = "", year: str = ""):
    """KCI referenceSearch — 논문명으로 발행본 참고문헌 목록 조회(편집위원 이상)."""
    require_editor(request)
    import verify_kr
    if not verify_kr.kr_api_status().get("kci"):
        raise HTTPException(400, "KCI 인증키가 설정되지 않았습니다. 서버 .env의 KCI_API_KEY를 설정해 주세요.")
    if len(title.strip()) < 4:
        raise HTTPException(400, "논문명을 4자 이상 입력해 주세요.")
    import httpx
    with httpx.Client(headers={"User-Agent": "refstd-agent"}) as client:
        refs = verify_kr.kci_reference_search(client, title.strip(), author.strip(), year.strip())
    if not refs:
        raise HTTPException(404, "KCI에서 이 논문의 참고문헌을 찾지 못했습니다. "
                                 "논문명을 정확히 입력했는지 확인하거나 발행본 파일을 올려 주세요.")
    return {"title": title.strip(), "count": len(refs), "references": refs}


@app.post("/api/admin/compare_kci")
def admin_compare_kci(request: Request, history_id: str = Form(...), title: str = Form(...),
                      author: str = Form(""), year: str = Form("")):
    """KCI에서 발행본 참고문헌을 가져와 3단 비교(발행본 파일 업로드 대체)."""
    _require_org_record(history_id, request)
    import verify_kr
    if not verify_kr.kr_api_status().get("kci"):
        raise HTTPException(400, "KCI 인증키가 설정되지 않았습니다. 서버 .env의 KCI_API_KEY를 설정해 주세요.")
    import httpx
    with httpx.Client(headers={"User-Agent": "refstd-agent"}) as client:
        refs = verify_kr.kci_reference_search(client, title.strip(), author.strip(), year.strip())
    if not refs:
        raise HTTPException(404, "KCI에서 이 논문의 참고문헌을 찾지 못했습니다.")
    job = _new_job("compare")
    threading.Thread(target=_run_compare_job,
                     args=(job, history_id, f"KCI: {title.strip()}", b"", refs),
                     daemon=True).start()
    return {"job_id": job["id"], "count": len(refs)}


@app.post("/api/admin/compare/adopt")
async def admin_compare_adopt(request: Request):
    """비교 결과에서 관리자가 체크한 차이를 '박주현 교수의 추가 제안'으로 등록."""
    require_admin(request)
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(400, "요청 형식이 올바르지 않습니다.")
    added, skipped = 0, 0
    for it in (payload.get("items") or [])[:100]:
        rule = (it.get("rule") or "").strip()
        if len(rule) < 5:
            skipped += 1
            continue
        if suggestions_mod.is_duplicate_rule("prof", rule):
            skipped += 1
            continue
        type_list = [t.strip() for t in (it.get("types") or []) if isinstance(t, str)]
        suggestions_mod.add_suggestion(
            "prof", topic=it.get("topic") or "발행본 비교", types=type_list,
            rule=rule, example=it.get("example", ""), evidence=it.get("evidence", ""),
            origin="compare", enabled=True)
        added += 1
    return {"ok": True, "added": added, "skipped": skipped}
