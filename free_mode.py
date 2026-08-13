# -*- coding: utf-8 -*-
"""무료(규칙 기반) 모드 실행기 — Claude API를 절대 호출하지 않는다.

이 앱은 .env의 ANTHROPIC_API_KEY가 있으면 자동으로 AI 모드(유료)로 켜진다.
(app/aiengine.py: env_get 이 .env를 OS 환경변수보다 우선해 읽으므로 환경변수로는 못 끈다.)
그래서 이 스크립트는 실행하는 동안만 .env의 그 줄을 주석 처리하고, 끝나면 원상복구한다.

  안전장치
  - 종료·예외·Ctrl+C 어느 경우에도 finally 에서 .env를 되돌린다.
  - 강제 종료로 복구가 안 됐더라도, 다음 실행 때 시작 단계에서 자동으로 되돌린다.
  - 자식 프로세스에도 빈 키를 넘겨 OS 환경변수를 통한 유입까지 막는다.
  - 실행 전후 app/api_cost_log.json 을 대조해 비용이 0원인지 확인해 준다.

사용법:
  python free_mode.py                      웹 UI 서버 실행 (http://localhost:8765)
  python free_mode.py --file 원고.docx      원고 1건을 헤드리스로 처리해 결과 저장
  python free_mode.py --file 원고.docx --no-verify   외부 DB 대조 없이 형식만 정리
"""
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ENV_PATH = ROOT / ".env"
COST_LOG = ROOT / "app" / "api_cost_log.json"
MARK = "# [무료모드 임시 비활성화] "
TARGETS = ("ANTHROPIC_API_KEY", "CLAUDE_API_KEY", "API_KEY")


# ------------------------------------------------------------ .env 토글

def _lines():
    return ENV_PATH.read_text(encoding="utf-8-sig").splitlines()


def restore() -> int:
    """주석 처리해 둔 줄을 되돌린다. 되돌린 줄 수를 반환."""
    if not ENV_PATH.exists():
        return 0
    out, n = [], 0
    for line in _lines():
        if line.startswith(MARK):
            out.append(line[len(MARK):])
            n += 1
        else:
            out.append(line)
    if n:
        ENV_PATH.write_text("\n".join(out) + "\n", encoding="utf-8")
    return n


def disable() -> int:
    """유료 키 줄을 주석 처리한다. 처리한 줄 수를 반환."""
    if not ENV_PATH.exists():
        return 0
    out, n = [], 0
    for line in _lines():
        name = line.split("=", 1)[0].strip().upper() if "=" in line else ""
        if name in TARGETS and not line.lstrip().startswith("#"):
            out.append(MARK + line)
            n += 1
        else:
            out.append(line)
    if n:
        ENV_PATH.write_text("\n".join(out) + "\n", encoding="utf-8")
    return n


def cost_snapshot():
    if not COST_LOG.exists():
        return None
    try:
        return json.loads(COST_LOG.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def cost_total(snap):
    return round(sum(e.get("usd", 0) for e in (snap or [])), 6)


def child_env():
    env = os.environ.copy()
    for t in TARGETS:
        env[t] = ""  # OS 환경변수를 통한 유입까지 차단
    return env


# ------------------------------------------------------------ 실행 모드

def run_server():
    print("  웹 UI: http://localhost:8765   (종료: 이 창에서 Ctrl+C)")
    subprocess.call([sys.executable, "-m", "uvicorn", "main:app",
                     "--host", "127.0.0.1", "--port", "8765"],
                    cwd=str(ROOT / "app"), env=child_env())


def run_file(path: Path, verify: bool, outdir: Path | None):
    sys.path.insert(0, str(ROOT / "app"))
    for t in TARGETS:
        os.environ[t] = ""
    import aiengine
    if aiengine.is_configured():
        raise SystemExit("[중단] AI 모드가 여전히 켜져 있습니다. 비용이 발생할 수 있어 중단합니다.")
    print("  엔진: 규칙 기반 (무료)")

    from main import process_file
    import report

    data = path.read_bytes()
    res = process_file(path.name, data, {
        "style_id": "munpyeonhyeop", "verify": verify,
        "crosscheck": True, "english": False,
    }, lambda stage, fn: print(f"  · {stage}"))

    if res.get("error"):
        raise SystemExit(f"[실패] {res['error']}")

    outdir = outdir or path.parent
    outdir.mkdir(parents=True, exist_ok=True)
    stem = path.stem + "_참고문헌정리"
    (outdir / (stem + ".docx")).write_bytes(report.build_result_docx(res))
    (outdir / (stem + ".txt")).write_text(report.build_result_txt(res), encoding="utf-8")
    (outdir / (stem + ".ris")).write_text(report.build_ris(res), encoding="utf-8")

    s = res["summary"]
    print(f"\n  총 {s['total']}건 / 형식변경 {s['changed']}건 / 확인필요 {s['needs_check']}건 / "
          f"실존확인 {s['verified']}건 / 철회 {s['retracted']}건 / 생성의심 {s['suspect']}건")
    cc = res.get("crosscheck") or {}
    if cc:
        print(f"  본문 대조: 목록 누락 {len(cc.get('cited_not_listed', []))}건 / "
              f"본문 미인용 {len(cc.get('listed_not_cited', []))}건")
    print(f"  저장: {outdir / (stem + '.docx')} (+ .txt, .ris)")


# ------------------------------------------------------------ main

def main():
    ap = argparse.ArgumentParser(description="참고문헌 에이전트를 무료(규칙 기반) 모드로 실행")
    ap.add_argument("--file", help="처리할 원고 파일 (HWPX·DOCX·PDF·TXT). 생략하면 웹 UI 서버 실행")
    ap.add_argument("--outdir", help="결과 저장 폴더 (기본: 원고와 같은 폴더)")
    ap.add_argument("--no-verify", action="store_true", help="외부 DB 실존 검증 생략 (형식 정리만)")
    args = ap.parse_args()

    healed = restore()  # 이전 실행이 비정상 종료됐다면 먼저 되돌린다
    if healed:
        print(f"  [자동복구] 지난 실행에서 남은 주석 {healed}줄을 되돌렸습니다.")

    before = cost_snapshot()
    n = disable()
    print(f"  AI 모드 차단: .env {n}줄 임시 주석 처리 — Claude API 호출 없음")
    try:
        if args.file:
            p = Path(args.file)
            if not p.exists():
                raise SystemExit(f"[실패] 파일이 없습니다: {p}")
            run_file(p, not args.no_verify, Path(args.outdir) if args.outdir else None)
        else:
            run_server()
    except KeyboardInterrupt:
        print("\n  중단됨.")
    finally:
        back = restore()
        print(f"  .env 원상복구 완료 ({back}줄)")
        after = cost_snapshot()
        b, a = cost_total(before), cost_total(after)
        if a > b:
            print(f"  ⚠ API 비용이 발생했습니다: ${a - b:.6f} (누적 ${a:.6f})")
        else:
            print(f"  ✅ 이번 실행 API 비용: $0.000000 (누적 ${a:.6f} 변동 없음)")


if __name__ == "__main__":
    main()
