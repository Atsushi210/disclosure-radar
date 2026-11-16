"""
TDnet 適時開示情報閲覧サービスの当日一覧ページから取得する（予備経路）

money-flow-radar の pipeline/collect/tdnet.py を移植し、次を改修した:
- XBRL 列（kjXbrl）と上場取引所列（kjPlace）を取得
- 日付は日本時間で決める（Actions は UTC で動くため）
- ページ上限を引き上げ（決算期は1日2,000件超）
閲覧ページは直近約1か月分しか残らないため、過去分の取得には使わない。
"""
from __future__ import annotations

import re
import time
from datetime import date

import requests

from radar.models import USER_AGENT, Disclosure, today_jst, to_code4

BASE = "https://www.release.tdnet.info/inbs/"
LIST_URL = BASE + "I_list_{page:03d}_{ymd}.html"
MAX_PAGES = 40
SLEEP_SEC = 1.0

ROW_RE = re.compile(
    r'kjTime"\s*noWrap>(?P<time>[\d:]+)</td>\s*'
    r'<td[^>]*kjCode"\s*noWrap>(?P<code>[0-9A-Z]+)</td>\s*'
    r'<td[^>]*kjName"\s*noWrap>(?P<name>[^<]*)</td>\s*'
    r'<td[^>]*kjTitle"[^>]*><a href="(?P<pdf>[^"]+)"[^>]*>(?P<title>[^<]*)</a>\s*</td>\s*'
    r'<td[^>]*kjXbrl"[^>]*>(?P<xbrl>.*?)</td>\s*'
    r'<td[^>]*kjPlace"[^>]*>(?P<place>[^<]*)</td>',
    re.S,
)
XBRL_HREF_RE = re.compile(r'href="([^"]+\.zip)"')


def fetch_day_pages(target: date | None = None, session: requests.Session | None = None) -> list[str]:
    """指定日（既定は日本時間の今日）の全ページのHTMLを取得する。"""
    s = session or requests.Session()
    ymd = (target or today_jst()).strftime("%Y%m%d")
    pages: list[str] = []
    for page in range(1, MAX_PAGES + 1):
        resp = s.get(LIST_URL.format(page=page, ymd=ymd), headers={"User-Agent": USER_AGENT}, timeout=20)
        if resp.status_code != 200:
            break
        resp.encoding = resp.apparent_encoding or "utf-8"
        if "main-list-table" not in resp.text:
            break
        pages.append(resp.text)
        time.sleep(SLEEP_SEC)
    return pages


def parse_rows(html: str, target: date) -> list[Disclosure]:
    """1ページ分のHTMLから開示を抽出する。"""
    out: list[Disclosure] = []
    for m in ROW_RE.finditer(html):
        pdf = m.group("pdf").strip()
        xm = XBRL_HREF_RE.search(m.group("xbrl"))
        out.append(
            Disclosure(
                doc_id=pdf.rsplit("/", 1)[-1],
                published_at=f"{target.isoformat()} {m.group('time').strip()}",
                code=to_code4(m.group("code")),
                company_name=m.group("name").strip(),
                title=m.group("title").strip(),
                pdf_url=BASE + pdf,
                xbrl_url=(BASE + xm.group(1)) if xm else None,
                markets=m.group("place").strip(),
                source="tdnet_release",
            )
        )
    return out


def fetch_day(target: date | None = None) -> list[Disclosure]:
    d = target or today_jst()
    return [row for html in fetch_day_pages(d) for row in parse_rows(html, d)]
