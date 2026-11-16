"""
定期実行の入口（GitHub Actions から15分ごとに呼ぶ）

  python -m radar.live            今日（JST）の新着を処理して通知
  python -m radar.live --dry-run  Firestore・FCM を使わずに結果を表示
  python -m radar.live --test-push  登録済み端末にテスト通知を1通送る

鮮度の安全策: 公開から FRESH_MINUTES を超えた開示は保存するが通知しない。
初回実行や障害明けに、その日の古い開示がまとめて通知されるのを防ぐ。
"""
from __future__ import annotations

import argparse
import logging
import os
from datetime import datetime, timedelta

from radar.master import load_master
from radar.models import JST, today_jst
from radar.notify import plan, send_all
from radar.pipeline import NewEvent, fetch, load_importance, process

log = logging.getLogger(__name__)
FRESH_MINUTES = 90
APP_URL = os.environ.get("RADAR_APP_URL") or "https://disclosure-radar-2026.web.app/"


TENDENCY_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "tendency.json")


def load_tendency() -> dict:
    import json

    try:
        with open(TENDENCY_PATH, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


def to_doc(ne: NewEvent, size: str, tendency: dict | None = None) -> dict:
    x, ev = ne.disclosure, ne.event
    t = (tendency or {})
    hist = t.get(f"{ev.type}|{ev.direction}|{size}") or t.get(f"{ev.type}|{ev.direction}|*")
    if ev.type in ("tob", "risk"):  # 生存バイアスで過去値が当てにならないため添えない
        hist = None
    return {
        "hist": hist,
        "id": f"{x.doc_id}_{ev.type}",
        "doc_id": x.doc_id,
        "day": x.published_at[:10],
        "published_at": x.published_at,
        "code": x.code,
        "company_name": x.company_name,
        "title": x.title,
        "pdf_url": x.pdf_url,
        "size": size,
        "type": ev.type,
        "label": ev.label,
        "direction": ev.direction,
        "importance": ev.importance,
        "sales_rate": ne.sales_rate,
        "profit_rate": ne.profit_rate,
    }


def run(store, now: datetime | None = None, sender=None, enrich: bool = True) -> dict:
    now = now or datetime.now(JST)
    days = [now.date()]
    if now.hour < 9:  # 朝の実行では前日夜の取りこぼしも確認する
        days.insert(0, now.date() - timedelta(days=1))
    master, table, tendency = load_master(), load_importance(), load_tendency()
    all_new: list[dict] = []
    for d in days:
        day = d.isoformat()
        seen = store.seen(day)
        fresh = [x for x in fetch(d) if x.doc_id not in seen]
        docs = []
        for x in fresh:
            size = master[x.code].size if x.code in master else ""
            docs += [to_doc(ne, size, tendency) for ne in process(x, master, table, enrich)]
        store.save_events(docs)
        store.mark_seen(day, seen | {x.doc_id for x in fresh})
        all_new += docs
        log.info("%s: 新規開示%d件 イベント%d件", day, len(fresh), len(docs))

    cutoff = (now - timedelta(minutes=FRESH_MINUTES)).strftime("%Y-%m-%d %H:%M")
    notifiable = [e for e in all_new if e["published_at"] >= cutoff]
    pairs = plan(notifiable, store.devices(), APP_URL)
    dead = send_all(pairs, sender) if sender else send_all(pairs)
    for token in dead:
        store.remove_device(token)
    log.info("通知 %d通（対象イベント%d件、無効端末%d）", len(pairs), len(notifiable), len(dead))
    return {"events": len(all_new), "notifiable": len(notifiable), "sent": len(pairs), "dead": len(dead)}


class MemoryStore:
    """--dry-run 用の何も保存しないストア。"""

    def __init__(self, devices=None):
        self._devices = devices or [{"token": "dry-run", "min_importance": 3}]
        self.saved: list[dict] = []
        self._seen: dict[str, set[str]] = {}

    def seen(self, day):
        return self._seen.get(day, set())

    def mark_seen(self, day, ids):
        self._seen[day] = set(ids)

    def save_events(self, events):
        self.saved += events

    def devices(self):
        return self._devices

    def remove_device(self, token):
        pass


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    logging.getLogger("pypdf").setLevel(logging.ERROR)
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--test-push", action="store_true")
    a = p.parse_args()

    if a.dry_run:
        store = MemoryStore()

        def show(token, msg):
            print(f"[通知] {msg['title']}\n        {msg['body']}")
            return True

        # 鮮度の安全策を外して、今日の全件で何が通知されるかを見る
        global FRESH_MINUTES
        FRESH_MINUTES = 24 * 60
        print(run(store, sender=show))
        return

    from radar.cloud import FirestoreStore

    store = FirestoreStore()
    if a.test_push:
        msg = {"title": "開示レーダー テスト通知", "body": f"{datetime.now(JST):%H:%M} 通知は正常に届いている",
               "url": APP_URL, "tag": "test"}
        dead = send_all([(d["token"], msg) for d in store.devices()])
        print(f"送信 {len(store.devices())}台、無効 {len(dead)}")
        return
    run(store)


if __name__ == "__main__":
    main()
