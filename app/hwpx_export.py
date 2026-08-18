# -*- coding: utf-8 -*-
"""처리 결과 → 참고문헌 목록 HWPX 생성.

내어쓰기가 적용된 최종 목록을 한글(HWP) 문서로 내려받게 한다 — 국내 학술지
투고가 아래아한글 중심이라, DOCX보다 이쪽이 원고에 바로 옮겨 붙는다.
안내문 생성기와 같은 기법: 내장 템플릿(hwpx_tpl.py)의 스타일 정의를 물려받고
section0.xml만 새로 쓴다. 빈 문서를 처음부터 만드는 것보다 한글에서 열릴
확률이 훨씬 높다(2026-08-14 실측).
"""
import base64
import html
import io
import re
import zipfile
from pathlib import Path

from hwpx_tpl import TPL_ZIP_B64

HWPUNIT_MM = 283.465
def _mm(v): return int(round(v * HWPUNIT_MM))

PAGE_W, PAGE_H = _mm(210), _mm(297)
MARGIN_LR, MARGIN_T, MARGIN_B, MARGIN_HF = _mm(20), _mm(18), _mm(15), _mm(10)

# hwpx_tpl.py(make_export_tpl.py)가 header.xml에 덧붙인 스타일 번호
T_TITLE, T_GROUP, T_BODY, T_SMALL = 46, 47, 48, 49
P_TITLE, P_GROUP, P_ITEM, P_META = 51, 52, 53, 54

_pid = [3100000000]


def _next_id():
    _pid[0] += 1
    return _pid[0]


def _esc(t: str) -> str:
    return html.escape(t or "", quote=False)


def _para(para_pr: int, char_pr: int, text: str, extra_run: str = "") -> str:
    run = f'<hp:run charPrIDRef="{char_pr}">{extra_run}<hp:t>{_esc(text)}</hp:t></hp:run>'
    return (f'<hp:p id="{_next_id()}" paraPrIDRef="{para_pr}" styleIDRef="0" '
            f'pageBreak="0" columnBreak="0" merged="0">{run}</hp:p>')


SEC_PR = (
    f'<hp:secPr id="" textDirection="HORIZONTAL" spaceColumns="1134" tabStop="8000" '
    f'tabStopVal="4000" tabStopUnit="HWPUNIT" outlineShapeIDRef="1" memoShapeIDRef="0" '
    f'textVerticalWidthHead="0" masterPageCnt="0">'
    f'<hp:grid lineGrid="0" charGrid="0" wonggojiFormat="0"/>'
    f'<hp:startNum pageStartsOn="BOTH" page="0" pic="0" tbl="0" equation="0"/>'
    f'<hp:visibility hideFirstHeader="0" hideFirstFooter="0" hideFirstMasterPage="0" '
    f'border="SHOW_ALL" fill="SHOW_ALL" hideFirstPageNum="0" hideFirstEmptyLine="0" showLineNumber="0"/>'
    f'<hp:lineNumberShape restartType="0" countBy="0" distance="0" startNumber="0"/>'
    f'<hp:pagePr landscape="WIDELY" width="{PAGE_W}" height="{PAGE_H}" gutterType="LEFT_ONLY">'
    f'<hp:margin header="{MARGIN_HF}" footer="{MARGIN_HF}" gutter="0" left="{MARGIN_LR}" '
    f'right="{MARGIN_LR}" top="{MARGIN_T}" bottom="{MARGIN_B}"/></hp:pagePr>'
    f'<hp:footNotePr><hp:autoNumFormat type="DIGIT" userChar="" prefixChar="" suffixChar=")" '
    f'supscript="0"/><hp:noteLine length="-1" type="SOLID" width="0.12 mm" color="#000000"/>'
    f'<hp:noteSpacing betweenNotes="850" belowLine="567" aboveLine="850"/>'
    f'<hp:numbering type="CONTINUOUS" newNum="1"/><hp:placement place="EACH_COLUMN" beneathText="0"/>'
    f'</hp:footNotePr>'
    f'<hp:endNotePr><hp:autoNumFormat type="DIGIT" userChar="" prefixChar="" suffixChar=")" '
    f'supscript="0"/><hp:noteLine length="14692344" type="SOLID" width="0.12 mm" color="#000000"/>'
    f'<hp:noteSpacing betweenNotes="0" belowLine="567" aboveLine="850"/>'
    f'<hp:numbering type="CONTINUOUS" newNum="1"/><hp:placement place="END_OF_DOCUMENT" beneathText="0"/>'
    f'</hp:endNotePr>'
    + "".join(
        f'<hp:pageBorderFill type="{t}" borderFillIDRef="1" textBorder="PAPER" headerInside="0" '
        f'footerInside="0" fillArea="PAPER"><hp:offset left="1417" right="1417" top="1417" bottom="1417"/>'
        f'</hp:pageBorderFill>' for t in ("BOTH", "EVEN", "ODD"))
    + '</hp:secPr>'
)

