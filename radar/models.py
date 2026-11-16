"""共通データ型と時刻・コードのユーティリティ"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")
USER_AGENT = "Mozilla/5.0 (compatible; disclosure-radar/0.1; personal use)"


def today_jst() -> date:
    """実行環境のタイムゾーンに関係なく日本時間の今日を返す（Actions は UTC のため）。"""
    return datetime.now(JST).date()


def to_code4(code: str) -> str:
    """TDnet の5桁コード（末尾0付き）を4桁表記にする。英字入り（例 130A0）にも対応。"""
    code = code.strip()
    if len(code) == 5 and code.endswith("0"):
        return code[:4]
    return code


@dataclass(frozen=True)
class Disclosure:
    """適時開示1件。doc_id は PDF ファイル名（例 140120261116581125.pdf）で一意。"""

    doc_id: str
    published_at: str  # "YYYY-MM-DD HH:MM"（JST）
    code: str  # 4桁表記
    company_name: str
    title: str
    pdf_url: str
    xbrl_url: str | None
    markets: str
    source: str  # "yanoshin" | "tdnet_release"
