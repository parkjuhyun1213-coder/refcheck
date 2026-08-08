# -*- coding: utf-8 -*-
"""Claude API 엔진 — 문헌 분리·서지 구조화·사용자 기준 변환·영문 변환.

API 키가 설정된 경우에만 사용되며, 없으면 규칙 엔진(rules.py)이 대신 동작한다.
Claude Opus 5 기본(설정에서 변경 가능). 안전 분류기 거절(refusal)에 대비해
서버측 fallback(기본값 라우팅)을 켠 상태로 호출한다.
"""
import json
import os
import re
from pathlib import Path

CONFIG_PATH = Path(__file__).parent / "config.json"
ENV_PATH = Path(__file__).parent.parent / ".env"
DEFAULT_MODEL = "claude-opus-5"
ALLOWED_MODELS = ["claude-opus-5", "claude-sonnet-5", "claude-haiku-4-5"]

BATCH_SIZE = 12
# 적응형 사고가 켜진 모델에서 max_tokens는 사고 토큰과 출력 토큰의 합산 상한이다.
# 12건×20여 필드의 구조화 출력이 16,000에서 잘리는 사례가 있어 상향했다.
MAX_TOKENS = 32000
MAX_TOKENS_CEILING = 64000  # 절단 시 1회 확대 재시도 상한

_ENV_KEY_NAMES = ("ANTHROPIC_API_KEY", "CLAUDE_API_KEY", "API_KEY")


def _env_all() -> dict:
    """프로젝트 루트 .env 파일의 모든 변수(이름 대문자 기준)."""
    vals = {}
    if ENV_PATH.exists():
        try:
            for line in ENV_PATH.read_text(encoding="utf-8-sig").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                name, _, value = line.partition("=")
                value = value.strip().strip('"').strip("'")
                if value:
                    vals[name.strip().upper()] = value
        except OSError:
            pass
    return vals


def env_get(name: str) -> str:
    """.env 우선, 없으면 OS 환경변수."""
    return _env_all().get(name.upper(), "") or os.environ.get(name, "").strip()


def _key_from_env() -> str:
    """프로젝트 루트의 .env 파일 또는 환경변수에서 API 키를 읽는다."""
    for name in _ENV_KEY_NAMES:
        v = env_get(name)
        if v:
            return v
    return ""


def _env_set(name: str, value: str | None) -> bool:
    """.env의 변수 하나를 추가·수정하거나(value) 삭제한다(value=None)."""
    name = name.upper()
    lines: list[str] = []
    if ENV_PATH.exists():
        try:
            lines = ENV_PATH.read_text(encoding="utf-8-sig").splitlines()
        except OSError:
            return False
    out, done = [], False
    for line in lines:
        head = line.strip()
        if head and not head.startswith("#") and "=" in head \
                and head.partition("=")[0].strip().upper() == name:
            done = True
            if value is not None:
                out.append(f"{name}={value}")
            continue
        out.append(line)
    if not done and value is not None:
        out.append(f"{name}={value}")
    try:
        ENV_PATH.write_text("\n".join(out).rstrip("\n") + "\n", encoding="utf-8")
        os.chmod(ENV_PATH, 0o600)  # 소유자만 읽기 — 윈도우에서는 효과가 없으나 무해
    except OSError:
        return False
    return True


def set_api_key(key: str) -> bool:
    """API 키는 .env에만 저장한다 — config.json에는 절대 기록하지 않는다."""
    return _env_set("ANTHROPIC_API_KEY", key.strip())


def clear_api_key() -> bool:
    ok = True
    for name in _ENV_KEY_NAMES:
        if _env_all().get(name):
            ok = _env_set(name, None) and ok
    return ok


def migrate_key_to_env() -> str:
    """config.json에 평문으로 남은 API 키를 .env로 옮기고 파일에서 제거한다.

    평문 키가 파일로 존재하는 것 자체가 유출 경로(서버 침해·화면 공유·파일 전달)이므로
    로컬·서버 어디서 실행되든 기동 시 한 번 자동으로 정리한다.
    """
    if not CONFIG_PATH.exists():
        return ""
    try:
        cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return ""
    if not (cfg.get("api_key") or "").strip():
        return ""
    moved = False
    if not _key_from_env():  # .env에 키가 없을 때만 옮긴다(.env가 항상 우선)
        if not set_api_key(cfg["api_key"].strip()):
            return "config.json의 평문 API 키를 .env로 옮기지 못했습니다(.env 쓰기 실패)."
        moved = True
    cfg.pop("api_key", None)
    cfg.pop("key_source", None)
    try:
        CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError:
        return "config.json에서 평문 API 키를 제거하지 못했습니다(파일 쓰기 실패)."
    return ("config.json의 평문 API 키를 .env로 옮기고 파일에서 제거했습니다."
            if moved else "config.json에 남아 있던 평문 API 키를 제거했습니다(.env 키 사용).")


