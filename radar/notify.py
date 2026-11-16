"""
プッシュ通知（FCM Web Push）

端末ごとの条件（PWA の設定画面で保存）:
  min_importance      通常の銘柄で通知する最低重要度（既定3）
  sizes               通知する規模（既定 small, micro）
  types               通知する種別（空なら全種別）
  watchlist           保有・ウォッチ銘柄のコード（規模・種別に関係なく通知）
  watch_min_importance ウォッチ銘柄で通知する最低重要度（既定1）
  enabled             false なら送らない

1回の実行で1端末あたり MAX_SINGLE 件を超えたら、1通のまとめ通知にする（決算集中日対策）。
"""
from __future__ import annotations

import logging
from collections import Counter
from typing import Any, Callable

log = logging.getLogger(__name__)

MAX_SINGLE = 5
ARROW = {"up": "↑", "down": "↓", "?": ""}
SIZE_JA = {"large": "大型", "mid": "中型", "small": "小型", "micro": "超小型"}
DEFAULTS = {"min_importance": 3, "sizes": ["small", "micro"], "types": [], "watchlist": [],
            "watch_min_importance": 1, "enabled": True}

Message = dict[str, str]  # title, body, url, tag


def matches(ev: dict[str, Any], device: dict[str, Any]) -> bool:
    d = {**DEFAULTS, **device}
    if not d["enabled"]:
        return False
    if ev["code"] in set(d["watchlist"]):
        return ev["importance"] >= d["watch_min_importance"]
    if ev["importance"] < d["min_importance"]:
        return False
    if d["sizes"] and ev.get("size") not in d["sizes"]:
        return False
    return not d["types"] or ev["type"] in d["types"]


def single_message(ev: dict[str, Any], app_url: str, watched: bool) -> Message:
    rate = f" 営業益{ev['profit_rate']:+.0f}%" if ev.get("profit_rate") is not None else ""
    mark = "【保有】" if watched else ""
    h = ev.get("hist")
    hist = f"\n過去{h['n']}件: 初動{h['car0']:+.1f}% → 20日{h['d20']:+.1f}%" if h else ""
    earn = ev.get("earn") or {}
    if earn.get("text"):  # 決算短信は数値の要約を添え、率は要約に含むので見出しには付けない
        hist = f"\n{earn['text']}" + hist
        rate = ""
    return {
        "title": f"{mark}{ev['code']} {ev['company_name']} {ARROW.get(ev['direction'], '')}{ev['label']}{rate}",
        "body": f"{ev['published_at'][11:]} {SIZE_JA.get(ev.get('size', ''), '')} {ev['title'][:70]}{hist}",
        "url": f"{app_url}?day={ev['day']}&event={ev['id']}",
        "tag": ev["id"],
    }


def digest_message(evs: list[dict[str, Any]], app_url: str) -> Message:
    counts = Counter(e["label"] for e in evs)
    top = "、".join(f"{k}{v}" for k, v in counts.most_common(4))
    names = "・".join(e["company_name"][:8] for e in evs[:3])
    return {
        "title": f"開示レーダー 新着{len(evs)}件",
        "body": f"{top}（{names} ほか）",
        "url": f"{app_url}?day={evs[0]['day']}",
        "tag": f"digest-{evs[0]['day']}-{evs[0]['published_at'][11:]}",
    }


def plan(events: list[dict[str, Any]], devices: list[dict[str, Any]], app_url: str) -> list[tuple[str, Message]]:
    """端末ごとに送るメッセージを決める。（token, message）のリストを返す。"""
    out: list[tuple[str, Message]] = []
    for dev in devices:
        hits = [e for e in events if matches(e, dev)]
        if not hits:
            continue
        watch = set(dev.get("watchlist", []))
        hits.sort(key=lambda e: (e["code"] not in watch, -e["importance"], e["published_at"]))
        watched = [e for e in hits if e["code"] in watch]
        others = [e for e in hits if e["code"] not in watch]
        # 保有銘柄は必ず個別に送る
        out += [(dev["token"], single_message(e, app_url, True)) for e in watched]
        if len(others) > MAX_SINGLE:
            out.append((dev["token"], digest_message(others, app_url)))
        else:
            out += [(dev["token"], single_message(e, app_url, False)) for e in others]
    return out


def fcm_sender(token: str, msg: Message) -> bool:
    """FCM で1通送る。トークンが無効なら False を返す（端末の登録を消す合図）。"""
    from firebase_admin import messaging

    message = messaging.Message(
        token=token,
        webpush=messaging.WebpushConfig(
            notification=messaging.WebpushNotification(title=msg["title"], body=msg["body"], tag=msg["tag"],
                                                       icon="/icons/icon-192.png"),
            fcm_options=messaging.WebpushFCMOptions(link=msg["url"]),
        ),
        data={"url": msg["url"]},
    )
    try:
        messaging.send(message)
        return True
    except (messaging.UnregisteredError, messaging.SenderIdMismatchError):
        return False


def send_all(pairs: list[tuple[str, Message]], sender: Callable[[str, Message], bool] = fcm_sender) -> set[str]:
    """送信し、無効だったトークンの集合を返す。1通の失敗で全体を止めない。"""
    dead: set[str] = set()
    for token, msg in pairs:
        try:
            if not sender(token, msg):
                dead.add(token)
        except Exception as exc:  # noqa: BLE001
            log.warning("通知失敗 %s…: %s", token[:12], exc)
    return dead
