# -*- coding: utf-8 -*-
"""에이전트 결과 ↔ 학회지 발행본 참고문헌 비교.

처리 이력(history.py)에 저장된 에이전트의 최종 참고문헌과, 관리자가 업로드한
학회지 발행본(최종 게재본)의 참고문헌 목록을 항목 단위로 짝지어 차이를 찾는다.
관리자가 차이를 검토·채택하면 '박주현 교수의 추가 제안'(suggestions.py의 prof)으로
축적되어 이후 처리 결과에 제안으로 표시된다.
"""
import difflib
import re

import extract
import parsing

_MATCH_THRESHOLD = 0.45  # 이 미만이면 같은 문헌으로 보지 않음


def _norm(s: str) -> str:
    return re.sub(r"[^0-9a-z가-힣一-鿿]", "", (s or "").lower())


def _collapse_ws(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip()


def extract_published_refs(filename: str, data: bytes, split_ai=None) -> list[str]:
    """발행본 파일에서 참고문헌 목록을 추출. split_ai가 주어지면 AI 분리 우선.

    실패 시 ValueError(사용자 안내 메시지)를 던진다.
    """
    text = parsing.extract_text(filename, data)  # ParseError는 호출측에서 처리
    body, section = extract.find_reference_section(text)
    if not section:
        probe = extract.split_entries(text)
        if len(probe) >= 3 and sum(1 for p in probe if re.search(r"\(?\d{4}", p)) >= len(probe) * 0.6:
            section = text
        else:
            raise ValueError("발행본에서 참고문헌 구역을 찾을 수 없습니다. "
                             "'참고문헌' 또는 'References' 표제가 있는지 확인해 주세요.")
    raws: list[str] = []
    if split_ai:
        try:
            raws = split_ai(section)
        except Exception:
            raws = []
    if not raws:
        raws = extract.split_entries(section)
    if not raws:
        raise ValueError("발행본 참고문헌 구역에서 문헌을 추출하지 못했습니다.")
    return raws


def _diff_segments(a: str, b: str) -> list[list[str]]:
    """문자 단위 차이 구간: [[tag, a부분, b부분], ...] (tag: equal|replace|delete|insert)."""
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    out = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        out.append([tag, a[i1:i2], b[j1:j2]])
    return out


def align(agent_items: list[dict], published: list[str]) -> dict:
    """에이전트 결과 항목과 발행본 문헌 문자열을 짝지어 비교.

    agent_items: history 레코드의 items ({raw, formatted, type, entry{title, doi}, ...})
    반환: {pairs, agent_only, published_only, n_same, n_diff}
    """
    pub_norm = [_norm(p) for p in published]
    used: set[int] = set()
    pairs: list[dict] = []
    agent_only: list[dict] = []

    for i, it in enumerate(agent_items):
        formatted = _collapse_ws(it.get("formatted", ""))
        a_norm = _norm(formatted)
        entry = it.get("entry") or {}
        title_norm = _norm(entry.get("title", ""))
        doi = (entry.get("doi") or "").lower().strip()

        best_j, best = -1, 0.0
        for j, p in enumerate(published):
            if j in used:
                continue
            score = difflib.SequenceMatcher(None, a_norm, pub_norm[j], autojunk=False).ratio()
            if doi and doi in p.lower():
                score = max(score, 0.99)
            if title_norm and len(title_norm) >= 8 and title_norm in pub_norm[j]:
                score = max(score, 0.90)
            if score > best:
                best, best_j = score, j

        if best >= _MATCH_THRESHOLD and best_j >= 0:
            used.add(best_j)
            pub_str = _collapse_ws(published[best_j])
            same = _collapse_ws(formatted) == pub_str
            pairs.append({
                "pair_id": len(pairs),
                "index": i,
                "type": it.get("type", ""),
                "raw": _collapse_ws(it.get("raw", "")),  # 편집(투고) 단계 원문
                "agent": formatted,
                "published": pub_str,
                "same": same,
                "sim": round(best, 3),
                "diff": [] if same else _diff_segments(formatted, pub_str),
            })
        else:
            agent_only.append({"index": i, "agent": formatted,
                               "raw": _collapse_ws(it.get("raw", "")),
                               "type": it.get("type", "")})

    published_only = [_collapse_ws(p) for j, p in enumerate(published) if j not in used]
    n_same = sum(1 for p in pairs if p["same"])
    return {
        "pairs": pairs,
        "agent_only": agent_only,
        "published_only": published_only,
        "n_pairs": len(pairs),
        "n_same": n_same,
        "n_diff": len(pairs) - n_same,
    }


def fallback_draft(pair: dict) -> dict:
    """AI 미사용 시 채택용 제안 문구 초안(관리자가 편집해 확정)."""
    return {
        "topic": "발행본 표기 차이",
        "rule": f"발행본 표기를 따른다: {pair.get('published', '')[:200]}",
        "example": pair.get("published", "")[:300],
    }