def load_config() -> dict:
    cfg = {}
    if CONFIG_PATH.exists():
        try:
            cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            cfg = {}
    env_key = _key_from_env()
    if env_key:  # .env가 항상 우선
        cfg["api_key"] = env_key
        cfg["key_source"] = "env"
    elif (cfg.get("api_key") or "").strip():
        # 이관에 실패해 평문 키가 남은 상태 — 동작은 시키되 관리자 화면에서 경고한다
        cfg["key_source"] = "file"
    return cfg


def save_config(cfg: dict):
    """설정 저장 — API 키는 어떤 경우에도 config.json에 기록하지 않는다."""
    cfg = dict(cfg)
    cfg.pop("api_key", None)
    cfg.pop("key_source", None)
    CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=1), encoding="utf-8")


def get_model() -> str:
    m = load_config().get("model", DEFAULT_MODEL)
    return m if m in ALLOWED_MODELS else DEFAULT_MODEL


def is_configured() -> bool:
    return bool(load_config().get("api_key"))


class AIError(Exception):
    pass


class AITruncated(AIError):
    """응답이 max_tokens에 걸려 잘린 경우 — 조용히 규칙 엔진으로 넘어가지 않도록 구분한다."""


def _client():
    import anthropic
    key = load_config().get("api_key")
    if not key:
        raise AIError("API 키가 설정되지 않았습니다.")
    return anthropic.Anthropic(api_key=key)


def _once(system: str, user: str, schema: dict, max_tokens: int):
    """Claude 1회 호출 — 응답 객체를 그대로 반환."""
    import anthropic
    client = _client()
    kwargs = dict(
        model=get_model(),
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
        output_config={"format": {"type": "json_schema", "schema": schema}},
    )
    # max_tokens가 크면 SDK가 스트리밍을 요구한다(비스트리밍 10분 제한). 항상 스트리밍으로 받는다.
    try:
        try:
            # 안전 분류기 거절 시 권장 모델로 자동 재시도(서버측 fallback)
            with client.beta.messages.stream(
                    betas=["server-side-fallback-2026-07-01"],
                    fallbacks="default", **kwargs) as stream:
                resp = stream.get_final_message()
        except (anthropic.BadRequestError, TypeError) as ex:
            # 베타 미지원 계정 등에서만 일반 경로로 재시도한다.
            # 잔액 부족 같은 '진짜 400'은 그대로 올려보내 원인을 정확히 알린다.
            low = str(ex).lower()
            if isinstance(ex, anthropic.BadRequestError) \
                    and "beta" not in low and "fallback" not in low:
                raise
            with client.messages.stream(**kwargs) as stream:
                resp = stream.get_final_message()
    except anthropic.AuthenticationError:
        raise AIError("API 키가 유효하지 않습니다. 설정에서 키를 확인해 주세요.")
    except anthropic.RateLimitError:
        raise AIError("Claude API 호출 한도에 도달했습니다. 잠시 후 다시 시도해 주세요.")
    except anthropic.APIStatusError as ex:
        detail = f"{getattr(ex, 'message', '')} {ex}".lower()
        if "credit balance" in detail or "insufficient" in detail:
            raise AIError(
                "Anthropic API 크레딧이 소진되어 AI 모드를 사용할 수 없습니다. "
                "console.anthropic.com의 Plans & Billing에서 충전한 뒤 다시 시도해 주세요.")
        raise AIError(f"Claude API 오류({ex.status_code}): {ex.message}")
    except anthropic.APIConnectionError:
        raise AIError("Claude API에 연결할 수 없습니다. 네트워크를 확인해 주세요.")

    try:  # 토큰 사용량 기록(비용 집계) — 실패해도 처리 흐름을 막지 않음
        import cost
        cost.record(get_model(), getattr(resp, "usage", None))
    except Exception:
        pass
    return resp