SEC_HEAD = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes" ?>'
    '<hs:sec xmlns:ha="http://www.hancom.co.kr/hwpml/2011/app" '
    'xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph" '
    'xmlns:hp10="http://www.hancom.co.kr/hwpml/2016/paragraph" '
    'xmlns:hs="http://www.hancom.co.kr/hwpml/2011/section" '
    'xmlns:hc="http://www.hancom.co.kr/hwpml/2011/core" '
    'xmlns:hh="http://www.hancom.co.kr/hwpml/2011/head" '
    'xmlns:hhs="http://www.hancom.co.kr/hwpml/2011/history" '
    'xmlns:hm="http://www.hancom.co.kr/hwpml/2011/master-page" '
    'xmlns:hpf="http://www.hancom.co.kr/schema/2011/hpf" '
    'xmlns:dc="http://purl.org/dc/elements/1.1/" '
    'xmlns:opf="http://www.idpf.org/2007/opf/" '
    'xmlns:ooxmlchart="http://www.hancom.co.kr/hwpml/2016/ooxmlchart" '
    'xmlns:hwpunitchar="http://www.hancom.co.kr/hwpml/2016/HwpUnitChar" '
    'xmlns:epub="http://www.idpf.org/2007/ops" '
    'xmlns:config="urn:oasis:names:tc:opendocument:xmlns:config:1.0">'
)


def _plain_text(section_xml: str) -> str:
    return re.sub(r"\n{3,}", "\n\n",
                  "\n".join(html.unescape(t)
                            for t in re.findall(r"<hp:t>(.*?)</hp:t>", section_xml, re.S)))


def build_result_hwpx(res: dict) -> bytes:
    """result dict → 참고문헌 목록 .hwpx 바이트."""
    stem = Path(res.get("filename") or "결과").stem
    title = f"{stem} — 참고문헌 목록"

    meta_bits = [b for b in (
        res.get("style_name", ""),
        f"검사일 {res.get('checked_at', '')}" if res.get("checked_at") else "",
        "refcheck.kr" + (f" {res.get('app_version')}" if res.get("app_version") else ""),
    ) if b]

    body: list[str] = []
    body.append(_para(P_TITLE, T_TITLE, title))
    body.append(_para(P_META, T_SMALL, " · ".join(meta_bits)))

    group = None
    for it in res.get("items") or []:
        if it.get("group") and it["group"] != group:
            group = it["group"]
            body.append(_para(P_GROUP, T_GROUP, f"[{group}]"))
        body.append(_para(P_ITEM, T_BODY, it.get("formatted", "")))

    eng = res.get("english_list") or []
    if eng:
        body.append(_para(P_GROUP, T_GROUP, "[국문 참고문헌 영문 변환 목록]"))
        for line in eng:
            body.append(_para(P_ITEM, T_BODY, line))

    body.append(_para(P_META, T_SMALL,
                      "이 목록은 refcheck.kr(참고문헌 검증 서비스)가 정리했습니다. "
                      "투고 전 변경 전·후 대비표로 최종 확인하세요."))

    # 첫 문단에 구역 설정과 단(段) 정의를 싣는다 — colPr이 빠지면 한글이
    # '파일을 열 수 없습니다'로 거부한다(안내문 생성기에서 2026-08-14 실측)
    ctrl = ('<hp:ctrl><hp:colPr id="" type="NEWSPAPER" layout="LEFT" colCount="1" '
            'sameSz="1" sameGap="0"/></hp:ctrl>')
    body[0] = body[0].replace(
        f'<hp:run charPrIDRef="{T_TITLE}"><hp:t>',
        f'<hp:run charPrIDRef="{T_TITLE}">{SEC_PR}{ctrl}</hp:run>'
        f'<hp:run charPrIDRef="{T_TITLE}"><hp:t>', 1)

    sec = SEC_HEAD + "".join(body) + "</hs:sec>"

    tpl = zipfile.ZipFile(io.BytesIO(base64.b64decode(TPL_ZIP_B64)))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        # mimetype은 압축하지 않고 맨 앞에 — OCF 규약
        z.writestr(zipfile.ZipInfo("mimetype"), "application/hwp+zip", zipfile.ZIP_STORED)
        for name in sorted(tpl.namelist()):
            if name == "mimetype":
                continue
            data = tpl.read(name)
            if name == "Contents/content.hpf":
                data = tpl.read(name).decode("utf-8").replace(
                    "<opf:title/>", f"<opf:title>{_esc(title)}</opf:title>", 1).encode("utf-8")
            if name == "Preview/PrvText.txt":
                data = _plain_text(sec).encode("utf-8")
            z.writestr(name, data)
        z.writestr("Contents/section0.xml", sec.encode("utf-8"))
    return out.getvalue()
