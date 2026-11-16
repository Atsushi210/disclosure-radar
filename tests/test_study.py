"""イベントスタディの計算テスト（人工の株価で検算）"""
import numpy as np
import pandas as pd
import pytest

from radar.study import Timing, event_returns, summarize, timing

CAL = pd.bdate_range("2026-11-02", periods=30)


def px(open_, close):
    return pd.DataFrame({"open": open_, "close": close, "volume": np.full(len(CAL), 1000.0)}, index=CAL)


@pytest.mark.parametrize(
    "published_at, d0, entry, off_hours",
    [
        ("2026-11-04 08:00", 2, 2, True),    # 寄り前 → 当日
        ("2026-11-04 13:00", 2, 3, False),   # 取引時間中 → 翌日エントリー
        ("2026-11-04 15:30", 3, 3, True),    # 大引け（15:30）以降 → 翌日
        ("2026-11-07 10:00", 5, 5, True),    # 土曜 → 月曜
    ],
)
def test_timing(published_at, d0, entry, off_hours):
    assert timing(published_at, CAL) == Timing(d0, entry, off_hours)


def test_close_time_before_2024_11():
    cal = pd.bdate_range("2024-10-01", periods=5)
    assert timing("2024-10-01 15:10", cal).d0 == 1  # 当時の大引けは15:00 → 引け後扱い


def test_event_returns_excess():
    flat = np.full(len(CAL), 100.0)
    bench = px(flat, flat)
    # 銘柄: 11/04 引け後の開示 → 11/05 寄りで +10% ギャップ、その後5日で始値から +5%
    o = flat.copy()
    c = flat.copy()
    o[3:], c[3:] = 110.0, 110.0
    c[7:] = 115.5
    events = pd.DataFrame([{"doc_id": "a", "code": "1111", "published_at": "2026-11-04 16:00",
                            "type": "buyback", "direction": "up"}])
    r = event_returns(events, {"1306": bench, "1111": px(o, c)}).iloc[0]
    assert r.gap == pytest.approx(0.10)
    assert r.car0 == pytest.approx(0.10)
    assert r.drift5 == pytest.approx(0.05)
    assert r.turnover == pytest.approx(100_000.0)


def test_summarize_filters_small_groups():
    df = pd.DataFrame({"type": ["a"] * 40 + ["b"] * 3, "car0": [0.01] * 43,
                       "gap": np.nan, "drift5": [0.02] * 43, "drift20": np.nan})
    s = summarize(df, ["type"])
    assert list(s["type"]) == ["a"]
    assert s.iloc[0]["car0_mean"] == pytest.approx(1.0)
