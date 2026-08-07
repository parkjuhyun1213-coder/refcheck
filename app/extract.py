# -*- coding: utf-8 -*-
"""참고문헌 구역 탐지 및 문헌 건별 분리 (규칙 기반)."""
import re

# 참고문헌 구역 시작 표제
_HEAD_RE = re.compile(
    r"^\s*(?:[<\[【]?\s*)?(?:\d+[\.\)]?\s*)?"
    r"(참\s*고\s*문\s*헌|인\s*용\s*문\s*헌|References?|REFERENCES?|BIBLIOGRAPHY|Bibliography|Works\s+Cited)"
    r"(?:\s*[>\]】]?)\s*$",
    re.MULTILINE,
)

# 구역 종료 표제(부록, 초록 등)
_END_RE = re.compile(
    r"^\s*(?:[<\[【]?\s*)?"
    r"(부\s*록|국\s*문\s*초\s*록|영\s*문\s*초\s*록|Abstract|ABSTRACT|Appendix|APPENDIX|감사의\s*글|저자\s*소개|필자\s*소개)"
    r"(?:\s*[>\]】]?)\s*",
    re.MULTILINE,
)


def find_reference_section(full_text: str) -> tuple[str, str]:
    """(본문 텍스트, 참고문헌 구역 텍스트)를 반환. 못 찾으면 참고문헌은 ''."""
    matches = list(_HEAD_RE.finditer(full_text))
    if not matches:
        loose = None
        for m in re.finditer(r"(참\s*고\s*문\s*헌|References)", full_text):
            loose = m
        if loose and loose.start() > len(full_text) * 0.3:
            body = full_text[: loose.start()]
            section = full_text[loose.end():]
        else:
            return full_text, ""
    else:
        m = matches[-1]  # 마지막 표제(목차 항목 배제)
        body = full_text[: m.start()]
        section = full_text[m.end():]

    end_m = _END_RE.search(section)
    if end_m and end_m.start() > 50:
        section = section[: end_m.start()]
    return body, section.strip()


_YEAR = r"(?:18|19|20)\d{2}"

# 명확한 계속줄(새 항목이 될 수 없는 줄)
_CONT_PATTERNS = [
    re.compile(r"^\s{4,}\S"),                                   # 깊은 들여쓰기
    re.compile(r"^(https?://|www\.|doi[:\.]|DOI[:\.]|10\.\d{4})"),
    re.compile(r"^(출처|Available|Retrieved|재인용)"),
    re.compile(r"^[a-z]"),                                       # 소문자 시작(영문 계속줄)
    re.compile(r"^[\d\-–—,\.\s]+$"),                             # 면수 조각
    re.compile(r"^[&,;::\)\]]"),
]


def _looks_like_continuation(line: str) -> bool:
    return any(p.match(line) for p in _CONT_PATTERNS)


def _looks_like_start(s: str) -> bool:
    """새 문헌 항목의 시작으로 보이는 줄인지 판정."""
    if re.match(r"^\[\d{1,3}\]\s*\S", s) or re.match(r"^\d{1,3}[\.\)]\s+\S", s):
        return True  # 번호 매김
    # 한글 저자/기관: 홍길동( / 홍길동, / 홍길동·김철수 / 국립중앙도서관 (2019) / 변회균. ...2014
    if re.match(r"^[가-힣]{2,15}\s*\(", s):
        return True
    if re.match(r"^[가-힣]{2,6}\s*[,·․]\s*[가-힣]{2,6}", s):
        return True
    if re.match(r"^[가-힣]{2,6}\s+외\b", s):
        return True
    if re.match(r"^[가-힣]{2,15}[\.,]", s) and re.search(_YEAR, s):
        return True
    if re.match(r"^[가-힣]{2,6}\s+[\"“『「]", s):
        return True
    # 서양 저자: Smith, J. / Smith & Jones / Smith et al. / Smith(2020)
    if re.match(r"^[A-Z][A-Za-z\-']+,\s", s):
        return True
    if re.match(r"^[A-Z][A-Za-z\-']+\s+(?:&|and|et al)", s):
        return True
    if re.match(r"^[A-Z][A-Za-z\-' ]{1,40}\(\s*" + _YEAR, s):
        return True
    # 한자·일문
    if re.match(r"^[一-鿿぀-ゟ゠-ヿ]{2,}", s):
        return True
    # 무저자: 서명. (연도)
    if re.match(r"^[A-Za-z가-힣“\"'].{3,80}[\.。]\s*\(" + _YEAR, s):
        return True
    return False


def split_entries(section_text: str) -> list[str]:
    """참고문헌 구역 텍스트를 문헌 건별 문자열 리스트로 분리."""
    lines = section_text.splitlines()
    entries: list[str] = []
    cur: list[str] = []

    for raw_line in lines:
        s = raw_line.strip()
        if not s:
            continue
        if cur and _looks_like_continuation(raw_line):
            cur.append(s)
        elif _looks_like_start(s):
            if cur:
                entries.append(" ".join(cur))
            cur = [s]
        else:
            if cur:
                cur.append(s)
            else:
                cur = [s]
    if cur:
        entries.append(" ".join(cur))

    cleaned = []
    for e in entries:
        e = re.sub(r"^\s*(\[\d{1,3}\]|\d{1,3}[\.\)])\s*", "", e)  # 번호 제거
        e = re.sub(r"\s{2,}", " ", e).strip()
        if len(e) >= 15:
            cleaned.append(e)
    return cleaned
