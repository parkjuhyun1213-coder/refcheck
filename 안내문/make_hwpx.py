# -*- coding: utf-8 -*-
"""refcheck 도입 안내문 HWPX 생성.

학회 원고형식(도서관정보학회지_원고형식.hwpx)을 구조 템플릿으로 삼는다.
header.xml의 글꼴·테두리 정의를 그대로 물려받고, 필요한 글자모양·문단모양·테두리를
뒤에 덧붙인 뒤 section0.xml만 새로 쓴다. 빈 문서를 처음부터 만드는 것보다
한글에서 열릴 확률이 훨씬 높다.
"""
import html
import re
import shutil
import zipfile
from pathlib import Path

TPL = Path(r"f:\UseAI\writing_reference_agent\규정\한국도서관정보학회 규정\도서관정보학회지_원고형식.hwpx")
WORK = Path(__file__).parent / "hwpx_build"
OUT = Path(__file__).parent / "refcheck_도입안내.hwpx"

HWPUNIT_MM = 283.465
def mm(v): return int(round(v * HWPUNIT_MM))

# ── 지면: A4, 좌우 20mm
PAGE_W, PAGE_H = mm(210), mm(297)
MARGIN_LR, MARGIN_T, MARGIN_B, MARGIN_HF = mm(20), mm(18), mm(15), mm(10)
TEXT_W = PAGE_W - MARGIN_LR * 2

NAVY = "#1F4E79"
NAVY_DEEP = "#16385A"
INK = "#17232F"
INK2 = "#48596A"
GREY = "#7C8B9B"
AMBER = "#7D5400"

