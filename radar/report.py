"""
イベントスタディのレポート作成と重要度テーブルの導出

  python -m radar.report   → docs/event_study.md と data/importance.json を出力
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import pandas as pd

from radar import db
from radar.master import load_master
from radar.prices import load_cache
from radar.study import event_returns, summarize

ROOT = Path(__file__).resolve().parent.parent
REPORT = ROOT / "docs" / "event_study.md"
IMPORTANCE = ROOT / "data" / "importance.json"
MIN_TURNOVER = 10_000_000  # 売買代金の中央値1,000万円/日未満は「実際には売買しにくい」として除外
SIZE_ORDER = ["large", "mid", "small", "micro"]
SIZE_JA = {"large": "大型", "mid": "中型", "small": "小型", "micro": "超小型"}
DIR_JA = {"up": "↑", "down": "↓", "?": "?"}

log = logging.getLogger(__name__)


def load_events(start: str = "2019-01-01", end: str = "2026-10-15") -> pd.DataFrame:
    """同じ銘柄・同じ日・同じ種別のイベントは1件にまとめる（同日の重複開示による二重計上を防ぐ）。"""
    with db.get_conn() as conn:
        df = pd.read_sql_query(
            """SELECT e.doc_id, d.code, d.published_at, e.type, e.direction, d.title
               FROM events e JOIN disclosures d USING (doc_id)
               WHERE d.published_at >= ? AND d.published_at < ?""",
            conn, params=(start, end),
        )
    df["day"] = df["published_at"].str[:10]
    df = df.sort_values("published_at").drop_duplicates(["code", "day", "type"])
    master = load_master()
    df["size"] = df["code"].map(lambda c: master[c].size if c in master else "delisted")
    return df


def derive_importance(s: pd.DataFrame) -> int:
    """集計1行から重要度を決める。
    3: 初期反応の中央値が±3%以上、または20日ドリフトが統計的に有意（|t|≥2）かつ平均±1.5%以上
    2: 初期反応の中央値が±1%以上、または5日ドリフトが有意
    1: それ以外
    """
    car = abs(s.get("car0_med", 0) or 0)
    d20, t20 = abs(s.get("drift20_mean", 0) or 0), abs(s.get("drift20_t", 0) or 0)
    t5 = abs(s.get("drift5_t", 0) or 0)
    if car >= 3 or (t20 >= 2 and d20 >= 1.5):
        return 3
    if car >= 1 or t5 >= 2:
        return 2
    return 1


def fmt_table(s: pd.DataFrame, keys: list[str]) -> str:
    cols = keys + ["n", "car0_med", "gap_mean", "drift5_mean", "drift5_t", "drift20_mean", "drift20_t", "drift20_hit", "imp"]
    head = keys + ["件数", "初期反応 中央値%", "寄り付きギャップ 平均%", "5日 平均%", "5日 t", "20日 平均%", "20日 t", "20日 勝率%", "重要度"]
    lines = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    for _, r in s.iterrows():
        vals = []
        for c in cols:
            v = r.get(c)
            if c == "direction":
                vals.append(DIR_JA.get(v, v))
            elif c == "size":
                vals.append(SIZE_JA.get(v, v))
            elif isinstance(v, float):
                vals.append("" if pd.isna(v) else f"{v:.1f}" if not c.endswith("_t") else f"{v:.2f}")
            else:
                vals.append(str(v))
        lines.append("| " + " | ".join(vals) + " |")
    return "\n".join(lines)


def build() -> None:
    events = load_events()
    prices = load_cache()
    res = event_returns(events, prices)
    res = res[res["size"].isin(SIZE_ORDER)]
    tradable = res[res["turnover"] >= MIN_TURNOVER]
    log.info("イベント %d件 → 株価あり %d件 → 売買可能 %d件", len(events), len(res), len(tradable))

    by_type = summarize(tradable, ["type", "direction"])
    by_size = summarize(tradable, ["type", "direction", "size"])
    for s in (by_type, by_size):
        s["imp"] = s.apply(derive_importance, axis=1)

    importance = {f"{r.type}|{r.direction}|{r.size}": int(r.imp) for r in by_size.itertuples()}
    importance.update({f"{r.type}|{r.direction}|*": int(r.imp) for r in by_type.itertuples()})
    IMPORTANCE.write_text(json.dumps(importance, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")

    yearly = tradable.assign(year=tradable["published_at"].str[:4])
    stable = summarize(yearly, ["type", "direction", "year"], min_n=20)

    REPORT.parent.mkdir(exist_ok=True)
    REPORT.write_text("\n\n".join([
        "# イベントスタディ結果",
        f"- 期間: 2019-01〜2026-10（開示 {len(events):,}件、株価が取れたもの {len(res):,}件、"
        f"売買代金1,000万円/日以上 {len(tradable):,}件）",
        "- 超過リターン = 銘柄 − TOPIX連動ETF(1306)。外れ値は上下1%で丸め",
        "- 初期反応 = 反応日前日終値→反応日終値。5日/20日 = 通知後に買える最初の始値→5/20営業日目終値",
        "- 注意: 上場廃止銘柄は株価が取れず除外（生存バイアス）。過去分の業績修正はタイトルでしか方向を判定できない",
        "## 種別 × 方向", fmt_table(by_type, ["type", "direction"]),
        "## 種別 × 方向 × 規模", fmt_table(by_size, ["type", "direction", "size"]),
        "## 年別（安定性の確認）", fmt_table(stable, ["type", "direction", "year"]),
    ]), encoding="utf-8")
    tradable.to_csv(ROOT / "data" / "study_events.csv", index=False)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    build()
