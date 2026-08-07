# -*- coding: utf-8 -*-
"""참고문헌 작성 기준(스타일) 관리.

- 내장 기준: 문편협 공통기준(builtin, 규칙 엔진 + AI 모두 지원)
- 사용자 기준: 파일 업로드 / URL / 직접 입력으로 등록한 학술지별 기준 (AI 모드에서 적용)
"""
import json
import re
import time
import uuid
from pathlib import Path

import httpx

from parsing import extract_text, ParseError

STYLES_DIR = Path(__file__).parent / "styles"
STYLES_DIR.mkdir(exist_ok=True)

BUILTIN_STYLE = {
    "id": "munpyeonhyeop",
    "name": "문편협 공통기준 (기본)",
    "builtin": True,
    "source": "인용 및 참고문헌의 기술요소와 형식에 관한 공통기준 (2024. 6. 17. 개정)",
    "description": "문헌정보학·기록학 분야 오픈액세스 학술지 편집인 회의 공통기준. APA 7판 기반. 규칙 엔진과 AI 모두 지원.",
}

MAX_STYLE_CHARS = 40000


def list_styles() -> list[dict]:
    styles = [BUILTIN_STYLE]
    for f in sorted(STYLES_DIR.glob("*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            styles.append({
                "id": data["id"], "name": data["name"], "builtin": False,
                "source": data.get("source", ""),
                "description": data.get("description", ""),
                "chars": len(data.get("text", "")),
            })
        except (json.JSONDecodeError, KeyError):
            continue
    return styles


def get_style(style_id: str) -> dict | None:
    if style_id == BUILTIN_STYLE["id"]:
        return dict(BUILTIN_STYLE)
    f = STYLES_DIR / f"{style_id}.json"
    if not f.exists():
        return None
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def delete_style(style_id: str) -> bool:
    f = STYLES_DIR / f"{style_id}.json"
    if f.exists():
        f.unlink()
        return True
    return False


def _save(name: str, text: str, source: str) -> dict:
    text = text.strip()
    if len(text) < 100:
        raise ValueError("기준 내용이 너무 짧습니다(100자 이상 필요). 기준 문서 전문 또는 핵심 규정을 제공해 주세요.")
    if len(text) > MAX_STYLE_CHARS:
        text = text[:MAX_STYLE_CHARS]
    style_id = "st_" + uuid.uuid4().hex[:10]
    data = {
        "id": style_id, "name": name.strip() or "이름 없는 기준",
        "builtin": False, "source": source, "text": text,
        "created": time.strftime("%Y-%m-%d %H:%M"),
    }
    (STYLES_DIR / f"{style_id}.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    return data


def add_style_from_file(name: str, filename: str, data: bytes) -> dict:
    try:
        text = extract_text(filename, data)
    except ParseError as ex:
        raise ValueError(str(ex))
    return _save(name, text, f"파일: {filename}")


def add_style_from_text(name: str, text: str) -> dict:
    return _save(name, text, "직접 입력")


def add_style_from_url(name: str, url: str) -> dict:
    if not re.match(r"https?://", url):
        raise ValueError("올바른 URL이 아닙니다(http:// 또는 https:// 필요).")
    try:
        with httpx.Client(follow_redirects=True, timeout=30,
                          headers={"User-Agent": "Mozilla/5.0 (RefStd-Agent)"}) as client:
            r = client.get(url)
            r.raise_for_status()
            ctype = r.headers.get("content-type", "")
            if "pdf" in ctype or url.lower().endswith(".pdf"):
                text = extract_text("style.pdf", r.content)
            elif "word" in ctype or url.lower().endswith(".docx"):
                text = extract_text("style.docx", r.content)
            else:
                text = _html_to_text(r.text)
    except httpx.HTTPError as ex:
        raise ValueError(f"URL을 가져올 수 없습니다: {ex}")
    except ParseError as ex:
        raise ValueError(str(ex))
    return _save(name, text, f"URL: {url}")


def _html_to_text(html: str) -> str:
    html = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.S | re.I)
    html = re.sub(r"<br\s*/?>|</p>|</div>|</li>|</h[1-6]>|</tr>", "\n", html, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", html)
    text = text.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"')
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()