# ── 덧붙일 글자모양 (id, 크기pt, 굵게, 색, 한글글꼴id)  글꼴: 1=맑은 고딕, 4=함초롬바탕
CHAR_PRS = [
    (46, 16.0, True,  NAVY_DEEP, 4),   # 대제목
    (47, 11.0, True,  NAVY_DEEP, 4),   # 절 제목
    (48,  9.5, False, INK,       1),   # 본문
    (49,  8.5, False, INK2,      1),   # 작은 본문
    (50,  9.5, True,  NAVY_DEEP, 1),   # 본문 강조
    (51,  8.7, True,  NAVY_DEEP, 1),   # 표 머리
    (52,  8.7, False, INK,       1),   # 표 본문
    (53,  9.8, False, INK2,      1),   # 리드문
    (54,  8.5, False, GREY,      1),   # 꼬리말·주석
    (55,  9.5, True,  AMBER,     1),   # 비용 강조
    (56,  8.7, True,  INK,       1),   # 표 첫 열
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

# ── 덧붙일 문단모양 (id, 정렬, 위여백, 아래여백, 줄간격%)
PARA_PRS = [
    (51, "CENTER",  0,        mm(1.4), 135),  # 대제목
    (52, "LEFT",    mm(3.2),  mm(1.2), 135),  # 절 제목
    (53, "JUSTIFY", 0,        mm(1.0), 142),  # 본문
    (54, "JUSTIFY", 0,        mm(1.8), 142),  # 본문(문단 끝)
    (55, "CENTER",  0,        0,       125),  # 표 셀 가운데
    (56, "LEFT",    0,        0,       125),  # 표 셀 왼쪽
    (57, "LEFT",    mm(0.5),  mm(0.5), 138),  # 목록 항목
    (58, "CENTER",  mm(2),    0,       130),  # 꼬리말
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
    '<hh:margin><hc:intent value="0" unit="HWPUNIT"/><hc:left value="0" unit="HWPUNIT"/>'
    '<hc:right value="0" unit="HWPUNIT"/><hc:prev value="{prev}" unit="HWPUNIT"/>'
    '<hc:next value="{next}" unit="HWPUNIT"/></hh:margin>'
    '<hh:lineSpacing type="PERCENT" value="{ls}" unit="HWPUNIT"/>'
)

# ── 덧붙일 테두리: 25=머리행(연한 남색 채움), 26=본문칸
BORDER_TPL = (
    '<hh:borderFill id="{id}" threeD="0" shadow="0" centerLine="NONE" breakCellSeparateLine="0">'
    '<hh:slash type="NONE" Crooked="0" isCounter="0"/>'
    '<hh:backSlash type="NONE" Crooked="0" isCounter="0"/>'
    '<hh:leftBorder type="SOLID" width="0.12 mm" color="#B6C6D6"/>'
    '<hh:rightBorder type="SOLID" width="0.12 mm" color="#B6C6D6"/>'
    '<hh:topBorder type="SOLID" width="{tw}" color="{tc}"/>'
    '<hh:bottomBorder type="SOLID" width="{bw}" color="{bc}"/>'
    '<hh:diagonal type="SOLID" width="0.1 mm" color="#000000"/>'
    '{fill}'
    '</hh:borderFill>'
)
FILL = ('<hc:fillBrush><hc:winBrush faceColor="{c}" hatchColor="#333333" alpha="0"/></hc:fillBrush>')


def build_header(src: str) -> str:
    chars = "".join(
        CHAR_TPL.format(id=i, h=int(pt * 100), color=col, f=f,
                        bold="<hh:bold/>" if b else "")
        for i, pt, b, col, f in CHAR_PRS)
    paras = "".join(
        PARA_TPL.format(id=i, align=al,
                        body=PARA_BODY.format(prev=pv, next=nx, ls=ls))
        for i, al, pv, nx, ls in PARA_PRS)
    borders = (
        BORDER_TPL.format(id=25, tw="0.4 mm", tc=NAVY, bw="0.4 mm", bc=NAVY,
                          fill=FILL.format(c="#E8F0F8")) +
        BORDER_TPL.format(id=26, tw="0.12 mm", tc="#B6C6D6", bw="0.12 mm", bc="#B6C6D6", fill="")
    )
    out = src
    out = out.replace('</hh:borderFills>', borders + '</hh:borderFills>', 1)
    out = out.replace('</hh:charProperties>', chars + '</hh:charProperties>', 1)
    out = out.replace('</hh:paraProperties>', paras + '</hh:paraProperties>', 1)
    # 개수 갱신 — 어긋나면 한글이 파일을 거부한다
    out = re.sub(r'(<hh:borderFills itemCnt=")\d+(")', lambda m: m.group(1) + "26" + m.group(2), out, 1)
    out = re.sub(r'(<hh:charProperties itemCnt=")\d+(")',
                 lambda m: m.group(1) + str(46 + len(CHAR_PRS)) + m.group(2), out, 1)
    out = re.sub(r'(<hh:paraProperties itemCnt=")\d+(")',
                 lambda m: m.group(1) + str(51 + len(PARA_PRS)) + m.group(2), out, 1)
    return out


# ================================================================ 본문 조립
_pid = [3000000000]


def _next_id():
    _pid[0] += 1
    return _pid[0]


def esc(t): return html.escape(t, quote=False)


def runs(parts):
    """parts: [(charPrId, 텍스트), …] → run XML"""
    return "".join(f'<hp:run charPrIDRef="{c}"><hp:t>{esc(t)}</hp:t></hp:run>' for c, t in parts if t)


def para(para_pr, parts, page_break=False):
    return (f'<hp:p id="{_next_id()}" paraPrIDRef="{para_pr}" styleIDRef="0" '
            f'pageBreak="{1 if page_break else 0}" columnBreak="0" merged="0">'
            f'{runs(parts)}</hp:p>')


def cell(text_parts, col, row, w, h, border, para_pr, colspan=1, rowspan=1):
    inner = (f'<hp:p id="{_next_id()}" paraPrIDRef="{para_pr}" styleIDRef="0" '
             f'pageBreak="0" columnBreak="0" merged="0">{runs(text_parts)}</hp:p>')
    return (f'<hp:tc name="" header="{1 if row == 0 else 0}" hasMargin="0" protect="0" '
            f'editable="0" dirty="0" borderFillIDRef="{border}">'
            f'<hp:subList id="" textDirection="HORIZONTAL" lineWrap="BREAK" vertAlign="CENTER" '
            f'linkListIDRef="0" linkListNextIDRef="0" textWidth="0" textHeight="0" '
            f'hasTextRef="0" hasNumRef="0">{inner}</hp:subList>'
            f'<hp:cellAddr colAddr="{col}" rowAddr="{row}"/>'
            f'<hp:cellSpan colSpan="{colspan}" rowSpan="{rowspan}"/>'
            f'<hp:cellSz width="{w}" height="{h}"/>'
            f'<hp:cellMargin left="141" right="141" top="141" bottom="141"/></hp:tc>')


def table(rows, widths, row_h=mm(5.5)):
    """rows: [[(charPrId, text, paraPr), …], …] — 첫 행은 머리행"""
    trs = []
    for r, row in enumerate(rows):
        tcs = []
        for c, (cpr, text, ppr) in enumerate(row):
            border = 25 if r == 0 else 26
            tcs.append(cell([(cpr, text)], c, r, widths[c], row_h, border, ppr))
        trs.append("<hp:tr>" + "".join(tcs) + "</hp:tr>")
    total_h = row_h * len(rows)
    return (f'<hp:p id="{_next_id()}" paraPrIDRef="53" styleIDRef="0" pageBreak="0" '
            f'columnBreak="0" merged="0"><hp:run charPrIDRef="48">'
            f'<hp:tbl id="{_next_id()}" zOrder="0" numberingType="TABLE" textWrap="TOP_AND_BOTTOM" '
            f'textFlow="BOTH_SIDES" lock="0" dropcapstyle="None" pageBreak="CELL" repeatHeader="1" '
            f'rowCnt="{len(rows)}" colCnt="{len(widths)}" cellSpacing="0" borderFillIDRef="26" noAdjust="0">'
            f'<hp:sz width="{sum(widths)}" widthRelTo="ABSOLUTE" height="{total_h}" '
            f'heightRelTo="ABSOLUTE" protect="0"/>'
            f'<hp:pos treatAsChar="1" affectLSpacing="0" flowWithText="1" allowOverlap="0" '
            f'holdAnchorAndSO="0" vertRelTo="PARA" horzRelTo="COLUMN" vertAlign="TOP" '
            f'horzAlign="LEFT" vertOffset="0" horzOffset="0"/>'
            f'<hp:outMargin left="0" right="0" top="{mm(1)}" bottom="{mm(1)}"/>'
            f'<hp:inMargin left="141" right="141" top="141" bottom="141"/>'
            + "".join(trs) + '</hp:tbl></hp:run></hp:p>')


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

# ================================================================ 내용
T = {"title": 46, "h2": 47, "body": 48, "small": 49, "b": 50,
     "th": 51, "td": 52, "lede": 53, "foot": 54, "amber": 55, "tdb": 56}
P = {"title": 51, "h2": 52, "body": 53, "body_end": 54,
     "cc": 55, "cl": 56, "li": 57, "foot": 58}

W4 = [int(TEXT_W * .43), int(TEXT_W * .19), int(TEXT_W * .19)]
W4.append(TEXT_W - sum(W4))


def content() -> str:
    b = []
    a = b.append

    # 표제
    a(para(P["title"], [(T["title"], "참고문헌 표준화·검증 에이전트 도입 안내")]))
    a(para(P["foot"], [(T["small"], "문헌정보학 4개 학회 공동 이용  ·  refcheck.kr")]))
    a(para(P["body_end"], [(T["small"],
        "한국도서관·정보학회 · 한국문헌정보학회 · 한국비블리아학회 · 한국정보관리학회")]))

    a(para(P["body_end"], [
        (T["lede"], "원고 파일을 올리면 참고문헌을 뽑아 "),
        (T["b"], "「인용 및 참고문헌의 기술요소와 형식에 관한 공통기준」(2024. 6. 17. 개정)"),
        (T["lede"], "에 맞게 정리하고, 그 문헌이 "),
        (T["b"], "실제로 존재하는지"),
        (T["lede"], "까지 대조해 돌려드립니다. 네 학회가 같은 공통기준을 쓰는 만큼, "
                    "투고 논문의 참고문헌을 한곳에서 이어서 관리하기 위해 만들었습니다."),
    ]))

    # 1. 왜 통합 서비스인가
    a(para(P["h2"], [(T["h2"], "왜 통합 서비스인가")]))
    a(para(P["body"], [
        (T["b"], "같은 기준, 다른 결과.  "),
        (T["body"], "네 학회가 동일한 공통기준을 채택했지만, 기준에 명시되지 않은 부분은 "
                    "학회마다 편집 관행이 다릅니다. 그 차이는 어느 규정집에도 적혀 있지 않고 "
                    "발행본에만 남습니다."),
    ]))
    a(para(P["body_end"], [
        (T["b"], "흩어지면 쌓이지 않습니다.  "),
        (T["body"], "학회별로 따로 점검하면 그 판단이 기록되지 않아 매 호 같은 지적이 반복됩니다. "
                    "한곳에 모으면 편집위원회의 판단이 다음 원고에 자동으로 반영됩니다."),
    ]))

    # 2. 해 주는 일
    a(para(P["h2"], [(T["h2"], "원고 한 편에 해 주는 일")]))
    for head, desc in [
        ("형식 정리", "유형별 기술형식, 저자 표기, 영문 대소문자, 국내→서양→동양 배열, "
                     "동일 저자·연도 a·b·c 부기까지 자동으로 맞춥니다."),
        ("실존 검증", "아홉 곳의 서지 데이터베이스와 대조합니다. 어디에서도 찾지 못한 문헌은 "
                     "‘실존 의심’으로 표시해, 생성형 AI가 지어낸 인용을 걸러냅니다."),
        ("철회 논문 경고", "인용한 문헌이 철회·정정·우려표명된 논문이면 경고합니다. "
                          "게재 후에 발견되면 되돌리기 어려운 문제입니다."),
        ("서지 자동 교정", "공식 서지와 다른 발행연도·권·호·면수, 단행본 출판사를 찾아 "
                          "고칠 내용을 제안하거나 바로 반영합니다."),
        ("본문 ↔ 목록 대조", "본문에 인용됐는데 목록에 없는 문헌, 목록에만 있고 본문에 없는 "
                            "문헌을 찾아냅니다."),
        ("영문화 목록", "국문 참고문헌의 영문 병기 목록을 만듭니다. 저자가 KCI에 등록한 "
                       "공식 영문 제목·성명을 그대로 씁니다."),
    ]:
        a(para(P["li"], [(T["b"], f"· {head}  "), (T["body"], desc)]))
    a(para(P["body_end"], [
        (T["small"], "대조하는 정보원 아홉 곳 —  국내: KCI · 국립중앙도서관 · 국회도서관   "
                     "해외: Crossref · OpenAlex · Semantic Scholar · DataCite · DOAJ · URL 접속 확인"),
    ]))

    # 3. 바로 써 보기
    a(para(P["h2"], [(T["h2"], "바로 써 보기")]))
    a(para(P["li"], [(T["b"], "1  refcheck.kr 접속.  "),
                     (T["body"], "설치할 것이 없습니다. 인터넷 창에서 주소를 여시면 됩니다.")]))
    a(para(P["li"], [(T["b"], "2  코드 입력.  "),
                     (T["body"], "첫 화면에서 아래 코드를 입력하십시오. 편집위원·(부)편집위원장은 "
                                 "두 칸을 모두 채워야 권한이 확인됩니다.")]))
    a(para(P["li"], [(T["body"], "        학회 코드 〇〇〇〇〇〇        역할 코드 〇〇〇〇〇〇        "),
                     (T["small"], "— 소속 학회 편집위원회에서 안내")]))
    a(para(P["body_end"], [(T["b"], "3  원고 올리기.  "),
                           (T["body"], "HWPX · DOCX · PDF · TXT(논문당 10MB 이내, 구형 HWP는 "
                                       "HWPX로 저장 후). 여러 편을 한 번에 올려도 됩니다. 정리된 목록과 "
                                       "검증 결과를 화면에서 확인하고 DOCX·TXT·RIS·BibTeX으로 내려받습니다.")]))

    a(para(P["foot"], [(T["foot"], "참고문헌 표준화·검증 에이전트 도입 안내          1 / 2")]))

    # ── 2쪽
    a(para(P["h2"], [(T["h2"], "권한별로 할 수 있는 일")], page_break=True))
    rows = [[(T["th"], "기능", P["cl"]), (T["th"], "이용자", P["cc"]),
             (T["th"], "편집위원", P["cc"]), (T["th"], "(부)편집위원장", P["cc"])]]
    for name, u, e, c in [
        ("원고 처리·결과 내려받기", "○", "○", "○"),
        ("우리 학회 처리 통계", "—", "○", "○"),
        ("우리 학회 원고 관리·열람", "—", "○", "○"),
        ("발행본 비교 (투고 원고 ↔ 처리 결과 ↔ 최종 발행본)", "—", "○", "○"),
        ("차이 발견 시 채택 요청", "—", "○", "○"),
        ("채택 요청 승인 → 우리 학회 기준으로 등록", "—", "—", "○"),
        ("보관 자료 삭제", "—", "—", "○"),
    ]:
        rows.append([(T["tdb"], name, P["cl"]), (T["td"], u, P["cc"]),
                     (T["td"], e, P["cc"]), (T["td"], c, P["cc"])])
    a(table(rows, W4))
    a(para(P["body_end"], [
        (T["b"], "(부)편집위원장께 부탁드리는 일은 하나입니다.  "),
        (T["body"], "편집위원이 발행본 비교에서 찾아 올린 채택 요청을 승인해 주시는 것입니다. "
                    "승인된 내용은 그 학회 이용자에게만 ‘○○학회 제안’으로 표시되어, 다음 투고자부터 "
                    "같은 지적을 받지 않습니다. 규정에 적히지 않은 편집 관행이 이 과정으로 쌓입니다."),
    ]))

    a(para(P["h2"], [(T["h2"], "발행본 비교 — 이 서비스의 핵심")]))
    a(para(P["body"], [(T["body"],
        "투고 단계의 원고, 에이전트가 정리한 결과, 학회지 최종 발행본을 세 단으로 나란히 놓고 "
        "항목별로 대조합니다. 편집위원회가 실제로 무엇을 고쳤는지가 그대로 드러납니다.")]))
    a(para(P["li"], [(T["b"], "· 발행본 파일을 올리는 방법  "),
                     (T["body"], "구두점·배열 같은 형식 관행까지 확인됩니다.")]))
    a(para(P["body_end"], [(T["b"], "· KCI에서 자동으로 가져오는 방법  "),
                           (T["body"], "파일 없이 논문명만으로. 서지요소 대조에 적합합니다.")]))

    a(para(P["h2"], [(T["h2"], "원고 보관과 개인정보")]))
    a(para(P["body"], [(T["body"],
        "올라온 원고 원본은 발행본 비교를 위해 서버에 보관되며, 소속 학회의 편집위원·"
        "(부)편집위원장만 열람할 수 있습니다. 다른 학회의 자료는 서로 보이지 않습니다. "
        "이 사실은 이용자가 원고를 올리는 화면에도 그대로 고지됩니다.")]))
    a(para(P["body_end"], [(T["body"],
        "보관 자료는 학회 참고문헌 기술을 개선하는 목적으로만 활용하며, 심사·편집 목적 외로 "
        "이용하거나 외부에 제공하지 않습니다. 보관을 원하지 않는 이용자를 위해 "
        "‘참고문헌만 붙여넣기’ 방식을 함께 제공합니다 — 이 경우 원고 파일이 서버에 남지 않습니다. "
        "보관이 끝난 자료는 (부)편집위원장이 언제든 삭제할 수 있습니다.")]))

    a(para(P["h2"], [(T["h2"], "이용 비용")]))
    a(para(P["body"], [(T["amber"], "2026년은 무료입니다.  "),
                       (T["body"], "올해는 네 학회 모두 비용 없이 이용하실 수 있습니다. "
                                   "편집위원회에서 충분히 써 보시고 판단해 주시기 바랍니다.")]))
    a(para(P["body_end"], [(T["body"],
        "2027년부터는 학회지별 실제 사용량에 따른 실비로 운영할 계획입니다. 현재 "),
        (T["b"], "학회지당 연 20만 원 안팎"),
        (T["body"], "으로 예상하고 있으나, 올해 사용량을 집계한 뒤 각 학회와 협의해 확정하겠습니다.")]))

    a(para(P["h2"], [(T["h2"], "문의")]))
    a(para(P["body_end"], [
        (T["b"], "전남대학교 문헌정보학과 박주현 교수      park51566@jnu.ac.kr"),
    ]))
    a(para(P["body_end"], [(T["small"],
        "접속 코드 발급·변경, 편집위원 계정 추가, 학회별 기준 등록에 관한 사항은 "
        "위 연락처로 문의해 주십시오.")]))
    a(para(P["foot"], [(T["foot"], "참고문헌 표준화·검증 에이전트 도입 안내          2 / 2")]))

    # 첫 문단에 구역 설정을 실어야 한다
    # 첫 문단에 구역 설정을 싣고, 그 뒤에 단(段) 정의를 붙인다.
    # colPr이 빠지면 한글이 '파일을 열 수 없습니다'로 거부한다(2026-08-14 실측).
    ctrl = ('<hp:ctrl><hp:colPr id="" type="NEWSPAPER" layout="LEFT" colCount="1" '
            'sameSz="1" sameGap="0"/></hp:ctrl>')
    first = b[0]
    b[0] = first.replace(
        '<hp:run charPrIDRef="46"><hp:t>',
        f'<hp:run charPrIDRef="46">{SEC_PR}{ctrl}</hp:run><hp:run charPrIDRef="46"><hp:t>', 1)
    return "".join(b)


def plain_text(section_xml: str) -> str:
    return re.sub(r"\n{3,}", "\n\n",
                  "\n".join(html.unescape(t) for t in re.findall(r"<hp:t>(.*?)</hp:t>", section_xml, re.S)))


def main():
    if WORK.exists():
        shutil.rmtree(WORK)
    WORK.mkdir(parents=True)
    with zipfile.ZipFile(TPL) as z:
        z.extractall(WORK)

    # 원고형식의 그림은 쓰지 않는다 — 참조와 파일을 함께 지운다
    (WORK / "BinData" / "image1.png").unlink(missing_ok=True)
    try:
        (WORK / "BinData").rmdir()
    except OSError:
        pass
    hpf = (WORK / "Contents" / "content.hpf").read_text(encoding="utf-8")
    hpf = re.sub(r'<opf:item id="image1"[^>]*/>', "", hpf)
    hpf = hpf.replace("<opf:title/>", "<opf:title>참고문헌 표준화·검증 에이전트 도입 안내</opf:title>")
    (WORK / "Contents" / "content.hpf").write_text(hpf, encoding="utf-8")

    hdr = (WORK / "Contents" / "header.xml").read_text(encoding="utf-8")
    (WORK / "Contents" / "header.xml").write_text(build_header(hdr), encoding="utf-8")

    sec = SEC_HEAD + content() + "</hs:sec>"
    (WORK / "Contents" / "section0.xml").write_text(sec, encoding="utf-8")
    (WORK / "Preview" / "PrvText.txt").write_text(plain_text(sec), encoding="utf-8")

    OUT.unlink(missing_ok=True)
    with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as z:
        # mimetype은 압축하지 않고 맨 앞에 — OCF 규약
        z.writestr(zipfile.ZipInfo("mimetype"), "application/hwp+zip", zipfile.ZIP_STORED)
        for p in sorted(WORK.rglob("*")):
            if p.is_file() and p.name != "mimetype":
                z.write(p, p.relative_to(WORK).as_posix())
    print("생성:", OUT, f"({OUT.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
