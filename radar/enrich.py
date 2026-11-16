"""
業績予想修正の方向（上方／下方）を開示PDFの表から判定する

タイトルに「上方」「下方」が無い修正が約9割あるため、PDFの
「前回発表予想（A）」「今回修正予想（B）」「増減額（B－A）」「増減率」の行を読む。
数値はコードで計算し、LLMは使わない。

利益列（営業利益・経常利益・当期純利益＝表の2〜4列目）の増減の符号を多数決で決める。
銀行など列構成が違う会社でも、2列目以降が利益系である点は共通。
"""
from __future__ import annotations

import io
import re
from dataclasses import dataclass

import requests

from radar.models import USER_AGENT

NUM_RE = re.compile(r"[△▲\-－−]?\d[\d,]*(?:\.\d+)?")
UNIT_RE = re.compile(r"百万円|千円|円|銭|％|%|\(|\)|（|）")
ROW_A = re.compile(r"前回(発表)?予想")
ROW_B = re.compile(r"今回\s*(修正|発表)?\s*予想|(今期|今回|当期)?\s*実績値?\s*[（(]\s*[ＢB]")
TEXT_UP = re.compile(r"(前回|当初|期初)[^。]{0,12}予想を?上回|上方修正")
TEXT_DOWN = re.compile(r"(前回|当初|期初)[^。]{0,12}予想を?下回|下方修正")
ROW_DIFF = re.compile(r"増減額")
ROW_RATE = re.compile(r"増減率")
LABEL_RE = re.compile(r"[（(][ＡＢA-Bａｂ\s\-－ー−Ｂ－Ａ]+[）)]")


@dataclass(frozen=True)
class Revision:
    direction: str  # up / down / ?
    sales_rate: float | None  # 売上高の増減率（%）
    profit_rate: float | None  # 営業利益の増減率（%）


def parse_numbers(line: str) -> list[float]:
    """行から数値を順に取り出す。△・▲・－はマイナス。ラベル内の (A)(B) は除外。"""
    body = LABEL_RE.sub(" ", line)
    body = UNIT_RE.sub(" ", body)
    out: list[float] = []
    for tok in NUM_RE.findall(body):
        neg = tok[0] in "△▲-－−"
        v = float(tok.lstrip("△▲-－−").replace(",", ""))
        out.append(-v if neg else v)
    return out


def _first_row(lines: list[str], pat: re.Pattern[str], min_nums: int) -> list[float] | None:
    for ln in lines:
        if pat.search(ln):
            nums = parse_numbers(ln)
            if len(nums) >= min_nums:
                return nums
    return None


def _majority(signs: list[float]) -> str:
    pos = sum(1 for s in signs if s > 0)
    neg = sum(1 for s in signs if s < 0)
    if pos > neg:
        return "up"
    if neg > pos:
        return "down"
    return "?"


def judge_revision(text: str) -> Revision:
    """PDFのテキストから修正の方向と増減率を求める。"""
    lines = [ln for ln in text.splitlines() if ln.strip()]
    rate = _first_row(lines, ROW_RATE, 2)
    diff = _first_row(lines, ROW_DIFF, 2)

    a = b = None
    for i, ln in enumerate(lines):
        if ROW_B.search(ln) and not ROW_A.search(ln):
            nb = parse_numbers(ln)
            if len(nb) >= 3:
                b = nb
                # 直前の行から A を探す（実績値との差異表をまたがないよう3行以内）
                for prev in reversed(lines[max(0, i - 3):i]):
                    if ROW_A.search(prev) and len(parse_numbers(prev)) >= 3:
                        a = parse_numbers(prev)
                        break
                if "修正" in ln:
                    break

    if a and b:
        n = min(len(a), len(b), 4)
        direction = _majority([b[k] - a[k] for k in range(1, n)])
        sales = (b[0] - a[0]) / abs(a[0]) * 100 if a[0] else None
        profit = (b[1] - a[1]) / abs(a[1]) * 100 if len(a) > 1 and a[1] else None
        return Revision(direction, _r(sales), _r(profit))
    if diff:
        return Revision(
            _majority(diff[1:4]),
            _r(rate[0]) if rate else None,
            _r(rate[1]) if rate and len(rate) > 1 else None,
        )
    # 表が崩れて読めない場合: 増減率の行で符号がそろっていればそれを使う
    if rate and (all(v > 0 for v in rate) or all(v < 0 for v in rate)):
        return Revision("up" if rate[0] > 0 else "down", None, None)
    # 最後の手段: 本文の「前回予想を上回る／下回る」「上方／下方修正」の出現数
    up, down = len(TEXT_UP.findall(text)), len(TEXT_DOWN.findall(text))
    if up != down:
        return Revision("up" if up > down else "down", None, None)
    return Revision("?", None, None)


def _r(v: float | None) -> float | None:
    return round(v, 1) if v is not None else None


def fetch_pdf_text(url: str, max_pages: int = 3) -> str:
    from pypdf import PdfReader

    resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=30)
    resp.raise_for_status()
    reader = PdfReader(io.BytesIO(resp.content))
    return "\n".join((p.extract_text() or "") for p in reader.pages[:max_pages])
