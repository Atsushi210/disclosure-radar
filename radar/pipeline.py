"""
収集 → 分類 → 補完（PDFで修正方向） → 保存 の一連の処理
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, replace
from datetime import date, datetime

from radar import db
from radar.classify import Event, classify
from radar.enrich import fetch_pdf_text, judge_revision
from radar.models import JST, Disclosure, today_jst
from radar.sources import tdnet_release, yanoshin

log = logging.getLogger(__name__)
REVISION_TYPES = {"forecast_revision", "forecast_gap"}
PDF_SLEEP_SEC = 0.5


@dataclass(frozen=True)
class NewEvent:
    disclosure: Disclosure
    event: Event
    sales_rate: float | None = None
    profit_rate: float | None = None


def fetch(target: date) -> list[Disclosure]:
    """非公式APIで取得し、失敗したら TDnet 閲覧ページに切り替える。"""
    try:
        return yanoshin.fetch_day(target)
    except Exception as exc:  # noqa: BLE001 — 取得元の障害は予備経路で吸収する
        log.warning("yanoshin 取得失敗のため TDnet 閲覧ページに切替: %s", exc)
        return tdnet_release.fetch_day(target)


def enrich_revision(d: Disclosure, ev: Event) -> NewEvent:
    """方向が不明な業績修正・差異はPDFの表から判定する。"""
    if ev.type not in REVISION_TYPES or not d.pdf_url:
        return NewEvent(d, ev)
    try:
        rev = judge_revision(fetch_pdf_text(d.pdf_url))
    except Exception as exc:  # noqa: BLE001
        log.warning("PDF読取失敗 %s %s: %s", d.code, d.doc_id, exc)
        return NewEvent(d, ev)
    finally:
        time.sleep(PDF_SLEEP_SEC)
    direction = ev.direction if ev.direction != "?" else rev.direction
    # 上方修正の幅が大きいものは重要度を上げる（暫定: 営業利益+30%以上）
    importance = ev.importance
    if direction == "up" and (rev.profit_rate or 0) >= 30:
        importance = 3
    if direction == "down" and (rev.profit_rate or 0) <= -30:
        importance = 3
    return NewEvent(d, replace(ev, direction=direction, importance=importance), rev.sales_rate, rev.profit_rate)


def collect(target: date | None = None, enrich: bool = True) -> list[NewEvent]:
    """指定日の開示を取り込み、新規分のイベントを返す。既存の開示は再処理しない。"""
    d = target or today_jst()
    disclosures = fetch(d)
    now = datetime.now(JST).isoformat(timespec="seconds")
    new_events: list[NewEvent] = []

    with db.get_conn() as conn:
        known = {r[0] for r in conn.execute(
            "SELECT doc_id FROM disclosures WHERE published_at LIKE ?", (f"{d.isoformat()}%",)
        )}
        fresh = [x for x in disclosures if x.doc_id not in known]
        for x in fresh:
            conn.execute(
                "INSERT OR IGNORE INTO disclosures VALUES (?,?,?,?,?,?,?,?,?,?)",
                (x.doc_id, x.published_at, x.code, x.company_name, x.title,
                 x.pdf_url, x.xbrl_url, x.markets, x.source, now),
            )
        conn.commit()

        for x in fresh:
            for ev in classify(x.title):
                ne = enrich_revision(x, ev) if enrich and ev.direction == "?" else NewEvent(x, ev)
                conn.execute(
                    "INSERT OR IGNORE INTO events (doc_id,type,label,direction,importance,sales_rate,profit_rate)"
                    " VALUES (?,?,?,?,?,?,?)",
                    (x.doc_id, ne.event.type, ne.event.label, ne.event.direction,
                     ne.event.importance, ne.sales_rate, ne.profit_rate),
                )
                new_events.append(ne)
            conn.commit()

    log.info("%s: 取得%d件 新規%d件 イベント%d件", d, len(disclosures), len(fresh), len(new_events))
    return new_events
