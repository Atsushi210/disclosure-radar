"""収集パイプラインのテスト（取得とPDFは差し替え）"""
from datetime import date

from radar import db, pipeline
from radar.enrich import Revision
from radar.models import Disclosure


def make(doc_id, title, code="1234"):
    return Disclosure(doc_id, "2026-11-13 15:00", code, "テスト社", title,
                      f"https://www.release.tdnet.info/inbs/{doc_id}", None, "東", "yanoshin")


def test_collect_saves_once_and_enriches(temp_db, monkeypatch):
    items = [
        make("a.pdf", "自己株式取得に係る事項の決定に関するお知らせ"),
        make("b.pdf", "通期連結業績予想の修正に関するお知らせ"),
        make("c.pdf", "役員の異動に関するお知らせ"),
    ]
    monkeypatch.setattr(pipeline, "fetch", lambda d: items)
    monkeypatch.setattr(pipeline, "fetch_pdf_text", lambda url: "")
    monkeypatch.setattr(pipeline, "judge_revision", lambda text: Revision("up", 5.0, 45.0))
    monkeypatch.setattr(pipeline, "PDF_SLEEP_SEC", 0)

    first = pipeline.collect(date(2026, 11, 13))
    assert {(e.event.type, e.event.direction, e.event.importance) for e in first} == {
        ("buyback", "up", 3),
        ("forecast_revision", "up", 3),  # 営業利益+45% で重要度3に昇格
    }

    # 2回目は新規なし（既存の開示は再処理しない）
    assert pipeline.collect(date(2026, 11, 13)) == []

    with db.get_conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM disclosures").fetchone()[0] == 3
        assert conn.execute("SELECT profit_rate FROM events WHERE type='forecast_revision'").fetchone()[0] == 45.0


def test_fetch_falls_back(monkeypatch):
    def boom(d):
        raise RuntimeError("down")

    monkeypatch.setattr(pipeline.yanoshin, "fetch_day", boom)
    monkeypatch.setattr(pipeline.tdnet_release, "fetch_day", lambda d: [make("x.pdf", "t")])
    assert [x.doc_id for x in pipeline.fetch(date(2026, 11, 13))] == ["x.pdf"]
