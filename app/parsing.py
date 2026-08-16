# -*- coding: utf-8 -*-
"""파일 파싱: HWP / HWPX / DOCX / PDF / TXT → 텍스트."""
import io
import re
import zipfile
import xml.etree.ElementTree as ET

SUPPORTED_EXTS = {".hwp", ".hwpx", ".docx", ".pdf", ".txt"}

OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"   # HWP 5.0 = OLE2 복합문서


class ParseError(Exception):
    pass


def extract_text(filename: str, data: bytes) -> str:
    ext = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext == ".txt":
        return _parse_txt(data)
    if ext == ".docx":
        return _parse_docx(data)
    if ext == ".hwpx":
        return _parse_hwpx(data)
    if ext == ".pdf":
        return _parse_pdf(data)
    if ext == ".hwp":
        return _parse_hwp(data)
    raise ParseError(f"지원하지 않는 파일 형식입니다: {ext} (지원: HWP, HWPX, DOCX, PDF, TXT)")


def _parse_txt(data: bytes) -> str:
    for enc in ("utf-8-sig", "utf-8", "cp949", "euc-kr", "utf-16"):
        try:
            return data.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode("utf-8", errors="replace")


def _parse_docx(data: bytes) -> str:
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise ParseError("DOCX 파일을 열 수 없습니다(손상되었거나 DOC 구형 형식일 수 있습니다).")
    try:
        xml_data = zf.read("word/document.xml")
    except KeyError:
        raise ParseError("DOCX 내부 구조를 읽을 수 없습니다.")
    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    root = ET.fromstring(xml_data)
    paragraphs = []
    for p in root.iter("{%s}p" % ns["w"]):
        texts = []
        for node in p.iter():
            tag = node.tag.split("}")[-1]
            if tag == "t" and node.text:
                texts.append(node.text)
            elif tag in ("br", "tab"):
                texts.append(" ")
        paragraphs.append("".join(texts))
    return "\n".join(paragraphs)


def _parse_hwp(data: bytes) -> str:
    """HWP 5.0 바이너리(OLE2 복합문서). hwpkit이 순수 파이썬으로 읽는다.

    문단 본문뿐 아니라 표 셀·각주 텍스트도 함께 나온다(2026-08-15 실측).
    """
    if data[:2] == b"PK":            # 확장자만 .hwp로 바꾼 HWPX
        return _parse_hwpx(data)
    try:
        from hwpkit import extract_text_from_hwp
    except ImportError:
        raise ParseError("hwpkit이 설치되어 있지 않습니다. (pip install hwpkit)")
    try:
        text = extract_text_from_hwp(io.BytesIO(data))
    except Exception:
        raise ParseError(_hwp_hint(data))
    if len(re.sub(r"\s", "", text)) < 30:
        # 배포용 문서는 예외 없이 빈 텍스트가 나오기도 한다
        raise ParseError(_hwp_hint(data, empty=True))
    return text


def _hwp_hint(data: bytes, empty: bool = False) -> str:
    """왜 못 읽었는지 짚어 준다. 이미 실패한 뒤에만 부르므로 추정이 틀려도 손해가 적다."""
    if data[:8] != OLE_MAGIC:
        if b"HWP Document File" in data[:64]:
            return ("한글 97 이전(HWP 3.x) 형식입니다. "
                    "한글에서 HWPX 또는 PDF로 저장한 뒤 올려 주세요.")
        return "HWP 파일이 아니거나 손상된 파일입니다."
    flags = _hwp_flags(data)
    if flags & 0x02:
        return "암호가 걸린 HWP 파일입니다. 암호를 푼 뒤 다시 올려 주세요."
    if flags & 0x04:
        return ("배포용으로 잠긴 HWP 파일입니다. "
                "한글에서 HWPX 또는 PDF로 저장한 뒤 올려 주세요.")
    if empty:
        return ("HWP 본문에서 글자를 찾지 못했습니다. "
                "빈 문서이거나 내용이 그림으로만 들어 있을 수 있습니다.")
    return "HWP 파일을 읽지 못했습니다. 한글에서 HWPX 또는 PDF로 저장한 뒤 다시 시도해 주세요."


def _hwp_flags(data: bytes) -> int:
    """FileHeader 속성 4바이트 — bit0 압축, bit1 암호, bit2 배포용."""
    try:
        import olefile
        ole = olefile.OleFileIO(io.BytesIO(data))
    except Exception:
        return 0
    try:
        head = ole.openstream("FileHeader").read(40)
    except Exception:
        return 0
    finally:
        ole.close()
    return int.from_bytes(head[36:40], "little") if len(head) >= 40 else 0


def _parse_hwpx(data: bytes) -> str:
    if data[:8] == OLE_MAGIC:        # 확장자만 .hwpx로 바꾼 구형 HWP
        return _parse_hwp(data)
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise ParseError("HWPX 파일을 열 수 없습니다. (파일이 손상되었을 수 있습니다)")
    section_names = sorted(
        n for n in zf.namelist()
        if re.match(r"Contents/section\d+\.xml$", n)
    )
    if not section_names:
        raise ParseError("HWPX 내부에서 본문(section)을 찾을 수 없습니다.")
    paragraphs = []
    for name in section_names:
        try:
            root = ET.fromstring(zf.read(name))
        except ET.ParseError:
            continue
        # 문단(<hp:p>) 단위로 텍스트(<hp:t>)를 모은다
        for p in root.iter():
            if p.tag.split("}")[-1] != "p":
                continue
            texts = []
            for node in p.iter():
                if node.tag.split("}")[-1] == "t":
                    if node.text:
                        texts.append(node.text)
                    for child in node:
                        if child.tail:
                            texts.append(child.tail)
            if texts or paragraphs:
                paragraphs.append("".join(texts))
    return "\n".join(paragraphs)


def _parse_pdf(data: bytes) -> str:
    try:
        from pypdf import PdfReader
    except ImportError:
        raise ParseError("pypdf가 설치되어 있지 않습니다. (pip install pypdf)")
    try:
        reader = PdfReader(io.BytesIO(data))
    except Exception:
        raise ParseError("PDF 파일을 열 수 없습니다.")
    pages = []
    for page in reader.pages:
        try:
            pages.append(page.extract_text() or "")
        except Exception:
            pages.append("")
    text = "\n".join(pages)
    if len(re.sub(r"\s", "", text)) < 30:
        raise ParseError("PDF에서 텍스트를 추출할 수 없습니다. 스캔본 PDF라면 OCR 처리 후 다시 시도해 주세요.")
    return text
