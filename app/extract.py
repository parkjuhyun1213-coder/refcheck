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


# '국한문 참고문헌의 영문 표기' 류 소절 표제 — 이 표제 아래 항목들은 국문(·한문)
# 문헌의 영문 변환 표기다(문편협 기준 9·10항의 병기 목록).
# 영문 표제는 괄호·꺾쇠로 감싼 줄만 인정한다: "Romanization of Korean bibliographic
# references." 같은 문헌 제목의 줄바꿈 조각과 겉모습이 같아, 괄호 없는 영문 줄까지
# 받으면 목록 한가운데를 표제로 오인한다.
_EN_CONV_HEAD_RE = re.compile(
    r"^[ \t]*(?:"
    #   국문 표제(장식 선택적): 국한문/국문/한글 (참고)문헌(의) 영문/영어 표기·변환·번역·목록
    r"[•·▪▶◆■○●※◦∙*#=\-–—<\[【(（]*[ \t]*"
    r"(?:"
    r"(?:국\s*[·ㆍ]?\s*한\s*문|한\s*문|국\s*문|한\s*글|한\s*국\s*어)[의\s]*(?:참\s*고\s*)?(?:문\s*헌)?[의\s]*"
    r"(?:영\s*문|영\s*어)\s*(?:(?:표\s*기|변\s*환|번\s*역|화)(?:\s*목\s*록)?|목\s*록)"
    r"|(?:참\s*고\s*문\s*헌[의\s]*)?(?:영\s*문|영\s*어)\s*(?:표\s*기|변\s*환|번\s*역|화)(?:\s*목\s*록)?"
    r")"
    #   영문 표제(괄호·꺾쇠 필수): (English translation / Romanization of references ... Korean)
    r"|[•·▪▶◆■○●※◦∙*#=\-–—]*[ \t]*[<\[【(（]\s*"
    r"(?:English\s+translations?|Romanizations?|Transliterations?|Translated)"
    r"(?=[^\n]{0,160}\bKorean\b)"
    r"[^()（）\n]{0,160}"
    r")"
    # 꼬리: 닫는 장식과 부기 괄호만 허용 — 표제 뒤에 다른 문장이 이어지는 줄
    # (제목이 '영문 표기'로 시작하는 문헌의 줄바꿈 조각 등)을 표제로 오인하지 않게.
    # 공백은 [ \t]만: \s는 줄바꿈을 넘어 다음 줄 영문 부기까지 한 표제로 삼킨다
    r"[ \t>\]】)）]*(?:[(（][^()（）\n]{0,160}[)）]?)?[ \t>\]】)）:：.。·˙\-–—]*$",
    re.MULTILINE | re.IGNORECASE,
)


def _is_conv_heading_line(line: str) -> bool:
    """영문 변환 소절 표제 줄인지. 연도가 있으면 표제가 아니라 문헌 항목이다."""
    return bool(_EN_CONV_HEAD_RE.match(line)) and not re.search(_YEAR, line)


def find_en_conversion_split(section_text: str) -> tuple[str, str, str]:
    """참고문헌 구역을 (일반 목록, 영문 변환 목록, 감지한 표제)로 나눈다.

    원고가 '국한문 참고문헌의 영문 표기'·'영문 변환 목록' 같은 소절 표제를 명시한
    경우, 그 아래 항목들은 국문 문헌의 영문 변환 표기다. 표제 기반 인식이 서지 요소
    휴리스틱(crosscheck.is_conversion_pair)보다 정확하므로 여기서 명시적으로 나눈다.
    표제가 없으면 (원문 그대로, "", "")를 반환한다.
    """
    for m in _EN_CONV_HEAD_RE.finditer(section_text):
        line = m.group(0)
        if re.search(_YEAR, line):
            continue  # 연도가 있으면 표제가 아니라 문헌 항목이다
        main = section_text[: m.start()].rstrip()
        rest_lines = section_text[m.end():].splitlines()
        # 표제 직후의 연속 표제 줄(국문 표제 다음 줄의 영문 부기 등)도 걷어낸다
        while rest_lines:
            s = rest_lines[0].strip()
            if not s:
                rest_lines.pop(0)
                continue
            if _is_conv_heading_line(rest_lines[0]):
                rest_lines.pop(0)
                continue
            break
        conv = "\n".join(rest_lines).strip()
        if not conv:
            continue  # 표제 뒤에 내용이 없으면 분할하지 않는다(표제 줄은 split_entries가 거른다)
        head = re.sub(r"\s+", " ", line).strip(" \t•·▪▶◆■○●※◦∙*#=–—<>[]【】()（）:：.。-")
        return main, conv, head or "영문 변환 목록"
    return section_text, "", ""


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
        if _is_conv_heading_line(raw_line):
            continue  # 영문 변환 소절 표제 줄 — 문헌이 아니다(직전 항목에 붙지 않게)
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
