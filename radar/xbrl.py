"""
決算短信サマリー（TDnet の XBRL zip 内 Summary/*-ixbrl.htm）の数値を読む

- 文脈（contextRef）は「期間_連結区分_実績/予想」の形。例:
    CurrentAccumulatedQ2Duration_ConsolidatedMember_ResultMember  当期累計の実績
    PriorAccumulatedQ2Duration_ConsolidatedMember_ResultMember    前年同期の実績
    CurrentYearDuration_ConsolidatedMember_ForecastMember         通期予想（四半期決算）
    CurrentYearDuration_ConsolidatedMember_ResultMember           通期実績（本決算）
    NextYearDuration_ConsolidatedMember_ForecastMember            来期予想（本決算）
- 会計基準で要素名が変わる（IFRS は末尾 IFRS、米国基準は US）。末尾を外して同一視する
- TDnet 上の zip は約1か月で消えるため、過去分は読めない
"""
from __future__ import annotations

import html
import io
import re
import zipfile
from dataclasses import dataclass, field

import requests

from radar.models import USER_AGENT

# 指標ごとの要素名の候補（先にあるものを優先）
SALES = ("NetSales", "Sales", "Revenue", "Revenues", "OperatingRevenues", "OperatingRevenue",
         "OrdinaryRevenues", "NetSalesOfCompletedConstructionContracts", "GrossOperatingRevenues")
OPERATING = ("OperatingIncome", "OperatingProfit", "OrdinaryIncome", "ProfitBeforeTax",
             "IncomeBeforeIncomeTaxes")  # 銀行・保険などは営業利益が無いので経常利益で代える
NET = ("ProfitAttributableToOwnersOfParent", "NetIncomeAttributableToOwnersOfParent", "NetIncome", "Profit")

# 値の無い要素は <ix:nonFraction ... xsi:nil="true"/> と自己終了で書かれる。
# 自己終了を除外しないと次の要素の値を取り込んで要素がずれる
_FACT = re.compile(r"<ix:nonFraction\b([^>]*?)(?<!/)>(.*?)</ix:nonFraction>", re.S)
_TEXT = re.compile(r"<ix:nonNumeric\b([^>]*?)(?<!/)>(.*?)</ix:nonNumeric>", re.S)
_ATTR = re.compile(r'([\w:]+)="([^"]*)"')
_SUFFIX = re.compile(r"(IFRS|US|JMIS)$")


@dataclass
class Summary:
    """サマリーの主要数値（金額は百万円、率は%）。値が無ければ None。"""
    quarter: int | None            # 1〜3=四半期、None=本決算（通期）
    consolidated: bool
    sales: float | None = None
    sales_prior: float | None = None
    op: float | None = None         # 営業利益（無ければ経常利益）
    op_prior: float | None = None
    net: float | None = None
    net_prior: float | None = None
    fy_sales: float | None = None   # 四半期: 今期の通期予想／本決算: 来期予想
    fy_op: float | None = None
    fy_net: float | None = None
    div: float | None = None        # 年間配当（四半期: 今期予想、本決算: 今期実績）
    div_prior: float | None = None  # 前期の年間配当
    div_next: float | None = None   # 本決算の来期予想
    forecast_corrected: bool = False
    dividend_corrected: bool = False
    extra: dict = field(default_factory=dict)


def _num(attrs: dict[str, str], inner: str) -> float | None:
    text = html.unescape(re.sub(r"<[^>]+>", "", inner))
    digits = re.sub(r"[^0-9.]", "", text)
    if digits.count(".") > 1:  # 読めない書式の値は捨てる（1項目のために全体を落とさない）
        return None
    if not digits or digits == ".":
        return None
    v = float(digits)
    scale = int(attrs.get("scale", "0") or 0)
    if scale >= 6:  # 金額は百万円にそろえる
        v = v * 10 ** (scale - 6)
    elif scale > 0:
        v = v * 10 ** scale / 1e6
    if attrs.get("sign") == "-":
        v = -v
    return v