def _call(system: str, user: str, schema: dict) -> dict:
    """구조화 출력(JSON 스키마)으로 Claude 호출.

    응답이 길이 제한에 걸려 잘리면(stop_reason='max_tokens') 잘린 JSON이 파싱에 실패해
    '해석 불가'로 뭉뚱그려지고 조용히 규칙 엔진 결과로 바뀐다. 절단을 별도로 감지해
    한도를 늘려 1회 재시도하고, 그래도 잘리면 AITruncated로 구분해 알린다.
    """
    limit = MAX_TOKENS
    for attempt in (1, 2):
        resp = _once(system, user, schema, limit)
        if resp.stop_reason == "refusal":
            raise AIError("요청이 안전상 처리되지 않았습니다(규칙 엔진으로 대체 처리됩니다).")
        if resp.stop_reason == "max_tokens":
            if attempt == 1 and limit < MAX_TOKENS_CEILING:
                limit = MAX_TOKENS_CEILING
                continue
            raise AITruncated(
                f"AI 응답이 길이 제한({limit:,}토큰)에 걸려 잘렸습니다. "
                "해당 묶음은 규칙 엔진으로 처리되었습니다.")
        text = next((b.text for b in resp.content if b.type == "text"), "")
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            m = re.search(r"\{.*\}", text, re.S)
            if m:
                try:
                    return json.loads(m.group(0))
                except json.JSONDecodeError:
                    pass
            raise AIError("AI 응답을 해석할 수 없습니다.")
    raise AIError("AI 응답을 받지 못했습니다.")


def test_key(api_key: str, model: str) -> tuple[bool, str]:
    """설정 화면의 키 검증용 소형 호출."""
    import anthropic
    client = anthropic.Anthropic(api_key=api_key)
    try:
        client.messages.create(
            model=model if model in ALLOWED_MODELS else DEFAULT_MODEL,
            max_tokens=32,
            messages=[{"role": "user", "content": "OK라고만 답하세요."}],
        )
        return True, "API 키가 확인되었습니다. AI 모드가 활성화됩니다."
    except anthropic.AuthenticationError:
        return False, "API 키가 유효하지 않습니다."
    except anthropic.PermissionDeniedError:
        return False, "이 키로는 해당 모델을 사용할 권한이 없습니다."
    except anthropic.APIStatusError as ex:
        return False, f"API 오류({ex.status_code}): {ex.message}"
    except anthropic.APIConnectionError:
        return False, "네트워크 연결을 확인해 주세요."


# ---------------------------------------------------------------- 스키마

_ENTRY_PROPS = {
    "type": {"type": "string", "enum": [
        "journal", "book", "book_chapter", "thesis", "report", "newspaper",
        "web", "conference", "law", "standard", "interview", "av", "unknown"]},
    "lang": {"type": "string", "enum": ["ko", "west", "east"]},
    "authors": {"type": "array", "items": {"type": "string"}},
    "author_note": {"type": "string"},
    "year": {"type": "string"},
    "date": {"type": "string"},
    "orig_year": {"type": "string"},
    "title": {"type": "string"},
    "container": {"type": "string"},
    "editors": {"type": "string"},
    "volume": {"type": "string"},
    "issue": {"type": "string"},
    "pages": {"type": "string"},
    "article_no": {"type": "string"},
    "edition": {"type": "string"},
    "place": {"type": "string"},
    "publisher": {"type": "string"},
    "degree": {"type": "string"},
    "institution": {"type": "string"},
    "country": {"type": "string"},
    "report_no": {"type": "string"},
    "doi": {"type": "string"},
    "url": {"type": "string"},
    "medium": {"type": "string"},
    "notes": {"type": "array", "items": {"type": "string"}},
}

_STRUCTURE_SCHEMA = {
    "type": "object",
    "properties": {
        "entries": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": _ENTRY_PROPS,
                "required": list(_ENTRY_PROPS.keys()),
                "additionalProperties": False,
            },
        }
    },
    "required": ["entries"],
    "additionalProperties": False,
}

_SPLIT_SCHEMA = {
    "type": "object",
    "properties": {
        "references": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["references"],
    "additionalProperties": False,
}

_CUSTOM_FORMAT_SCHEMA = {
    "type": "object",
    "properties": {
        "references": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer"},
                    "formatted": {"type": "string"},
                    "group": {"type": "string"},
                    "issues": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["index", "formatted", "group", "issues"],
                "additionalProperties": False,
            },
        },
        "order_note": {"type": "string"},
    },
    "required": ["references", "order_note"],
    "additionalProperties": False,
}

