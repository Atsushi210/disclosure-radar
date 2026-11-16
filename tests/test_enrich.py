"""業績修正PDFの読み取りテスト（fixtures は実際の開示PDFから抽出したテキスト）"""
import pytest

from radar.enrich import judge_revision, parse_numbers


def test_parse_numbers_handles_triangle_and_units():
    assert parse_numbers("今回修正予想（Ｂ） 2,377 △517 △513 △1,719 △67.00") == [2377, -517, -513, -1719, -67.0]
    assert parse_numbers("前回発表予想 (A) 百万円 200,000 百万円 8,000 円 銭  22.73") == [200000, 8000, 22.73]
    assert parse_numbers("増減率（%） 2.1% 150.0% 136.8% 107.7% －") == [2.1, 150.0, 136.8, 107.7]


@pytest.mark.parametrize(
    "code, direction, profit_rate",
    [
        ("7256", "up", 12.5),    # A/B 行あり、営業利益 8,000→9,000
        ("458A", "down", None),  # A 行が崩れ、増減額の行で判定
        ("3719", "down", None),  # 赤字転落、増減額の行で判定
        ("266A", "up", None),    # 増減額の行で判定
        ("3933", "up", 0.0),     # 純利益だけ上方修正
        ("217A", "down", None),  # 表が崩れ、本文「前回予想を下回」で判定
    ],
)
def test_judge_revision_real_pdfs(fixture_text, code, direction, profit_rate):
    r = judge_revision(fixture_text(f"revision_{code}.txt"))
    assert r.direction == direction
    if profit_rate is not None:
        assert r.profit_rate == profit_rate


def test_judge_revision_unknown():
    assert judge_revision("お知らせ\n本文のみ").direction == "?"
