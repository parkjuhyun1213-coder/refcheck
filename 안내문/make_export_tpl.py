# -*- coding: utf-8 -*-
"""app/hwpx_tpl.py 생성기 — 결과 HWPX 내보내기용 내장 템플릿.

서버에는 규정/ 폴더가 올라가지 않으므로(배포는 app/*.py만), 학회 원고형식
HWPX에서 필요한 파일들을 뽑아 파이썬 모듈에 base64로 심어 둔다.
안내문 생성기(make_hwpx.py)와 같은 기법: header.xml에 글자·문단모양을
덧붙여 두고, section0.xml만 실행 시점에 새로 쓴다.

규정 템플릿이 바뀌면 이 스크립트를 다시 실행해 app/hwpx_tpl.py를 갱신할 것.
"""
import base64
import io
import re
import zipfile
from pathlib import Path

TPL = Path(r"f:\UseAI\writing_reference_agent\규정\한국도서관정보학회 규정\도서관정보학회지_원고형식.hwpx")
OUT = Path(__file__).resolve().parent.parent / "app" / "hwpx_tpl.py"

HWPUNIT_MM = 283.465
def mm(v): return int(round(v * HWPUNIT_MM))

NAVY_DEEP = "#16385A"
INK = "#17232F"
GREY = "#7C8B9B"

# 결과 문서용 글자모양 (id, 크기pt, 굵게, 색, 글꼴 1=맑은 고딕 4=함초롬바탕)
CHAR_PRS = [
    (46, 13.0, True,  NAVY_DEEP, 4),   # 문서 제목
    (47, 10.5, True,  NAVY_DEEP, 1),   # 그룹 표제([국내문헌] 등)
    (48,  9.5, False, INK,       1),   # 참고문헌 항목
    (49,  8.5, False, GREY,      1),   # 메타·꼬리말
]
CHAR_TPL = (
    '<hh:charPr id="{id}" height="{h}" textColor="{color}" shadeColor="none" '
    'useFontSpace="0" useKerning="0" symMark="NONE" borderFillIDRef="2">'
    '<hh:fontRef hangul="{f}" latin="{f}" hanja="{f}" japanese="{f}" other="{f}" symbol="{f}" user="{f}"/>'
    '<hh:ratio hangul="100" latin="100" hanja="100" japanese="100" other="100" symbol="100" user="100"/>'
    '<hh:spacing hangul="0" latin="0" hanja="0" japanese="0" other="0" symbol="0" user="0"/>'
    '<hh:relSz hangul="100" latin="100" hanja="100" japanese="100" other="100" symbol="100" user="100"/>'
    '<hh:offset hangul="0" latin="0" hanja="0" japanese="0" other="0" symbol="0" user="0"/>'
    '{bold}'
    '<hh:underline type="NONE" shape="SOLID" color="#000000"/>'
    '<hh:strikeout shape="NONE" color="#000000"/>'
    '<hh:outline type="NONE"/>'
    '<hh:shadow type="NONE" color="#C0C0C0" offsetX="10" offsetY="10"/>'
    '</hh:charPr>'
)

