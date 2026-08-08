# -*- coding: utf-8 -*-
"""Claude API 사용 비용 집계.

Anthropic API는 계정 잔액 조회를 제공하지 않는다. 대신 모든 응답의 usage(토큰 수)를
모델별 공개 단가로 환산해 비용을 추정하고, 관리자가 설정한 월 예산과 대비해 보여준다.
정확한 청구액은 console.anthropic.com에서 확인해야 한다.

- 건별 비용: process_file 처리 동안 스레드별로 누적(start_job/end_job) → 이력에 저장
- 일자·모델별 누적: api_cost_log.json (파일 크기 억제를 위해 (날짜, 모델) 단위로 합산)
"""
import json
import threading
import time
from pathlib import Path

APP_DIR = Path(__file__).parent
COST_LOG_PATH = APP_DIR / "api_cost_log.json"
_LOCK = threading.Lock()
_LOCAL = threading.local()

# 100만 토큰당 미국 달러 (input, output) — 2026-08 기준 공개 정가
PRICES = {
    "claude-opus-5": (5.0, 25.0),
    "claude-sonnet-5": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
}
# 한시 도입가 — 기간 안에는 이 단가가 적용된다. (모델: (input, output, 종료일))
INTRO_PRICES = {
    "claude-sonnet-5": (2.0, 10.0, "2026-08-31"),
}
DEFAULT_PRICE = (5.0, 25.0)
CACHE_READ_RATE = 0.1    # 캐시 읽기는 입력 단가의 0.1배
CACHE_WRITE_RATE = 1.25  # 캐시 쓰기는 입력 단가의 1.25배


def price_of(model: str, day: str = "") -> tuple[float, float]:
    """해당 날짜(YYYY-MM-DD, 생략 시 오늘)에 적용되는 (입력, 출력) 단가."""
    intro = INTRO_PRICES.get(model)
    if intro:
        today = day or time.strftime("%Y-%m-%d")
        if today <= intro[2]:
            return intro[0], intro[1]
    return PRICES.get(model, DEFAULT_PRICE)


def _usage_dict(usage) -> dict:
    """SDK usage 객체 → 평범한 dict(없는 필드는 0)."""
    def g(name):
        v = getattr(usage, name, None) if not isinstance(usage, dict) else usage.get(name)
        return int(v or 0)
    return {"input": g("input_tokens"), "output": g("output_tokens"),
            "cache_read": g("cache_read_input_tokens"),
            "cache_write": g("cache_creation_input_tokens")}


def calc_usd(model: str, u: dict, day: str = "") -> float:
    """토큰 사용량 → 달러(그 날짜에 적용되던 단가로 환산)."""
    pin, pout = price_of(model, day)
    return ((u["input"] * pin
             + u["cache_read"] * pin * CACHE_READ_RATE
             + u["cache_write"] * pin * CACHE_WRITE_RATE
             + u["output"] * pout) / 1_000_000)


# ---------------------------------------------------------------- 건별 누적

def start_job():
    """이 스레드에서 지금부터 발생하는 API 사용을 한 건으로 누적 시작."""
    _LOCAL.acc = {"calls": 0, "input": 0, "output": 0, "cache_read": 0,
                  "cache_write": 0, "usd": 0.0, "models": {}}


def end_job() -> dict | None:
    """누적 종료 후 집계 반환(시작하지 않았으면 None)."""
    acc = getattr(_LOCAL, "acc", None)
    _LOCAL.acc = None
    if acc:
        acc["usd"] = round(acc["usd"], 6)
    return acc


def record(model: str, usage) -> float:
    """API 호출 1건의 사용량 기록 — 건별 누적 + 일자·모델별 로그. 비용(달러) 반환."""
    u = _usage_dict(usage)
    usd = calc_usd(model, u)
    acc = getattr(_LOCAL, "acc", None)
    if acc is not None:
        acc["calls"] += 1
        for k in ("input", "output", "cache_read", "cache_write"):
            acc[k] += u[k]
        acc["usd"] += usd
        acc["models"][model] = acc["models"].get(model, 0) + 1
    try:
        _append_log(model, u, usd)
    except OSError:
        pass  # 비용 기록 실패가 처리 자체를 막지 않도록
    return usd


def _append_log(model: str, u: dict, usd: float):
    today = time.strftime("%Y-%m-%d")
    with _LOCK:
        data = _load_unlocked()
        row = next((r for r in data if r["date"] == today and r["model"] == model), None)
        if row is None:
            row = {"date": today, "model": model, "calls": 0, "input": 0, "output": 0,
                   "cache_read": 0, "cache_write": 0, "usd": 0.0}
            data.append(row)
        row["calls"] += 1
        for k in ("input", "output", "cache_read", "cache_write"):
            row[k] += u[k]
        row["usd"] = round(row["usd"] + usd, 6)
        data = data[-800:]  # 약 2년치(모델 3종 기준) 유지
        tmp = COST_LOG_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        import os
        os.replace(tmp, COST_LOG_PATH)


def _load_unlocked() -> list[dict]:
    if COST_LOG_PATH.exists():
        try:
            return json.loads(COST_LOG_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return []
    return []


def load_log() -> list[dict]:
    with _LOCK:
        return _load_unlocked()


# ---------------------------------------------------------------- 요약

def summary(budget_usd: float = 0.0, rate_krw: float = 1400.0) -> dict:
    """오늘·이번 달·전체 비용과 월 예산 대비 잔액."""
    data = load_log()
    today = time.strftime("%Y-%m-%d")
    month = time.strftime("%Y-%m")

    def total(rows):
        return round(sum(r.get("usd", 0.0) for r in rows), 4)

    month_rows = [r for r in data if r["date"].startswith(month)]
    by_model: dict[str, dict] = {}
    for r in month_rows:
        m = by_model.setdefault(r["model"], {"model": r["model"], "calls": 0, "usd": 0.0,
                                             "input": 0, "output": 0})
        m["calls"] += r.get("calls", 0)
        m["usd"] = round(m["usd"] + r.get("usd", 0.0), 6)
        m["input"] += r.get("input", 0)
        m["output"] += r.get("output", 0)

    by_day: dict[str, float] = {}
    for r in data:
        by_day[r["date"]] = round(by_day.get(r["date"], 0.0) + r.get("usd", 0.0), 6)
    recent_days = [{"date": d, "usd": by_day[d]} for d in sorted(by_day, reverse=True)[:30]]

    month_usd = total(month_rows)
    out = {
        "today_usd": total([r for r in data if r["date"] == today]),
        "month_usd": month_usd,
        "total_usd": total(data),
        "month": month,
        "by_model": sorted(by_model.values(), key=lambda m: -m["usd"]),
        "recent_days": recent_days,
        "rate_krw": rate_krw,
        "budget_usd": budget_usd,
    }
    if budget_usd > 0:
        out["remain_usd"] = round(max(0.0, budget_usd - month_usd), 4)
        out["used_ratio"] = round(month_usd / budget_usd, 4)
        out["level"] = "over" if month_usd >= budget_usd else (
            "warn" if month_usd >= budget_usd * 0.8 else "ok")
    return out
