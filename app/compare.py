# -*- coding: utf-8 -*-
"""에이전트 결과 ↔ 학회지 발행본 참고문헌 비교.

처리 이력(history.py)에 저장된 에이전트의 최종 참고문헌과, 관리자가 업로드한
학회지 발행본(최종 게재본)의 참고문헌 목록을 항목 단위로 짝지어 차이를 찾는다.
관리자가 차이를 검토·채택하면 '박주현 교수의 추가 제안'(suggestions.py의 prof)으로
축적되어 이후 처리 결과에 제안으로 표시된다.
"""
import difflib
import json
import re
import threading
import time
from pathlib import Path

import extract
import parsing

_MATCH_THRESHOLD = 0.45  # 이 미만이면 같은 문헌으로 보지 않음

# 3단 비교 원자료의 영구 보관소 — 절대 지우거나 덮어쓰지 않는다.
# 처리 이력(history)은 300건을 넘으면 오래된 것부터 정리되고 같은 원고를 다시 비교하면
# 덮어써지지만, '편집부가 실제로 무엇을 고쳤는가'는 소급 생성이 불가능한 자료다.
CORPUS_PATH = Path(__file__).parent / "compare_corpus.jsonl"
_CORPUS_LOCK = threading.Lock()


def append_corpus(rec: dict, published_filename: str, cmp_result: dict) -> int:
    """비교 결과를 한 건씩 JSONL로 덧붙이고 기록한 줄 수를 반환.

    한 줄 = 참고문헌 한 건. 나중에 학회별·연도별로 집계하거나 실측 정확도를
    산출할 수 있도록 부모 정보(학회·원고·발행본)를 각 줄에 함께 담는다.
    """
    stamp = time.strftime("%Y-%m-%d %H:%M")
    base = {
        "t": stamp,
        "hid": rec.get("id", ""),
        "org": rec.get("org", ""),
        "user": rec.get("user", ""),
        "style": rec.get("style_name", ""),
        "src": rec.get("filename", ""),
        "pub": published_filename,
    }
    lines = []
    for p in cmp_result.get("pairs", []):
        lines.append({**base, "kind": "pair", "same": bool(p.get("same")),
                      "raw": p.get("raw", ""), "agent": p.get("agent", ""),
                      "published": p.get("published", "")})
    for a in cmp_result.get("agent_only", []):
        lines.append({**base, "kind": "agent_only", "same": False,
                      "raw": a.get("raw", ""), "agent": a.get("agent", ""), "published": ""})
    for pub in cmp_result.get("published_only", []):
        lines.append({**base, "kind": "published_only", "same": False,
                      "raw": "", "agent": "", "published": pub})
    if not lines:
        return 0
    try:
        with _CORPUS_LOCK:
            with CORPUS_PATH.open("a", encoding="utf-8") as f:
                for row in lines:
                    f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError:
        return 0  # 보관 실패가 비교 자체를 막지는 않는다
    return len(lines)


def corpus_stats() -> dict:
    """누적 코퍼스 요약 — 기록 건수, 비교한 논문 수, 일치율."""
    total = same = 0
    papers = set()
    orgs: dict[str, int] = {}
    if not CORPUS_PATH.exists():
        return {"rows": 0, "papers": 0, "same": 0, "match_rate": None, "orgs": {}}
    try:
        with CORPUS_PATH.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                total += 1
                if row.get("kind") == "pair" and row.get("same"):
                    same += 1
                if row.get("hid"):
                    papers.add((row.get("hid"), row.get("pub", "")))
                org = row.get("org") or "미지정"
                orgs[org] = orgs.get(org, 0) + 1
    except OSError:
        return {"rows": 0, "papers": 0, "same": 0, "match_rate": None, "orgs": {}}
    return {"rows": total, "papers": len(papers), "same": same,
            "match_rate": round(same / total, 4) if total else None, "orgs": orgs}


def _norm(s: str) -> str:
    return re.sub(r"[^0-9a-z가-힣一-鿿]", "", (s or "").lower())


def _collapse_ws(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip()


# 저자 표기부 = 맨 앞부터 '(연도).'까지. 문편협 형식은 국문·영문 모두 이 꼴이다.
_AUTHOR_HEAD = re.compile(r"^.*?\((?:\d{4}[a-z]?|n\.d\.|발행년불명)[^)]*\)\.\s*")


def _drop_authors(s: str) -> str:
    """저자 표기부를 떼어낸 나머지.

    KCI 참고문헌 레코드는 제1저자만 담고 있어(4인 공저도 한 명), 저자까지 대조하면
    모든 항목이 차이로 잡힌다. 저자를 뺀 나머지로 비교할 때 쓴다.
    """
    return _AUTHOR_HEAD.sub("", s or "", count=1).strip() or (s or "").strip()


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


def align(agent_items: list[dict], published: list[str],
          ignore_authors: bool = False) -> dict:
    """에이전트 결과 항목과 발행본 문헌 문자열을 짝지어 비교.

    agent_items: history 레코드의 items ({raw, formatted, type, entry{title, doi}, ...})
    ignore_authors: 저자 표기부를 뺀 나머지로만 일치를 판정한다. 발행본을 KCI에서
        가져온 경우, KCI 레코드에 제1저자만 담겨 있어 저자를 대조하면 모든 항목이
        차이로 잡히기 때문이다(화면에는 양쪽 원문을 그대로 보여 준다).
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
            if ignore_authors:
                cmp_a, cmp_b = _drop_authors(formatted), _drop_authors(pub_str)
            else:
                cmp_a, cmp_b = _collapse_ws(formatted), pub_str
            same = cmp_a == cmp_b
            pairs.append({
                "pair_id": len(pairs),
                "index": i,
                "type": it.get("type", ""),
                "raw": _collapse_ws(it.get("raw", "")),  # 편집(투고) 단계 원문
                "agent": formatted,
                "published": pub_str,
                "same": same,
                "sim": round(best, 3),
                "diff": [] if same else _diff_segments(cmp_a, cmp_b),
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
