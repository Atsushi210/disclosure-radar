"""
日次株価の取得とキャッシュ（yfinance、無料）

- 分割・配当で調整済みの始値・終値・出来高を使う（auto_adjust=True）
- キャッシュは data/prices.pkl（銘柄コード → DataFrame[open, close, volume]）
- 上場廃止銘柄は yfinance で取れないことが多い（生存バイアス。集計レポートに注記する）
"""
from __future__ import annotations

import logging
import pickle
import time
from pathlib import Path

import pandas as pd

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
CACHE = DATA_DIR / "prices.pkl"
BENCHMARK = "1306"  # TOPIX連動ETF。超過リターンの基準
CHUNK = 50
SLEEP_SEC = 2.0

log = logging.getLogger(__name__)


def load_cache(path: Path = CACHE) -> dict[str, pd.DataFrame]:
    if not path.exists():
        return {}
    with path.open("rb") as f:
        return pickle.load(f)


def save_cache(cache: dict[str, pd.DataFrame], path: Path = CACHE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with tmp.open("wb") as f:
        pickle.dump(cache, f)
    tmp.replace(path)


def _split(df: pd.DataFrame, tickers: list[str]) -> dict[str, pd.DataFrame]:
    out: dict[str, pd.DataFrame] = {}
    for t in tickers:
        if t not in df.columns.get_level_values(0):
            continue
        sub = df[t][["Open", "Close", "Volume"]].dropna(subset=["Close"])
        if sub.empty:
            continue
        sub.columns = ["open", "close", "volume"]
        sub.index = pd.DatetimeIndex(sub.index).tz_localize(None).normalize()
        out[t.removesuffix(".T")] = sub
    return out


def download(codes: list[str], start: str, end: str, refresh: bool = False) -> dict[str, pd.DataFrame]:
    """未取得の銘柄だけを50銘柄ずつ取得してキャッシュに足す。"""
    import yfinance as yf

    cache = load_cache()
    todo = sorted({c for c in codes if refresh or c not in cache} | ({BENCHMARK} - set(cache)))
    for i in range(0, len(todo), CHUNK):
        tickers = [f"{c}.T" for c in todo[i:i + CHUNK]]
        df = yf.download(tickers, start=start, end=end, auto_adjust=True, group_by="ticker",
                         progress=False, threads=True)
        got = _split(df, tickers)
        cache.update(got)
        for c in todo[i:i + CHUNK]:
            cache.setdefault(c, pd.DataFrame(columns=["open", "close", "volume"]))  # 取得不可も記録し再試行しない
        save_cache(cache)
        log.info("株価取得 %d/%d（成功 %d）", min(i + CHUNK, len(todo)), len(todo), len(got))
        time.sleep(SLEEP_SEC)
    return cache
