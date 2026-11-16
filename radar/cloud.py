"""
Firestore 保存層（定期実行用）

コレクション:
  events/{doc_id}_{type}  イベント（PWA のフィードが日付で読む）
  state/{YYYY-MM-DD}      その日に処理済みの開示ID（重複処理と重複通知を防ぐ）
  devices/{token}         通知先の端末（PWA が書く。uid を持つ）
  settings/{uid}          通知条件・ウォッチ銘柄（PWA が書く。全端末で共有）
  members/{uid}           閲覧・端末登録を許すユーザー（管理者が手で作る。rules で参照）

Spark（無料）枠: 書き込み2万/日・読み取り5万/日。1回の実行は state 1読1書＋新規イベント分の書き込み。
"""
from __future__ import annotations

import json
import os
from typing import Any, Protocol


class Store(Protocol):
    def seen(self, day: str) -> set[str]: ...
    def mark_seen(self, day: str, ids: set[str]) -> None: ...
    def save_events(self, events: list[dict[str, Any]]) -> None: ...
    def devices(self) -> list[dict[str, Any]]: ...
    def remove_device(self, token: str) -> None: ...


def init_app():
    """環境変数 FIREBASE_SERVICE_ACCOUNT（JSON文字列）またはファイルパスで初期化する。"""
    import firebase_admin
    from firebase_admin import credentials

    if firebase_admin._apps:
        return firebase_admin.get_app()
    raw = os.environ.get("FIREBASE_SERVICE_ACCOUNT", "")
    if raw.strip().startswith("{"):
        cred = credentials.Certificate(json.loads(raw))
    elif raw:
        cred = credentials.Certificate(raw)
    else:
        raise RuntimeError("FIREBASE_SERVICE_ACCOUNT が未設定")
    return firebase_admin.initialize_app(cred)


class FirestoreStore:
    BATCH = 400

    def __init__(self, client=None):
        if client is None:
            from firebase_admin import firestore

            init_app()
            client = firestore.client()
        self.db = client

    def seen(self, day: str) -> set[str]:
        snap = self.db.collection("state").document(day).get()
        return set(snap.to_dict().get("seen", [])) if snap.exists else set()

    def mark_seen(self, day: str, ids: set[str]) -> None:
        self.db.collection("state").document(day).set({"seen": sorted(ids)})

    def save_events(self, events: list[dict[str, Any]]) -> None:
        col = self.db.collection("events")
        for i in range(0, len(events), self.BATCH):
            batch = self.db.batch()
            for ev in events[i:i + self.BATCH]:
                batch.set(col.document(ev["id"]), ev)
            batch.commit()

    def devices(self) -> list[dict[str, Any]]:
        """端末の一覧に、持ち主（uid）の通知条件 settings/{uid} を合わせて返す。"""
        settings = {s.id: s.to_dict() for s in self.db.collection("settings").stream()}
        out = []
        for d in self.db.collection("devices").stream():
            dev = d.to_dict()
            out.append({**settings.get(dev.get("uid", ""), {}), **dev, "token": d.id})
        return out

    def remove_device(self, token: str) -> None:
        self.db.collection("devices").document(token).delete()
