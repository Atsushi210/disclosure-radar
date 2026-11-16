"""定期実行と通知の組み立てのテスト（Firestore・FCM は差し替え）"""
from datetime import datetime

from radar import live, notify
from radar.models import JST, Disclosure


def disc(doc_id, title, code, hhmm):
    return Disclosure(doc_id, f"2026-11-16 {hhmm}", code, f"社{code}", title,
                      f"https://example/{doc_id}", None, "東", "yanoshin")


def ev(code="1111", importance=3, size="micro", type_="buyback", label="自社株買い", hhmm="15:30"):
    return {"id": f"{code}{type_}", "day": "2026-11-16", "published_at": f"2026-11-16 {hhmm}", "code": code,
            "company_name": f"社{code}", "title": "t", "size": size, "type": type_, "label": label,
            "direction": "up", "importance": importance, "profit_rate": None}


def test_matches_watchlist_overrides_size_and_importance():
    dev = {"token": "t", "watchlist": ["7203"]}
    assert notify.matches(ev(code="7203", importance=1, size="large"), dev)
    assert not notify.matches(ev(importance=2), dev)
    assert notify.matches(ev(importance=3, size="small"), dev)
    assert not notify.matches(ev(importance=3, size="large"), dev)
    assert not notify.matches(ev(), {**dev, "enabled": False})


def test_plan_digests_when_many():
    evs = [ev(code=str(1000 + i)) for i in range(7)] + [ev(code="7203", importance=1, size="large")]
    pairs = notify.plan(evs, [{"token": "t", "watchlist": ["7203"]}], "https://app/")
    titles = [m["title"] for _, m in pairs]
    assert titles[0].startswith("【保有】7203")
    assert titles[1] == "開示レーダー 新着7件"
    assert len(pairs) == 2


def test_run_dedupes_and_only_notifies_fresh(monkeypatch):
    items = [
        disc("a.pdf", "自己株式取得に係る事項の決定に関するお知らせ", "1111", "15:30"),
        disc("b.pdf", "自己株式取得に係る事項の決定に関するお知らせ", "2222", "10:00"),  # 古い → 通知しない
        disc("c.pdf", "役員の異動に関するお知らせ", "3333", "15:30"),
    ]
    monkeypatch.setattr(live, "fetch", lambda d: items)
    monkeypatch.setattr(live, "load_importance", lambda: {})
    monkeypatch.setattr(live, "load_master", lambda: {})
    sent = []
    store = live.MemoryStore(devices=[{"token": "t", "sizes": []}])
    now = datetime(2026, 11, 16, 16, 0, tzinfo=JST)

    r1 = live.run(store, now=now, sender=lambda t, m: sent.append(m) or True, enrich=False)
    assert r1 == {"events": 2, "notifiable": 1, "sent": 1, "dead": 0}
    assert "1111" in sent[0]["title"]
    assert store.seen("2026-11-16") == {"a.pdf", "b.pdf", "c.pdf"}

    r2 = live.run(store, now=now, sender=lambda t, m: sent.append(m) or True, enrich=False)
    assert r2["events"] == 0 and len(sent) == 1


def test_dead_tokens_removed(monkeypatch):
    monkeypatch.setattr(live, "fetch", lambda d: [disc("a.pdf", "自己株式取得に係る事項の決定に関するお知らせ", "1111", "15:30")])
    monkeypatch.setattr(live, "load_importance", lambda: {})
    monkeypatch.setattr(live, "load_master", lambda: {})
    removed = []
    store = live.MemoryStore(devices=[{"token": "gone", "sizes": []}])
    store.remove_device = removed.append
    live.run(store, now=datetime(2026, 11, 16, 16, 0, tzinfo=JST), sender=lambda t, m: False, enrich=False)
    assert removed == ["gone"]
