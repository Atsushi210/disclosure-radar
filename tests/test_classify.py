"""イベント分類のテスト（タイトルは2026年10〜11月の実際の開示から採取）"""
import pytest

from radar.classify import classify


def types(title):
    return {(e.type, e.direction) for e in classify(title)}


@pytest.mark.parametrize(
    "title, expected",
    [
        ("自己株式取得に係る事項の決定に関するお知らせ", {("buyback", "up")}),
        ("自己株式の取得及び自己株式立会外買付取引（ToSTNeT-3）による自己株式の買付けに関するお知らせ", {("buyback", "up")}),
        ("配当予想の修正及び自己株式取得に係る事項の決定に関するお知らせ", {("buyback", "up"), ("dividend", "?")}),
        ("ＭＢＯの実施及び応募の推奨に関するお知らせ", {("tob", "up")}),
        ("株式会社岐阜造園（証券コード：1438）に対する公開買付けの開始に関するお知らせ", {("tob", "?")}),
        ("通期業績予想の上方修正に関するお知らせ", {("forecast_revision", "up")}),
        ("通期連結業績予想の修正（上方）および配当予想の修正（増配）に関するお知らせ",
         {("forecast_revision", "up"), ("dividend", "up")}),
        ("2026年12月期の通期連結業績予想の修正に関するお知らせ", {("forecast_revision", "?")}),
        ("2026年9月期業績予想と実績の差異に関するお知らせ", {("forecast_gap", "?")}),
        ("剰余金の配当（中間配当）の決定及び2027年３月期配当予想の上方修正に関するお知らせ", {("dividend", "up")}),
        ("剰余金の配当（無配）に関するお知らせ", {("dividend", "down")}),
        ("株主優待制度の新設に関するお知らせ", {("yutai", "up")}),
        ("株主優待実施の見送りに関するお知らせ", {("yutai", "down")}),
        ("株式の立会外分売に関するお知らせ", {("dilution", "down")}),
        ("連結子会社の破産手続開始の申立てに関するお知らせ", {("risk", "down")}),
        ("特別損失の計上に関するお知らせ", {("special_loss", "down")}),
        ("2027年３月期第２四半期（中間期）決算短信〔日本基準〕(連結)", {("earnings", "?")}),
        ("大口受注に関するお知らせ", {("order", "up")}),
    ],
)
def test_classify_real_titles(title, expected):
    assert types(title) == expected


@pytest.mark.parametrize(
    "title",
    [
        "（訂正）「2026年９月期中間決算短信〔日本基準〕(連結)」の一部訂正について",
        "アジア国債・公債ＥＴＦ（正式名称：ＡＢＦ汎アジア債券インデックス・ファンド）に関する日々の開示事項",
        "2027年3月期第2四半期決算説明資料",
        "譲渡制限付株式報酬としての自己株式処分の払込完了に関するお知らせ",
        "自己株式の取得状況に関するお知らせ",
        "第１回無担保転換社債型新株予約権付社債の一部繰上償還について",
        "2026年10月度 受注速報",
        "資金の借入（借換）に関するお知らせ",
        "前年実績値と当期実績値との差異に関するお知らせ",
        "前期実績との差異に関するお知らせ",
    ],
)
def test_not_events(title):
    assert classify(title) == []


def test_midterm_upward_is_not_forecast_up():
    """中期経営計画の上方修正を業績予想の上方修正と取り違えない"""
    t = "2027年３月期通期業績予想の修正および中期経営計画「NIRECO-VISION 100」の数値目標変更（上方修正）ならびに期末配当予想の修正に関するお知らせ"
    assert ("forecast_revision", "?") in types(t)
