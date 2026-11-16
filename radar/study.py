"""
イベントスタディ: 開示の種類ごとに、その後の株価が市場（TOPIX連動ETF 1306）に比べてどう動いたかを集計する

各イベントについて次の超過リターン（銘柄 − 1306）を計算する:
  car0   : 反応日の前日終値 → 反応日の終値（ニュースへの初期反応の大きさ）
  gap    : 前日終値 → 反応日の始値（引け後・寄り前の開示のみ）
  drift5 : 通知を見て買える最初の始値 → その5営業日目の終値（個人が取れる値幅）
  drift20: 同じく20営業日目の終値

反応日とエントリー:
  寄り前（9:00前）       → 反応日=当日、エントリー=当日始値
  取引時間中             → 反応日=当日、エントリー=翌営業日始値
  引け後（大引け以降）   → 反応日=翌営業日、エントリー=翌営業日始値
  大引けは2024-11-05から15:30（それ以前は15:00）
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, time

import numpy as np
import pandas as pd

CLOSE_CHANGE = date(2024, 11, 5)
METRICS = ["car0", "gap", "drift5", "drift20"]


def close_time(d: date) -> time:
    return time(15, 30) if d >= CLOSE_CHANGE else time(15, 0)


@dataclass(frozen=True)
class Timing:
    d0: int  # 反応日のカレンダー位置
    entry: int  # エントリー日の位置
    off_hours: bool  # 寄り前・引け後（gap を計算する）


def timing(published_at: str, cal: pd.DatetimeIndex) -> Timing | None:
    """開示時刻から反応日とエントリー日を決める。cal は取引日の昇順インデックス。"""
    ts = pd.Timestamp(published_at)
    day = ts.normalize()
    pos = int(cal.searchsorted(day))
    if pos >= len(cal):
        return None
    is_trading_day = cal[pos] == day
    t = ts.time()
    if is_trading_day and t < time(9, 0):
        return Timing(pos, pos, True)
    if is_trading_day and t < close_time(day.date()):
        return Timing(pos, pos + 1, False)
    nxt = pos + 1 if is_trading_day else pos
    return Timing(nxt, nxt, True)


def event_returns(events: pd.DataFrame, prices: dict[str, pd.DataFrame], bench_code: str = "1306") -> pd.DataFrame:
    """events（doc_id, code, published_at, type, direction）に超過リターンの列を足して返す。"""
    bench = prices[bench_code]
    cal = bench.index
    b_open, b_close = bench["open"].to_numpy(), bench["close"].to_numpy()

    rows = []
    for code, grp in events.groupby("code", sort=False):
        px = prices.get(code)
        if px is None or px.empty:
            continue
        px = px.reindex(cal)
        o, c, v = px["open"].to_numpy(), px["close"].to_numpy(), px["volume"].to_numpy()
        for ev in grp.itertuples(index=False):
            tm = timing(ev.published_at, cal)
            if tm is None or tm.d0 < 1:
                continue
            d0, e = tm.d0, tm.entry
            prev = d0 - 1
            r = {"doc_id": ev.doc_id, "type": ev.type}
            r["car0"] = _ret(c[prev], c[d0]) - _ret(b_close[prev], b_close[d0]) if d0 < len(cal) else np.nan
            r["gap"] = (_ret(c[prev], o[d0]) - _ret(b_close[prev], b_open[d0])) if tm.off_hours and d0 < len(cal) else np.nan
            for name, k in (("drift5", 4), ("drift20", 19)):
                end = e + k
                r[name] = (_ret(o[e], c[end]) - _ret(b_open[e], b_close[end])) if end < len(cal) else np.nan
            lo = max(0, prev - 19)
            r["turnover"] = float(np.nanmedian(c[lo:prev + 1] * v[lo:prev + 1])) if prev >= 0 else np.nan
            rows.append(r)
    return events.merge(pd.DataFrame(rows), on=["doc_id", "type"], how="inner") if rows else events.iloc[0:0]


def _ret(a: float, b: float) -> float:
    if not (a and b) or np.isnan(a) or np.isnan(b) or a <= 0:
        return np.nan
    return b / a - 1


def summarize(df: pd.DataFrame, by: list[str], min_n: int = 30) -> pd.DataFrame:
    """グループごとに件数・平均・中央値・勝率・t値を出す。外れ値は上下1%で丸める。"""
    out = []
    for key, g in df.groupby(by):
        row = dict(zip(by, key if isinstance(key, tuple) else (key,)))
        row["n"] = len(g)
        for m in METRICS:
            x = g[m].dropna()
            if len(x) < 5:
                continue
            lo, hi = x.quantile([0.01, 0.99])
            x = x.clip(lo, hi)
            sd = x.std(ddof=1)
            row[f"{m}_mean"] = x.mean() * 100
            row[f"{m}_med"] = x.median() * 100
            row[f"{m}_hit"] = (x > 0).mean() * 100
            row[f"{m}_t"] = x.mean() / sd * np.sqrt(len(x)) if sd > 0 else np.nan
        out.append(row)
    res = pd.DataFrame(out)
    return res[res["n"] >= min_n].sort_values("n", ascending=False) if not res.empty else res
