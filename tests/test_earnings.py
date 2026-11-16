"""決算短信サマリー（XBRL）の読み取りと判定のテスト"""
from pathlib import Path

import pytest

from radar import pipeline
from radar.earnings import judge
from radar.models import Disclosure
from radar.xbrl import Summary, parse_summary

FIX = Path(__file__).parent / "fixtures"


def load(name):
    return parse_summary((FIX / name).read_text(encoding="utf-8"))


def test_parse_q2_consolidated():
    s = load("summary_q2_jgaap.htm")
    assert (s.quarter, s.consolidated) == (2, True)
    assert (s.sales, s.op, s.op_prior, s.fy_op) == (11226, 521, 178, 700)
    assert (s.div, s.div_prior) == (17, 17)
    assert s.forecast_corrected is False


def test_parse_fy_nonconsolidated_with_losses():
    s = load("summary_fy_nonconsol.htm")
    assert (s.quarter, s.consolidated) == (None, False)
    assert (s.op, s.op_prior) == (-1119, -774)  # sign="-" を負数にする
    assert s.fy_op is None  # 来期予想を出していない


def test_parse_ifrs_suffix():
    s = load("summary_fy_ifrs.htm")
    assert (s.sales, s.op, s.op_prior) == (29447, 3953, 3815)
    assert s.fy_op == 4200


def test_self_closing_nil_does_not_shift_values():
    from radar.xbrl import parse_facts

    h = ('<ix:nonFraction name="tse-ed-t:OperatingIncome" contextRef="A" xsi:nil="true"/>'
         '<ix:nonFraction name="tse-ed-t:ChangeInOperatingIncome" contextRef="B" scale="-2">22.4</ix:nonFraction>'
         '<ix:nonFraction name="tse-ed-t:NetSales" contextRef="C" scale="6">1,234&#160;</ix:nonFraction>')
    nums, _ = parse_facts(h)
    assert nums == {("ChangeInOperatingIncome", "B"): 22.4, ("NetSales", "C"): 1234.0}


def test_judge_strong_quarter():
    e = judge(load("summary_q2_jgaap.htm"))
    assert e.direction == "up" and e.importance == 3
    assert e.goods == ["営業益+193%", "進捗74%（標準50%）"]
    assert e.text == "営業益+193% 進捗74%（標準50%）"


def test_judge_loss_widening_is_neutral():
    e = judge(load("summary_fy_nonconsol.htm"))
    assert (e.direction, e.importance, e.goods, e.bads) == ("?", 1, [], [])


@pytest.mark.parametrize("kw, direction, goods, bads", [
    (dict(op=50, op_prior=-10), "up", ["黒字転換"], []),
    (dict(op=-5, op_prior=40), "down", [], ["赤字転落"]),
    (dict(quarter=3, op=40, op_prior=50, fy_op=100), "down", [], ["進捗40%（標準75%）"]),  # 減益20%は弱いので材料にしない
    (dict(quarter=None, op=100, op_prior=90, fy_op=70), "down", [], ["来期予想-30%"]),
    (dict(quarter=None, op=-10, op_prior=-20, fy_op=5), "up", ["来期黒字予想"], []),
    (dict(div=30, div_prior=20), "?", [], []),  # 増配は表示のみ（判定に数えない）
    (dict(div=10, div_prior=20), "down", [], ["減配20→10円"]),
    (dict(div=19.5, div_prior=20), "?", [], []),  # 5%未満の変化は無視
])
def test_judge_rules(kw, direction, goods, bads):
    base = dict(quarter=2, consolidated=True, op=60, op_prior=55, fy_op=120)
    e = judge(Summary(**{**base, **kw}))
    assert (e.direction, e.goods, e.bads) == (direction, goods, bads)


def test_process_uses_xbrl_for_earnings(monkeypatch):
    html = (FIX / "summary_q2_jgaap.htm").read_text(encoding="utf-8")
    monkeypatch.setattr("radar.xbrl.fetch_summary_html", lambda url: html)
    monkeypatch.setattr(pipeline, "PDF_SLEEP_SEC", 0)
    x = Disclosure("e.pdf", "2026-11-16 13:00", "7953", "菊水化学工業", "2027年3月期 第2四半期決算短信〔日本基準〕（連結）",
                   "https://example/e.pdf", "https://example/e.zip", "東", "yanoshin")
    [ne] = pipeline.process(x, {}, {"earnings|?|*": 2})
    assert (ne.event.direction, ne.event.importance, ne.profit_rate) == ("up", 3, pytest.approx(192.7, 0.1))
    assert ne.earn["text"].startswith("営業益+193%")
