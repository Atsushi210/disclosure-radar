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


IMPORTANCE_PATH = db.DB_PATH.parent / "importance.json"


def load_importance() -> dict[str, int]:
    """イベントスタディで決めた重要度（type|direction|size → 1〜3）。無ければ空。"""
    import json

    return json.loads(IMPORTANCE_PATH.read_text(encoding="utf-8")) if IMPORTANCE_PATH.exists() else {}


def apply_importance(ne: NewEvent, size: str, table: dict[str, int]) -> NewEvent:
    """過去の株価反応に基づく重要度で置き換える。業績修正の大幅修正（±30%）による昇格は残す。"""
    ev = ne.event
    base = table.get(f"{ev.type}|{ev.direction}|{size}", table.get(f"{ev.type}|{ev.direction}|*"))
    if base is None:
        return ne
    big_revision = ev.type in REVISION_TYPES and abs(ne.profit_rate or 0) >= 30
    return replace(ne, event=replace(ev, importance=max(base, 3 if big_revision else 1)))


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

        from radar.master import load_master

        master, table = load_master(), load_importance()
        for x in fresh:
            size = master[x.code].size if x.code in master else ""
            for ev in classify(x.title):
                ne = enrich_revision(x, ev) if enrich and ev.direction == "?" else NewEvent(x, ev)
                ne = apply_importance(ne, size, table)
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


def backfill(start: date, end: date, sleep_sec: float = 2.0, retries: int = 3) -> None:
    """過去の開示を1日ずつ取り込む（PDF補完なし）。取り込み済みの日は飛ばすので中断しても再開できる。

    過去分のPDFは TDnet 上から消えているため、業績修正の方向はタイトルでしか判定できない。
    """
    from datetime import timedelta

    d = start
    while d <= end:
        if d.weekday() < 5:
            with db.get_conn() as conn:
                done = conn.execute(
                    "SELECT 1 FROM disclosures WHERE published_at LIKE ? LIMIT 1", (f"{d.isoformat()}%",)
                ).fetchone()
            if not done:
                for attempt in range(retries):
                    try:
                        disclosures = yanoshin.fetch_day(d)
                        _store(disclosures)
                        log.info("%s: %d件", d, len(disclosures))
                        break
                    except Exception as exc:  # noqa: BLE001
                        log.warning("%s 取得失敗（%d回目）: %s", d, attempt + 1, exc)
                        time.sleep(sleep_sec * 5 * (attempt + 1))
                time.sleep(sleep_sec)
        d += timedelta(days=1)


def _store(disclosures: list[Disclosure]) -> None:
    """開示とタイトル分類のイベントを保存する（補完なし）。"""
    now = datetime.now(JST).isoformat(timespec="seconds")
    with db.get_conn() as conn:
        for x in disclosures:
            cur = conn.execute(
                "INSERT OR IGNORE INTO disclosures VALUES (?,?,?,?,?,?,?,?,?,?)",
                (x.doc_id, x.published_at, x.code, x.company_name, x.title,
                 x.pdf_url, x.xbrl_url, x.markets, x.source, now),
            )
            if cur.rowcount == 0:
                continue
            for ev in classify(x.title):
                conn.execute(
                    "INSERT OR IGNORE INTO events (doc_id,type,label,direction,importance) VALUES (?,?,?,?,?)",
                    (x.doc_id, ev.type, ev.label, ev.direction, ev.importance),
                )