# 결과 문서용 문단모양 (id, 정렬, 왼쪽들여쓰기, 첫줄내어쓰기(intent, 음수), 위, 아래, 줄간격%)
# 53번이 핵심: 참고문헌 항목의 내어쓰기(왼쪽 7mm, 첫 줄 -7mm)
PARA_PRS = [
    (51, "CENTER",  0,      0,        0,       mm(2.0), 135),  # 제목
    (52, "LEFT",    0,      0,        mm(3.0), mm(1.0), 135),  # 그룹 표제
    (53, "JUSTIFY", mm(7),  -mm(7),   0,       mm(1.0), 150),  # 항목(내어쓰기)
    (54, "LEFT",    0,      0,        0,       mm(1.2), 135),  # 메타·꼬리말
]
PARA_TPL = (
    '<hh:paraPr id="{id}" tabPrIDRef="0" condense="0" fontLineHeight="0" snapToGrid="1" '
    'suppressLineNumbers="0" checked="0">'
    '<hh:align horizontal="{align}" vertical="BASELINE"/>'
    '<hh:heading type="NONE" idRef="0" level="0"/>'
    '<hh:breakSetting breakLatinWord="KEEP_WORD" breakNonLatinWord="BREAK_WORD" widowOrphan="0" '
    'keepWithNext="0" keepLines="0" pageBreakBefore="0" lineWrap="BREAK"/>'
    '<hh:autoSpacing eAsianEng="0" eAsianNum="0"/>'
    '<hp:switch>'
    '<hp:case hp:required-namespace="http://www.hancom.co.kr/hwpml/2016/HwpUnitChar">{body}</hp:case>'
    '<hp:default>{body}</hp:default>'
    '</hp:switch>'
    '<hh:border borderFillIDRef="2" offsetLeft="0" offsetRight="0" offsetTop="0" offsetBottom="0" '
    'connect="0" ignoreMargin="0"/>'
    '</hh:paraPr>'
)
PARA_BODY = (
    '<hh:margin><hc:intent value="{intent}" unit="HWPUNIT"/><hc:left value="{left}" unit="HWPUNIT"/>'
    '<hc:right value="0" unit="HWPUNIT"/><hc:prev value="{prev}" unit="HWPUNIT"/>'
    '<hc:next value="{next}" unit="HWPUNIT"/></hh:margin>'
    '<hh:lineSpacing type="PERCENT" value="{ls}" unit="HWPUNIT"/>'
)


def build_header(src: str) -> str:
    chars = "".join(
        CHAR_TPL.format(id=i, h=int(pt * 100), color=col, f=f,
                        bold="<hh:bold/>" if b else "")
        for i, pt, b, col, f in CHAR_PRS)
    paras = "".join(
        PARA_TPL.format(id=i, align=al,
                        body=PARA_BODY.format(intent=intent, left=left, prev=pv, next=nx, ls=ls))
        for i, al, left, intent, pv, nx, ls in PARA_PRS)
    out = src
    out = out.replace('</hh:charProperties>', chars + '</hh:charProperties>', 1)
    out = out.replace('</hh:paraProperties>', paras + '</hh:paraProperties>', 1)
    # 개수 갱신 — 어긋나면 한글이 파일을 거부한다
    out = re.sub(r'(<hh:charProperties itemCnt=")\d+(")',
                 lambda m: m.group(1) + str(46 + len(CHAR_PRS)) + m.group(2), out, count=1)
    out = re.sub(r'(<hh:paraProperties itemCnt=")\d+(")',
                 lambda m: m.group(1) + str(51 + len(PARA_PRS)) + m.group(2), out, count=1)
    return out


def main():
    files: dict[str, bytes] = {}
    with zipfile.ZipFile(TPL) as z:
        for info in z.infolist():
            name = info.filename
            # 그림은 쓰지 않고, section0.xml은 실행 시점에 새로 쓴다
            if name in ("BinData/image1.png", "Contents/section0.xml"):
                continue
            files[name] = z.read(name)

    hpf = files["Contents/content.hpf"].decode("utf-8")
    hpf = re.sub(r'<opf:item id="image1"[^>]*/>', "", hpf)
    files["Contents/content.hpf"] = hpf.encode("utf-8")

    hdr = files["Contents/header.xml"].decode("utf-8")
    files["Contents/header.xml"] = build_header(hdr).encode("utf-8")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name in sorted(files):
            z.writestr(name, files[name])
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")

    lines = [b64[i:i + 100] for i in range(0, len(b64), 100)]
    body = "\n".join(f'    "{ln}"' for ln in lines)
    OUT.write_text(
        '# -*- coding: utf-8 -*-\n'
        '"""HWPX 내보내기용 내장 템플릿 — 안내문/make_export_tpl.py가 생성(직접 수정 금지).\n\n'
        '학회 원고형식 HWPX의 글꼴·스타일 정의(header.xml)에 결과 문서용 글자·문단모양\n'
        '(46~49번 글자, 51~54번 문단 — 53번이 참고문헌 내어쓰기)을 덧붙여 zip째 담았다.\n'
        'section0.xml은 없다 — hwpx_export.py가 실행 시점에 새로 써 넣는다.\n"""\n\n'
        'TPL_ZIP_B64 = (\n' + body + '\n)\n',
        encoding="utf-8")
    print(f"생성: {OUT.name} ({OUT.stat().st_size:,} bytes, zip {len(buf.getvalue()):,} bytes)")


if __name__ == "__main__":
    main()
