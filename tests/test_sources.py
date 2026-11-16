"""取得元のパーサのテスト"""
from datetime import date

import pytest
import responses

from radar.sources import tdnet_release, yanoshin

REC = {
    "id": "1296338",
    "pubdate": "2026-11-13 21:00:00",
    "company_code": "37190",
    "company_name": "ＡＩストーム",
    "title": "業績予想の修正に関するお知らせ",
    "document_url": "https://webapi.yanoshin.jp/rd.php?https://www.release.tdnet.info/inbs/140120261112577422.pdf",
    "url_xbrl": None,
    "markets_string": "東",
}


@pytest.mark.parametrize("payload", [{"items": [{"Tdnet": REC}]}, {"items": [REC]}])
def test_yanoshin_parse_both_shapes(payload):
    d = yanoshin.to_disclosure(yanoshin.parse_items(payload)[0])
    assert d.doc_id == "140120261112577422.pdf"
    assert d.pdf_url == "https://www.release.tdnet.info/inbs/140120261112577422.pdf"
    assert d.code == "3719"
    assert d.published_at == "2026-11-13 21:00"


def test_alnum_code():
    assert yanoshin.to_disclosure({**REC, "company_code": "130A0"}).code == "130A"


@responses.activate
def test_yanoshin_limit_reached(monkeypatch):
    monkeypatch.setattr(yanoshin, "DAY_LIMIT", 2)
    responses.get(yanoshin.BASE.format(key="20261113"), json={"items": [REC, REC]})
    with pytest.raises(yanoshin.LimitReached):
        yanoshin.fetch_day(date(2026, 11, 13))


def test_tdnet_release_parse(fixture_text):
    rows = tdnet_release.parse_rows(fixture_text("tdnet_sample.html"), date(2026, 9, 11))
    assert len(rows) == 6
    assert rows[0].code == "3350"
    assert rows[0].published_at == "2026-09-11 20:45"
    assert rows[0].pdf_url.endswith("140120260911535177.pdf")
    assert rows[0].markets == "東"
    assert rows[0].xbrl_url is None