_ENGLISH_SCHEMA = {
    "type": "object",
    "properties": {
        "references": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer"},
                    "formatted": {"type": "string"},
                },
                "required": ["index", "formatted"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["references"],
    "additionalProperties": False,
}


# ---------------------------------------------------------------- 기능

def split_entries_ai(section_text: str) -> list[str]:
    """참고문헌 구역 원문 → 문헌 건별 문자열(흐트러진 줄바꿈 복원 포함)."""
    section_text = section_text[:60000]
    system = (
        "당신은 학술 논문의 참고문헌 목록을 정리하는 전문가입니다. "
        "주어진 참고문헌 구역 원문에서 개별 문헌을 한 건씩 분리하세요. "
        "PDF 추출 과정에서 깨진 줄바꿈과 하이픈 분절을 복원하고, 항목 번호([1], 1. 등)는 제거하되 "
        "문헌 내용 자체는 절대 수정·요약·보완하지 마세요. 참고문헌이 아닌 텍스트(쪽 번호, 머리글, 부록)는 제외하세요."
    )
    result = _call(system, f"<참고문헌_구역>\n{section_text}\n</참고문헌_구역>", _SPLIT_SCHEMA)
    return [r.strip() for r in result.get("references", []) if len(r.strip()) >= 15]


def structure_entries_ai(raws: list[str]) -> list[dict]:
    """문헌 문자열 목록 → 구조화 필드. 배치 처리."""
    from rules import new_entry
    system = (
        "당신은 문헌정보학 서지사항 분석 전문가입니다. 각 참고문헌을 분석해 서지요소를 구조화하세요.\n"
        "규칙:\n"
        "- type: 자료 유형(학술지 논문=journal, 단행본=book, 편집서 장=book_chapter, 학위논문=thesis, "
        "보고서·정부간행물=report, 신문·잡지 기사=newspaper, 웹 자원=web, 학술대회=conference, "
        "법률=law, 표준·특허=standard, 인터뷰=interview, 영상·음반=av, 판단 불가=unknown)\n"
        "- lang: 한국어 문헌=ko, 서양어 문헌=west, 한자·일본어 등 동양 문헌=east\n"
        "- authors: 저자명 배열. 원문에 있는 모든 저자를 기재. 서양 저자는 'Last, F. M.' 형식으로 정규화. "
        "편·역 표시(편, 공편, 옮김, ed., Translated by 등)는 author_note에.\n"
        "- year: 4자리 연도(같은 저자 동일연도 구분자 a,b,c가 있으면 포함). 불명이면 국내 '발행년불명', 해외 'n.d.'. "
        "번역서는 orig_year에 원본 발행년, year에 번역본 발행년.\n"
        "- date: 신문기사·웹자원·학술대회 발표 등 일자가 있는 경우. 국문은 '2020. 5. 25.', 서양은 '2020, October 8' 형식.\n"
        "- title: 논문명·서명·기사명. container: 학술지명·신문명·웹사이트명·논문집명·(장의 경우) 단행본명.\n"
        "- 서양 학술지 논문 title은 문장식 대문자(첫 글자만), 서양 서명·간행물명 container는 각 단어 첫 글자 대문자로 교정.\n"
        "- pages: '105-126' 형식(하이픈). 온라인 학술지 아티클 넘버는 article_no에.\n"
        "- doi: '10.'으로 시작하는 DOI만(URL 접두어 제거). url: 그 외 웹주소.\n"
        "- degree: '석사학위논문'/'박사학위논문'/'Doctoral dissertation'/\"Master's thesis\". institution: 수여기관. "
        "해외 학위논문은 country에 국가명.\n"
        "- 원문에 없는 정보를 만들어내지 마세요. 확인이 필요하거나 누락된 요소는 notes 배열에 "
        "'~ 확인 필요' 형태의 한국어 메모로 기재하세요.\n"
        "- 입력 순서 그대로, 같은 개수의 entries를 반환하세요."
    )
    out: list[dict] = []
    for i in range(0, len(raws), BATCH_SIZE):
        batch = raws[i:i + BATCH_SIZE]
        numbered = "\n".join(f"[{j + 1}] {r}" for j, r in enumerate(batch))
        result = _call(system, numbered, _STRUCTURE_SCHEMA)
        entries = result.get("entries", [])
        for j, raw in enumerate(batch):
            if j < len(entries):
                e = new_entry(raw)
                data = entries[j]
                for k in e:
                    if k in data and data[k]:
                        e[k] = data[k]
                e["raw"] = raw
                if e.get("type") == "book_chapter":
                    e["type"] = "book"
                out.append(e)
            else:
                e = new_entry(raw)
                e["notes"].append("AI 구조화 실패 — 확인 필요")
                out.append(e)
    return out


def format_custom_style_ai(entries: list[dict], style: dict,
                           directives: list[str] | None = None) -> tuple[list[dict], str]:
    """사용자 정의 기준으로 각 문헌을 변환. (references, order_note) 반환.
    references: [{index, formatted, group, issues}] — index는 입력 순서(0부터)."""
    style_text = style.get("text", "")[:30000]
    if directives:
        style_text += ("\n\n[관리자 확정 추가 편집 지침 — 위 기준과 함께 반드시 적용]\n"
                       + "\n".join(f"- {d}" for d in directives))
    system = (
        "당신은 학술지 참고문헌 형식 변환 전문가입니다. 아래 <작성기준>은 특정 학술지의 참고문헌 작성 규정 원문입니다. "
        "이 기준을 정확히 따라 각 문헌의 참고문헌 표기를 작성하세요.\n"
        "규칙:\n"
        "- 기준에 명시된 저자 표기, 연도 위치, 구두점, 대소문자, 이탤릭 표시 불가 시 평문 표기, 정렬 규칙을 그대로 따르세요.\n"
        "- 기준이 문헌 유형별 형식을 정의하면 해당 유형 형식을 적용하고, 기준에 없는 유형은 기준의 전반적 원칙(APA 등)에 맞춰 작성하세요.\n"
        "- formatted: 변환된 참고문헌 한 건의 완성 문자열.\n"
        "- group: 기준의 배열 규칙상 소속 그룹(예: '국내문헌', '서양문헌', '동양문헌', 구분 없으면 '전체').\n"
        "- issues: 기준 적용에 필요한 정보가 원문에 없어 확인이 필요한 사항(한국어, 없으면 빈 배열). "
        "원문에 없는 서지요소를 임의로 만들지 마세요.\n"
        "- references 배열은 기준이 요구하는 최종 배열 순서대로 정렬해 반환하고, 각 항목의 index에 "
        "입력 문헌 번호(0부터)를 기재하세요. order_note에 적용한 정렬 규칙을 한 문장으로 설명하세요."
    )
    payload_entries = []
    for i, e in enumerate(entries):
        payload_entries.append({
            "index": i,
            "원문": e.get("raw", ""),
            "서지요소": {k: v for k, v in e.items()
                     if k not in ("raw", "notes") and v and v != []},
        })
    refs: list[dict] = []
    order_note = ""
    for i in range(0, len(payload_entries), BATCH_SIZE):
        batch = payload_entries[i:i + BATCH_SIZE]
        user = (
            f"<작성기준>\n{style_text}\n</작성기준>\n\n"
            f"<문헌목록>\n{json.dumps(batch, ensure_ascii=False, indent=1)}\n</문헌목록>"
        )
        result = _call(system, user, _CUSTOM_FORMAT_SCHEMA)
        refs.extend(result.get("references", []))
        order_note = result.get("order_note", order_note)
    # 배치 간 순서는 AI가 배치 내에서만 정렬하므로, 전체를 그룹→formatted로 재정렬
    return refs, order_note


def apply_directives_ai(formatted_list: list[str], directives: list[str], style_name: str) -> list[str]:
    """관리자 확정 편집 지침을 이미 변환된 참고문헌 목록에 반영.
    지침과 무관한 부분은 변경하지 않는다. 실패 시 원본 유지."""
    if not formatted_list or not directives:
        return formatted_list
    system = (
        f"당신은 참고문헌 목록 교정 전문가입니다. 아래 목록은 '{style_name}' 기준으로 이미 변환된 참고문헌입니다. "
        "<편집지침>의 내용만 각 항목에 반영해 수정하세요.\n"
        "규칙:\n"
        "- 지침과 무관한 부분(저자, 연도, 제목, 구두점 등)은 한 글자도 변경하지 마세요.\n"
        "- 지침이 해당되지 않는 항목은 원문 그대로 반환하세요.\n"
        "- 입력과 같은 개수의 references를 index 순서대로 반환하세요."
    )
    out = list(formatted_list)
    dir_text = "\n".join(f"- {d}" for d in directives)
    for i in range(0, len(formatted_list), BATCH_SIZE):
        batch = formatted_list[i:i + BATCH_SIZE]
        payload = [{"index": j, "reference": r} for j, r in enumerate(batch)]
        user = (f"<편집지침>\n{dir_text}\n</편집지침>\n\n"
                f"<참고문헌목록>\n{json.dumps(payload, ensure_ascii=False, indent=1)}\n</참고문헌목록>")
        result = _call(system, user, _ENGLISH_SCHEMA)  # {references:[{index, formatted}]}
        for r in result.get("references", []):
            j = r.get("index", -1)
            if 0 <= j < len(batch) and r.get("formatted"):
                out[i + j] = r["formatted"]
    return out


_STANDARDS_SCHEMA = {
    "type": "object",
    "properties": {
        "references": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer"},
                    "formatted": {"type": "string"},
                    "note": {"type": "string"},
                    "conflict": {"type": "string"},
                },
                "required": ["index", "formatted", "note", "conflict"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["references"],
    "additionalProperties": False,
}


def apply_standards_ai(formatted_list: list[str], standards: list[dict],
                       directives: list[str], style_name: str) -> list[dict]:
    """'관리자 추가 기준'(문편협에 준함 — 공백 보완)과 편집 지침을 목록에 반영.

    반환: 입력과 같은 길이의 [{formatted, note, conflict}].
    - 관리자 추가 기준은 문편협 공통기준이 명시하지 않는 공백에만 적용한다.
      문편협 명시 규정과 충돌하면 적용하지 않고 conflict에 사유를 기재한다.
    - 편집 지침은 반드시 적용한다.
    """
    out = [{"formatted": f, "note": "", "conflict": ""} for f in formatted_list]
    if not formatted_list or not (standards or directives):
        return out
    std_text = "\n".join(f"- {s['rule']}" + (f" (예: {s['example']})" if s.get("example") else "")
                         for s in standards) or "(없음)"
    dir_text = "\n".join(f"- {d}" for d in directives) or "(없음)"
    system = (
        f"당신은 참고문헌 목록 교정 전문가입니다. 아래 목록은 '{style_name}'(문편협 공통기준, "
        "2024. 6. 17. 개정, APA 7판 기반) 기준으로 이미 변환된 참고문헌입니다.\n"
        "두 종류의 추가 규칙을 반영해 주세요.\n"
        "1) <관리자추가기준>: 문편협 공통기준에 준하는 기준. 단, 문편협 공통기준이 '명시적으로' 규정한 "
        "사항과 충돌하면 문편협을 우선하여 적용하지 말고, 해당 항목의 conflict에 어떤 기준과 어떻게 "
        "충돌하는지 한 문장으로 기재하세요. 문편협이 다루지 않거나 애매한 공백 부분에만 적용하세요.\n"
        "2) <편집지침>: 관리자가 확정한 지침 — 반드시 적용하세요.\n"
        "규칙:\n"
        "- 규칙과 무관한 부분(저자, 연도, 제목, 구두점 등)은 한 글자도 변경하지 마세요.\n"
        "- 적용한 항목은 note에 어떤 규칙을 적용했는지 짧게 기재하세요(미적용이면 빈 문자열).\n"
        "- 해당 없는 항목은 원문 그대로, note·conflict는 빈 문자열로 반환하세요.\n"
        "- 입력과 같은 개수의 references를 index 순서대로 반환하세요."
    )
    for i in range(0, len(formatted_list), BATCH_SIZE):
        batch = formatted_list[i:i + BATCH_SIZE]
        payload = [{"index": j, "reference": r} for j, r in enumerate(batch)]
        user = (f"<관리자추가기준>\n{std_text}\n</관리자추가기준>\n\n"
                f"<편집지침>\n{dir_text}\n</편집지침>\n\n"
                f"<참고문헌목록>\n{json.dumps(payload, ensure_ascii=False, indent=1)}\n</참고문헌목록>")
        result = _call(system, user, _STANDARDS_SCHEMA)
        for r in result.get("references", []):
            j = r.get("index", -1)
            if 0 <= j < len(batch):
                if r.get("formatted"):
                    out[i + j]["formatted"] = r["formatted"]
                out[i + j]["note"] = r.get("note", "")
                out[i + j]["conflict"] = r.get("conflict", "")
    return out


_MATCH_SCHEMA = {
    "type": "object",
    "properties": {
        "matches": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer"},
                    "suggestion_ids": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["index", "suggestion_ids"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["matches"],
    "additionalProperties": False,
}


def match_suggestions_ai(items_payload: list[dict], pool: list[dict]) -> dict[int, list[str]]:
    """항목별로 관련 있는 작성 제안 id를 매칭.

    items_payload: [{index, formatted, type, issues}]
    pool: suggestions.enabled_suggestions() 결과 [{id, topic, types, rule, ...}]
    반환: {item_index: [suggestion_id, ...]}
    """
    if not items_payload or not pool:
        return {}
    pool_text = json.dumps(
        [{"id": s["id"], "구분": s.get("topic", ""), "적용유형": s.get("types") or "전체",
          "제안": s.get("rule", "")} for s in pool],
        ensure_ascii=False, indent=1)
    system = (
        "당신은 문헌정보학 참고문헌 작성 조력자입니다. <제안목록>은 문편협 공통기준이 명시하지 않거나 "
        "애매한 부분에 대한 보조 작성 제안들입니다. 각 참고문헌 항목에 대해, 그 항목의 '현재 표기'와 "
        "실제로 관련이 있어 저자가 참고할 가치가 있는 제안만 골라 연결하세요.\n"
        "규칙:\n"
        "- 항목의 자료 유형·표기 상태와 무관한 제안은 연결하지 마세요.\n"
        "- 이미 제안대로 작성되어 있는 항목에는 연결하지 마세요(고칠 여지가 있을 때만).\n"
        "- 해당 제안이 하나도 없으면 그 항목은 matches에서 생략해도 됩니다.\n"
        "- suggestion_ids에는 <제안목록>의 id만 사용하세요."
    )
    out: dict[int, list[str]] = {}
    valid_ids = {s["id"] for s in pool}
    for i in range(0, len(items_payload), BATCH_SIZE * 2):
        batch = items_payload[i:i + BATCH_SIZE * 2]
        user = (f"<제안목록>\n{pool_text}\n</제안목록>\n\n"
                f"<참고문헌목록>\n{json.dumps(batch, ensure_ascii=False, indent=1)}\n</참고문헌목록>")
        result = _call(system, user, _MATCH_SCHEMA)
        for m in result.get("matches", []):
            idx = m.get("index", -1)
            ids = [x for x in (m.get("suggestion_ids") or []) if x in valid_ids]
            if idx >= 0 and ids:
                out[idx] = ids
    return out


_CASE_SCHEMA = {
    "type": "object",
    "properties": {
        "suggestions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "topic": {"type": "string"},
                    "types": {"type": "array", "items": {"type": "string"}},
                    "rule": {"type": "string"},
                    "example": {"type": "string"},
                },
                "required": ["topic", "types", "rule", "example"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["suggestions"],
    "additionalProperties": False,
}


def analyze_case_refs_ai(journal: str, raw_refs: list[str],
                         existing_rules: list[str]) -> list[dict]:
    """발행 논문의 참고문헌 목록에서 문편협 공통기준을 보완하는 관행 패턴을 추출.

    반환: [{topic, types, rule, example}] — '논문 사례를 통한 제안' 초안.
    """
    refs_text = "\n".join(f"[{i + 1}] {r}" for i, r in enumerate(raw_refs[:150]))
    existing = "\n".join(f"- {r}" for r in existing_rules[:80]) or "(없음)"
    system = (
        "당신은 문헌정보학 학술지 편집 전문가입니다. <참고문헌목록>은 학술지 "
        f"'{journal}'에 실제 게재된 논문의 참고문헌입니다. 이 학술지는 문편협 "
        "「인용 및 참고문헌의 기술요소와 형식에 관한 공통기준」(2024. 6. 17. 개정, APA 7판 기반)을 따릅니다.\n"
        "임무: 이 목록에서 공통기준이 '명시하지 않거나 애매하게 남겨둔' 부분에 대해 이 학술지가 "
        "실제로 어떤 표기 관행을 쓰는지 패턴을 찾아, 투고자에게 도움이 될 '작성 제안'으로 정리하세요.\n"
        "규칙:\n"
        "- 공통기준에 이미 명시된 규칙(저자 나열, 연도 괄호, 국내→서양→동양 배열 등)을 반복하지 마세요.\n"
        "- <기존제안>과 실질적으로 같은 내용은 제외하세요.\n"
        "- 목록에서 2건 이상 일관되게 관찰되는 패턴만 제안하세요. 1건뿐인 우연한 표기는 제외.\n"
        "- rule: '~한다' 형태의 한국어 제안 한 문장. example: 목록에서 가져온 실제 표기 예 1건.\n"
        "- topic: 짧은 구분명(예: 'DOI 표기', '온라인 자료 접속일', '학위논문').\n"
        "- types: 관련 자료 유형 코드 배열 — journal, book, book_chapter, thesis, report, newspaper, "
        "web, conference, law, standard, interview, av 중에서. 전체에 해당하면 빈 배열.\n"
        "- 확실한 패턴이 없으면 빈 배열을 반환하세요(무리하게 만들지 마세요). 최대 8건."
    )
    user = (f"<기존제안>\n{existing}\n</기존제안>\n\n"
            f"<참고문헌목록>\n{refs_text}\n</참고문헌목록>")
    result = _call(system, user, _CASE_SCHEMA)
    return result.get("suggestions", [])[:8]


_DRAFT_SCHEMA = {
    "type": "object",
    "properties": {
        "rules": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer"},
                    "topic": {"type": "string"},
                    "rule": {"type": "string"},
                    "example": {"type": "string"},
                },
                "required": ["index", "topic", "rule", "example"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["rules"],
    "additionalProperties": False,
}


def draft_rules_from_diffs_ai(pairs: list[dict]) -> dict[int, dict]:
    """에이전트 결과 ↔ 발행본 차이 쌍에서 일반화된 제안 문구 초안을 생성.

    pairs: [{pair_id, agent, published, type}]
    반환: {pair_id: {topic, rule, example}}
    """
    if not pairs:
        return {}
    payload = [{"index": p["pair_id"], "유형": p.get("type", ""),
                "에이전트결과": p.get("agent", ""), "발행본": p.get("published", "")}
               for p in pairs]
    system = (
        "당신은 문헌정보학 학술지 편집 전문가입니다. 각 항목은 참고문헌 자동 정리 결과(에이전트결과)와 "
        "학술지에 최종 게재된 표기(발행본)의 쌍입니다. 두 표기의 차이를 분석해, 앞으로 같은 상황에서 "
        "발행본 쪽 표기를 따르도록 하는 '일반화된 작성 제안'을 만드세요.\n"
        "규칙:\n"
        "- rule: 특정 문헌에 한정되지 않는 일반 규칙 한 문장(한국어, '~한다' 형태). "
        "예: '기관 보고서의 발행처가 저자와 같으면 발행처를 생략한다.'\n"
        "- 차이가 단순 오탈자·서지 정보 차이(연도·면수 등 데이터 차이)이면 rule에 "
        "'[서지 차이] '를 붙이고 그 사실을 기술하세요(형식 규칙이 아님을 표시).\n"
        "- topic: 짧은 구분명. example: 발행본 표기 그대로.\n"
        "- 입력 index를 그대로 유지해 모든 항목에 대해 반환하세요."
    )
    out: dict[int, dict] = {}
    for i in range(0, len(payload), BATCH_SIZE):
        batch = payload[i:i + BATCH_SIZE]
        result = _call(system, json.dumps(batch, ensure_ascii=False, indent=1), _DRAFT_SCHEMA)
        for r in result.get("rules", []):
            idx = r.get("index", -1)
            if idx >= 0 and r.get("rule"):
                out[idx] = {"topic": r.get("topic", ""), "rule": r["rule"],
                            "example": r.get("example", "")}
    return out


def translate_to_english_ai(entries: list[dict]) -> list[dict]:
    """국문 참고문헌 → 영문 변환 목록(문편협 기준 9·10항).
    입력 entries 인덱스 기준 [{index, formatted}] 반환."""
    system = (
        "당신은 한국 학술지 참고문헌의 영문 변환 전문가입니다. 문편협 공통기준 9항·10항에 따라 "
        "국문 참고문헌을 영문 목록으로 변환하세요.\n"
        "규칙:\n"
        "- 해외문헌 기술요소와 형식을 따르되, 저자 이름은 두문자가 아닌 전체 이름을 로마자로 기재 "
        "(예: 홍길동 → Hong, Gildong).\n"
        "- 학술지명·서명·기관명의 공식 영문명을 알고 있으면 그것을 사용하고, 확인할 수 없으면 "
        "저자명(단체명)은 음역(로마자 표기)하고 서명·논문명은 영어로 번역하세요.\n"
        "- 학술지 논문: Author, Fullname (Year). Translated title. Journal Name, vol(iss), pages. DOI\n"
        "- 단행본: Author, Fullname (Year). Translated Title. Place: Publisher.\n"
        "- 원문에 없는 서지요소를 만들지 마세요."
    )
    payload = [{"index": i, "참고문헌": e.get("raw", "")} for i, e in enumerate(entries)]
    out: list[dict] = []
    for i in range(0, len(payload), BATCH_SIZE):
        batch = payload[i:i + BATCH_SIZE]
        result = _call(system, json.dumps(batch, ensure_ascii=False, indent=1), _ENGLISH_SCHEMA)
        out.extend(result.get("references", []))
    return out
