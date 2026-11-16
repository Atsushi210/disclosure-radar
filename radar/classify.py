"""
開示タイトルからイベント種別・方向・重要度を判定する（ルールベース、LLMなし）

- 1件の開示から複数のイベントが出ることがある（例: 業績予想と配当予想を同時に修正）
- direction: "up"（株価にプラス方向の材料）/ "down" / "?"（タイトルだけでは不明）
- importance: 3=即プッシュ候補、2=要チェック、1=フィード表示のみ
  重要度は暫定値。Phase 2 のイベントスタディ（過去の株価反応）で置き換える
"""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Event:
    type: str
    label: str
    direction: str
    importance: int


def _re(p: str) -> re.Pattern[str]:
    return re.compile(p)


# 判定対象から外す開示（訂正・説明資料・ETFの日々開示など）
SKIP = _re(
    r"^[（(【]?\s*訂正|訂正に関する|の一部訂正|数値データ訂正|訂正報告書|"
    r"日々の開示事項|決算説明|説明資料|補足資料|説明会|スクリプト|Transcript|"
    r"開示事項の経過|定款の一部変更|役員|人事|組織変更|代表取締役の異動"
)

TOB = _re(r"公開買付|ＴＯＢ|TOB|ＭＢＯ|MBO|マネジメント・バイアウト|完全子会社化|株式交換|株式等売渡請求|株式売渡請求|株式併合")
TOB_TARGET = _re(r"賛同|応募|意見表明|当社株式|当社株券")

BUYBACK = _re(r"自己株式.{0,6}取得(に係る事項の決定|及び|に関する|の決定)|自己株式の公開買付|自社株買い")
BUYBACK_EXCLUDE = _re(r"取得状況|取得結果|取得終了|終了に関する|処分|譲渡制限|消却|取得の結果")

# 「配当予想の上方修正」を業績修正と取り違えないよう、業績の語を必須にする
FORECAST = _re(r"(業績予想|業績見通し|通期予想|業績目標)[^配]{0,20}(修正|上方|下方)")
FORECAST_UP = _re(r"(業績予想|業績見通し|通期予想)[^、及並]{0,12}上方|(業績予想|業績見通し|通期予想)の修正（上方")
FORECAST_DOWN = _re(r"(業績予想|業績見通し|通期予想)[^、及並]{0,12}下方|(業績予想|業績見通し|通期予想)の修正（下方")
FORECAST_GAP =_re(r"(予想|見通し).{0,15}(実績|決算値|決算).{0,10}差異|差異に関するお知らせ")

DIVIDEND = _re(r"配当予想.{0,10}修正|増配|復配|減配|無配|特別配当|記念配当|配当方針|株主還元方針|累進配当|配当性向")
DIV_UP = _re(r"増配|復配|特別配当|記念配当|累進配当|配当予想.{0,4}上方")
DIV_DOWN = _re(r"減配|無配")

SPLIT = _re(r"株式分割")
SPLIT_EXCLUDE = _re(r"株式分割に伴う.{0,10}(定款|調整|変更)")

YUTAI = _re(r"株主優待")
YUTAI_UP = _re(r"新設|導入|拡充|再開")
YUTAI_DOWN = _re(r"廃止|休止|縮小|見送り")

DILUTION = _re(
    r"公募|募集株式|新株式発行|新株式の発行|第三者割当|新株予約権.{0,20}発行|行使価額修正|"
    r"転換社債|ＭＳ|立会外分売|株式の売出し|株式売出し"
)
DILUTION_EXCLUDE = _re(
    r"譲渡制限付株式|株式報酬|ストック・?オプション|払込完了|発行条件|行使状況|"
    r"大量行使|中止|取りやめ|実施の結果|分売の終了|分売実施|引受け|繰上償還|"
    r"転換価額の調整|行使価額の調整|行使完了|発行株式数の確定|買入消却"
)

RISK = _re(
    r"上場廃止|監理銘柄|整理銘柄|特別注意銘柄|継続企業の前提|債務超過|民事再生|破産|"
    r"会社更生|不適切な会計|不正行為|第三者委員会|特別調査委員会|上場維持基準.{0,10}(不適合|適合していない)"
)

ALLIANCE = _re(r"資本業務提携|資本提携")
PARTNER = _re(r"業務提携|共同開発")
ORDER = _re(r"受注(?!速報|状況|高)")
SPECIAL_GAIN = _re(r"特別利益")
SPECIAL_LOSS = _re(r"特別損失|減損損失|貸倒引当金")
EARNINGS = _re(r"決算短信")
MIDTERM = _re(r"中期経営計画")


def classify(title: str) -> list[Event]:
    t = title.strip()
    if SKIP.search(t):
        return []

    ev: list[Event] = []
    add = ev.append

    if BUYBACK.search(t) and not BUYBACK_EXCLUDE.search(t):
        add(Event("buyback", "自社株買い", "up", 3))
    elif TOB.search(t):
        direction = "up" if TOB_TARGET.search(t) else "?"
        add(Event("tob", "TOB・再編", direction, 3))

    if RISK.search(t):
        add(Event("risk", "上場廃止・不正・継続疑義", "down", 3))

    if FORECAST.search(t):
        # 中期経営計画の「上方修正」と混同しないよう、業績予想の近くの上方/下方だけを見る
        direction = "up" if FORECAST_UP.search(t) else "down" if FORECAST_DOWN.search(t) else "?"
        add(Event("forecast_revision", "業績予想の修正", direction, 2))
    elif FORECAST_GAP.search(t):
        add(Event("forecast_gap", "予想と実績の差異", "?", 2))

    if DIVIDEND.search(t):
        direction = "up" if DIV_UP.search(t) else "down" if DIV_DOWN.search(t) else "?"
        add(Event("dividend", "配当の変更", direction, 2))

    if SPLIT.search(t) and not SPLIT_EXCLUDE.search(t):
        add(Event("split", "株式分割", "up", 2))

    if YUTAI.search(t):
        direction = "down" if YUTAI_DOWN.search(t) else "up" if YUTAI_UP.search(t) else "?"
        importance = 1 if direction == "?" else 2
        add(Event("yutai", "株主優待", direction, importance))

    if DILUTION.search(t) and not DILUTION_EXCLUDE.search(t):
        add(Event("dilution", "増資・売出し・希薄化", "down", 2))

    if ALLIANCE.search(t):
        add(Event("alliance", "資本業務提携", "up", 2))
    elif PARTNER.search(t):
        add(Event("partner", "業務提携", "up", 1))

    if ORDER.search(t):
        add(Event("order", "受注", "up", 1))
    if SPECIAL_GAIN.search(t):
        add(Event("special_gain", "特別利益", "up", 1))
    if SPECIAL_LOSS.search(t):
        add(Event("special_loss", "特別損失・減損", "down", 1))
    if MIDTERM.search(t):
        add(Event("midterm_plan", "中期経営計画", "?", 1))
    if EARNINGS.search(t) and not ev:
        add(Event("earnings", "決算短信", "?", 1))

    return ev
