"""
TDnet 適時開示一覧の取得（非公式 API: webapi.yanoshin.jp）

- 認証不要。2016年以降の日付指定に対応（2026-11-16 実地確認）
- 1日の件数は決算期に2,000件を超える。by_id によるページングは機能しなかった（2026-11-16確認）ため、
  1日単位で limit を大きく取って1回で取得する。上限に達したら警告する
- 応答は {"items": [{"Tdnet": {...}}]} と {"items": [{...}]} の2形式があるので両方を受ける
- 非公式サービスのため、落ちた時は tdnet_release（TDnet 閲覧ページ）に切り替える
"""
from __future__ import annotations

import time
from datetime import date

import requests

from radar.models import USER_AGENT, Disclosure, to_code4

BASE = "https://webapi.yanoshin.jp/webapi/tdnet/list/{key}.json2"
REDIRECT_PREFIX = "https://webapi.yanoshin.jp/rd.php?"
DAY_LIMIT = 5000
SLEEP_SEC = 1.0


class LimitReached(RuntimeError):
    """1回の取得が上限件数に達した（取りこぼしの可能性）。"""


def _unwrap_url(url: str | None) -> str | None:
    """rd.php 経由のリダイレクトURLから TDnet の実URLを取り出す。"""
    if not url:
        return None
    return url[len(REDIRECT_PREFIX):] if url.startswith(REDIRECT_PREFIX) else url


def parse_items(payload: dict) -> list[dict]:
    """応答JSONから開示レコードのリストを取り出す（ラッパーの有無を吸収）。"""
    return [item.get("Tdnet", item) for item in payload.get("items", [])]


def to_disclosure(rec: dict) -> Disclosure:
    pdf_url = _unwrap_url(rec.get("document_url")) or ""
    return Disclosure(
        doc_id=pdf_url.rsplit("/", 1)[-1] or f"yanoshin-{rec['id']}",
        published_at=str(rec["pubdate"])[:16],
        code=to_code4(str(rec["company_code"])),
        company_name=str(rec["company_name"]).strip(),
        title=str(rec["title"]).strip(),
        pdf_url=pdf_url,
        xbrl_url=_unwrap_url(rec.get("url_xbrl")),
        markets=str(rec.get("markets_string") or ""),
        source="yanoshin",
    )


def fetch_records(key: str, since_id: int | None = None, session: requests.Session | None = None) -> list[dict]:
    """key（today / YYYYmmdd / 銘柄コード）の開示を新しい順に1回で取得する。"""
    s = session or requests.Session()
    params: dict[str, int] = {"limit": DAY_LIMIT}
    if since_id is not None:
        params["since_id"] = since_id
    resp = s.get(BASE.format(key=key), params=params, headers={"User-Agent": USER_AGENT}, timeout=60)
    resp.raise_for_status()
    recs = parse_items(resp.json())
    if len(recs) >= DAY_LIMIT:
        raise LimitReached(f"{key}: {len(recs)}件で上限に達した")
    return recs


def fetch_day(target: date, since_id: int | None = None) -> list[Disclosure]:
    """指定日の開示を返す。since_id を渡すとそれより新しいものだけ。"""
    return [to_disclosure(r) for r in fetch_records(target.strftime("%Y%m%d"), since_id=since_id)]


def fetch_days(start: date, end: date) -> list[Disclosure]:
    """期間の開示を1日ずつ取得する（過去データの取り込み用）。"""
    from datetime import timedelta

    out: list[Disclosure] = []
    d = start
    while d <= end:
        if d.weekday() < 5:
            out.extend(fetch_day(d))
            time.sleep(SLEEP_SEC)
        d += timedelta(days=1)
    return out