def parse_facts(html: str) -> tuple[dict[tuple[str, str], float], dict[str, str]]:
    """数値は (要素名, 文脈) → 値、文字は 要素名 → 値 で返す。要素名は接頭辞と会計基準の末尾を外す。"""
    nums: dict[tuple[str, str], float] = {}
    for m in _FACT.finditer(html):
        attrs = dict(_ATTR.findall(m.group(1)))
        name = _SUFFIX.sub("", attrs.get("name", "").split(":")[-1])
        v = _num(attrs, m.group(2))
        if v is not None:
            nums.setdefault((name, attrs.get("contextRef", "")), v)
    texts: dict[str, str] = {}
    for m in _TEXT.finditer(html):
        attrs = dict(_ATTR.findall(m.group(1)))
        name = attrs.get("name", "").split(":")[-1]
        texts.setdefault(name, re.sub(r"<[^>]+>", "", m.group(2)).strip())
    return nums, texts


def _pick(nums, names, period: str, scope: str, kind: str) -> float | None:
    for n in names:
        v = nums.get((n, f"{period}_{scope}_{kind}"))
        if v is not None:
            return v
    if kind == "ForecastMember":  # 幅で出している会社は中央値を使う
        for n in names:
            lo = nums.get((n, f"{period}_{scope}_LowerMember"))
            hi = nums.get((n, f"{period}_{scope}_UpperMember"))
            if lo is not None and hi is not None:
                return (lo + hi) / 2
    return None


def parse_summary(html: str) -> Summary | None:
    nums, texts = parse_facts(html)
    if not nums:
        return None
    ctxs = {c for _, c in nums}
    m = re.search(r"CurrentAccumulatedQ(\d)Duration", " ".join(ctxs))
    quarter = int(m.group(1)) if m else None
    consolidated = any("_ConsolidatedMember" in c for c in ctxs)
    scope = "ConsolidatedMember" if consolidated else "NonConsolidatedMember"
    cur, prior = (f"CurrentAccumulatedQ{quarter}Duration", f"PriorAccumulatedQ{quarter}Duration") if quarter \
        else ("CurrentYearDuration", "PriorYearDuration")
    fc = "CurrentYearDuration" if quarter else "NextYearDuration"

    def g(names, period, kind="ResultMember"):
        return _pick(nums, names, period, scope, kind)

    def div(period, kind):  # 配当は会社単体の区分で出ることが多い
        for sc in ("NonConsolidatedMember", "ConsolidatedMember"):
            v = nums.get(("DividendPerShare", f"{period}_AnnualMember_{sc}_{kind}"))
            if v is not None:
                return v
        return None

    s = Summary(
        quarter=quarter, consolidated=consolidated,
        sales=g(SALES, cur), sales_prior=g(SALES, prior),
        op=g(OPERATING, cur), op_prior=g(OPERATING, prior),
        net=g(NET, cur), net_prior=g(NET, prior),
        fy_sales=g(SALES, fc, "ForecastMember"), fy_op=g(OPERATING, fc, "ForecastMember"),
        fy_net=g(NET, fc, "ForecastMember"),
        div_prior=div("PriorYearDuration", "ResultMember"),
        forecast_corrected=any(texts.get(k) == "有" for k in texts if k.startswith("CorrectionOf") and "Forecast" in k
                               and "Dividend" not in k),
        dividend_corrected=texts.get("CorrectionOfDividendForecastInThisQuarter") == "有",
    )
    if quarter:
        s.div = div("CurrentYearDuration", "ForecastMember")
    else:
        s.div = div("CurrentYearDuration", "ResultMember")
        s.div_next = div("NextYearDuration", "ForecastMember")
    return s


def fetch_summary_html(url: str, timeout: int = 30) -> str | None:
    """XBRL zip を取得し、サマリーの iXBRL を返す。無ければ None。"""
    r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=timeout)
    r.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        names = [n for n in z.namelist() if "/Summary/" in n and n.endswith("ixbrl.htm")]
        return z.read(names[0]).decode("utf-8", errors="replace") if names else None
