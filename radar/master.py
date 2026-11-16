"""
銘柄マスタ（JPX 東証上場銘柄一覧 data_j.xlsx）

規模区分（TOPIX のサイズ）を小型株判定に使う:
  large = Core30 / Large70、mid = Mid400、small = Small 1、
  micro = Small 2 と TOPIX 対象外（スタンダード・グロースの多く）
時価総額による判定は株価取得（Phase 2）で置き換える。
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

import requests

from radar.models import USER_AGENT

MASTER_URL = "https://www.jpx.co.jp/markets/statistics-equities/misc/tvdivq0000001vg2-att/data_j.xlsx"
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
MASTER_CSV = DATA_DIR / "master.csv"

SIZE_TIER = {
    "TOPIX Core30": "large",
    "TOPIX Large70": "large",
    "TOPIX Mid400": "mid",
    "TOPIX Small 1": "small",
    "TOPIX Small 2": "micro",
}
EQUITY_MARKETS = ("プライム", "スタンダード", "グロース")


@dataclass(frozen=True)
class Security:
    code: str
    name: str
    market: str
    sector33: str
    size: str  # large / mid / small / micro / other（ETF・REIT等）


def size_tier(market: str, size_label: str) -> str:
    if not market.startswith(EQUITY_MARKETS):
        return "other"
    return SIZE_TIER.get(size_label, "micro")


def download_master(dest: Path = MASTER_CSV) -> int:
    """JPX から一覧を取得し、必要列だけの CSV に変換して保存する。件数を返す。"""
    import openpyxl  # 取得時のみ必要

    dest.parent.mkdir(parents=True, exist_ok=True)
    xlsx = dest.with_suffix(".xlsx")
    resp = requests.get(MASTER_URL, headers={"User-Agent": USER_AGENT}, timeout=60)
    resp.raise_for_status()
    xlsx.write_bytes(resp.content)

    rows = list(openpyxl.load_workbook(xlsx, read_only=True).active.iter_rows(values_only=True))
    header = rows[0]
    idx = {k: header.index(k) for k in ("コード", "銘柄名", "市場・商品区分", "33業種区分", "規模区分")}
    with dest.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["code", "name", "market", "sector33", "size"])
        for r in rows[1:]:
            market = str(r[idx["市場・商品区分"]])
            w.writerow([
                str(r[idx["コード"]]).strip(),
                str(r[idx["銘柄名"]]).strip(),
                market,
                str(r[idx["33業種区分"]]),
                size_tier(market, str(r[idx["規模区分"]])),
            ])
    return len(rows) - 1


def load_master(path: Path = MASTER_CSV) -> dict[str, Security]:
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as f:
        return {r["code"]: Security(**r) for r in csv.DictReader(f)}
