# -*- coding: utf-8 -*-
"""파일 파싱: HWPX / DOCX / PDF / TXT → 텍스트."""
import io
import re
import zipfile
import xml.etree.ElementTree as ET

SUPPORTED_EXTS = {".hwpx", ".docx", ".pdf", ".txt"}


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
        raise ParseError("HWP 구형 파일은 지원하지 않습니다. 한글에서 HWPX 또는 PDF로 저장한 뒤 다시 시도해 주세요.")
    raise ParseError(f"지원하지 않는 파일 형식입니다: {ext} (지원: HWPX, DOCX, PDF, TXT)")


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


def _parse_hwpx(data: bytes) -> str:
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise ParseError("HWPX 파일을 열 수 없습니다. (HWP 구형 파일이라면 HWPX로 다시 저장해 주세요)")
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
