"""Deterministic baseline, anomaly and waste detection. All money is Decimal; no model touches these numbers.

Baseline: per (tenant, service, workload) daily series. Expected value for a day is the median of the same
weekday over the history window (weekly seasonality) when >= 3 samples exist, else the overall median.
Spread: MAD of history residuals, floored so a perfectly flat history cannot create infinite z-scores.
"""
import math
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from statistics import median

D4 = Decimal("0.0001")


def money(x) -> Decimal:
    return Decimal(x).quantize(D4)


def analyze_series(daily: dict[date, Decimal], window_end: date, *, history_days=28, window_days=3,
                   min_excess=5.0, min_history_points=21) -> dict:
    ws = window_end - timedelta(days=window_days)
    hist = {d: a for d, a in daily.items() if ws - timedelta(days=history_days) <= d < ws}
    win = {d: a for d, a in daily.items() if ws <= d < window_end}
    if len(hist) < min_history_points or len(win) < 2:
        return {"status": "insufficient_data",
                "features": {"history_points": len(hist), "window_points": len(win),
                             "required_history_points": min_history_points}}

    level = median(hist.values())
    by_wd: dict[int, list[Decimal]] = {}
    for d, a in hist.items():
        by_wd.setdefault(d.weekday(), []).append(a)
    seasonal = {wd: median(v) for wd, v in by_wd.items() if len(v) >= 3}

    def expected(d: date) -> Decimal:
        return seasonal.get(d.weekday(), level)

    resid = [a - expected(d) for d, a in hist.items()]
    r_med = median(resid)
    mad = median([abs(r - r_med) for r in resid])
    sigma = max(float(mad) * 1.4826, float(level) * 0.05, 0.25)

    rows, zs = [], []
    for d in sorted(win):
        e, a = expected(d), win[d]
        z = float(a - e) / sigma
        zs.append(z)
        rows.append({"date": d.isoformat(), "actual": str(money(a)), "expected": str(money(e)), "z": round(z, 2)})
    actual_total = sum(win.values(), Decimal(0))
    expected_total = sum((expected(d) for d in win), Decimal(0))
    n = len(win)
    avg_excess = (actual_total - expected_total) / n
    rel = float(avg_excess / expected_total * n) if expected_total > 0 else (math.inf if avg_excess > 0 else 0.0)
    mean_z = sum(zs) / n
    days_over = sum(1 for z in zs if z >= 3)
    is_anomaly = days_over >= 2 and mean_z >= 4 and float(avg_excess) >= min_excess and rel >= 0.25
    # score in [0,1): deterministic squash of the mean z-score
    score = min(0.99, round(1 - math.exp(-max(mean_z, 0) / 3), 2))
    return {
        "status": "anomaly" if is_anomaly else "normal",
        "actual": money(actual_total), "expected": money(expected_total), "anomaly_score": score,
        "features": {"history_points": len(hist), "window_points": n, "level": str(money(level)),
                     "sigma": round(sigma, 4), "mean_z": round(mean_z, 2), "days_over_3sigma": days_over,
                     "avg_daily_excess": str(money(avg_excess)), "relative_excess": round(rel, 3) if math.isfinite(rel) else None,
                     "seasonality_applied": len(seasonal) == 7, "days": rows},
    }


def idle_ec2(res: dict, cpu: list[Decimal], window_end: date, *, idle_days=7, cpu_pct=5.0, min_points=6, min_age_days=7) -> dict:
    """Idle needs enough data: missing metrics are 'insufficient_data', never 'idle'."""
    if res["state"] != "running":
        return {"idle": False, "reason": f"state={res['state']}"}
    launched = res.get("launch_time")
    if launched and launched.date() > window_end - timedelta(days=min_age_days):
        return {"idle": False, "reason": "younger than minimum resource age"}
    if len(cpu) < min_points:
        return {"idle": False, "reason": "insufficient_data", "points": len(cpu)}
    below = sum(1 for c in cpu if c < Decimal(str(cpu_pct)))
    mean = sum(cpu, Decimal(0)) / len(cpu)
    idle = below / len(cpu) >= 0.9 and mean < Decimal(str(cpu_pct))
    return {"idle": idle, "points": len(cpu), "cpu_mean_pct": str(mean.quantize(Decimal("0.01"))),
            "days_below_threshold": below, "threshold_pct": cpu_pct, "window_days": idle_days}


def unattached_ebs(res: dict, window_end: date, *, min_days=7) -> dict:
    if res["state"] != "available" or res.get("attached"):
        return {"unattached": False}
    launched = res.get("launch_time")
    end = datetime.combine(window_end, datetime.min.time(), tzinfo=timezone.utc) + timedelta(days=1)   # end of the window day
    age = max((end - launched).days, 0) if launched else None
    return {"unattached": age is not None and age >= min_days, "age_days": age}
