# -*- coding: utf-8 -*-
"""종단 간 테스트: 샘플 원고 → 파이프라인 → 결과 요약 출력 (서버 없이 직접 호출)."""
import io
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import make_sample
from main import process_file
import report


def progress(stage, filename):
    print(f"  [{filename}] {stage}")


def run(path: Path, verify: bool):
    print(f"\n=== 테스트: {path.name} (verify={verify}) ===")
    data = path.read_bytes()
    res = process_file(path.name, data, {
        "style_id": "munpyeonhyeop", "verify": verify,
        "crosscheck": True, "english": False,
    }, progress)
    if res.get("error"):
        print("!! 오류:", res["error"])
        return res
    print("요약:", json.dumps(res["summary"], ensure_ascii=False))
    print("경고:", res.get("warnings"))
    print("\n--- 최종 목록 ---")
    g = None
    for it in res["items"]:
        if it["group"] != g:
            g = it["group"]
            print(f"[{g}]")
        v = (it.get("verify") or {}).get("status", "")
        print(f"  {it['formatted']}   {'<'+v+'>' if v else ''}")
        if it["issues"]:
            print(f"     !! {' / '.join(it['issues'])}")
    cc = res.get("crosscheck")
    if cc:
        print("\n--- 본문 대조 ---")
        print("본문 인용 수:", cc["citations_found"])
        for c in cc["cited_not_listed"]:
            print("  [목록 누락]", c["name"], c["year"], "-", c["snippet"])
        for c in cc["listed_not_cited"]:
            print("  [본문 미인용]", c["authors"], c["year"])
    # DOCX 생성 확인
    docx_bytes = report.build_result_docx(res)
    out = path.parent / (path.stem + "_결과테스트.docx")
    out.write_bytes(docx_bytes)
    print(f"\nDOCX 생성 확인: {out} ({len(docx_bytes)//1024} KB)")
    return res


if __name__ == "__main__":
    docx_path = make_sample.make_docx()
    txt_path = make_sample.make_txt()
    verify = "--verify" in sys.argv
    run(docx_path, verify)
    run(txt_path, False)
