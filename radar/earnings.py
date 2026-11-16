"""
決算短信の数値から「良い決算か・悪い決算か」を判定する（LLMなし）

見るもの（営業利益。無い業種は経常利益）:
  前年同期比        ±30%以上を強い材料とする
  黒字転換・赤字転落
  通期予想に対する進捗率（四半期のみ） 標準（Q1=25%, Q2=50%, Q3=75%）との差
  来期予想（本決算のみ） 今期実績比 ±20%以上
  年間配当          前期比の増配・減配

材料の数（良い − 悪い）で方向と重要度を決める。しきい値は暫定で、
決算短信の XBRL は約1か月で TDnet から消えるため、過去データでは検証できない。
運用で蓄積した結果（Firestore の events.earn）で後から見直す。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from radar.xbrl import Summary

YOY_STRONG = 30.0
NEXT_STRONG = 20.0
PROGRESS_HIGH = 15.0   # 標準より何ポイント上なら好進捗
PROGRESS_LOW = 20.0    # 標準より何ポイント下なら進捗遅れ（Q2以降のみ）
DIV_MIN_CHANGE = 0.05  # 年間配当の変化がこれ未満なら材料にしない


@dataclass
class EarningsRead:
    direction: str                  # up / down / ?
    importance: int
    sales_yoy: float | None
    op_yoy: float | None
    goods: list[str] = field(default_factory=list)
    bads: list[str] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        """通知・画面に出す1行の要約。"""
        parts = self.goods + self.bads + self.notes
        m = self.metrics
        if m.get("op_yoy") is not None and not any(p.startswith(("営業益", "黒字", "赤字")) for p in parts):
            parts.insert(0, f"営業益{m['op_yoy']:+.0f}%")
        if (m.get("progress") or -1) >= 0 and not any(p.startswith("進捗") for p in parts):
            parts.append(f"進捗{m['progress']:.0f}%（標準{m['standard']}%）")
        if m.get("next_yoy") is not None and not any(p.startswith("来期") for p in parts):
            parts.append(f"来期予想{m['next_yoy']:+.0f}%")
        return " ".join(parts)


def _yoy(cur: float | None, prior: float | None) -> float | None:
    if cur is None or prior is None or prior <= 0 or cur < 0:
        return None  # 赤字をまたぐ比較は率にしない（黒字転換・赤字転落で別に扱う）
    return (cur - prior) / prior * 100


def judge(s: Summary) -> EarningsRead:
    goods: list[str] = []
    bads: list[str] = []
    notes: list[str] = []  # 表示だけする材料
    m: dict = {"quarter": s.quarter}

    op_yoy = _yoy(s.op, s.op_prior)
    sales_yoy = _yoy(s.sales, s.sales_prior)
    m["op"], m["op_prior"] = s.op, s.op_prior

    # 前年同期比・黒字転換
    if s.op is not None and s.op_prior is not None:
        if s.op_prior < 0 < s.op:
            goods.append("黒字転換")
        elif s.op_prior > 0 > s.op:
            bads.append("赤字転落")
        elif op_yoy is not None and op_yoy >= YOY_STRONG:
            goods.append(f"営業益{op_yoy:+.0f}%")
        elif op_yoy is not None and op_yoy <= -YOY_STRONG:
            bads.append(f"営業益{op_yoy:+.0f}%")

    # 通期予想に対する進捗（四半期）
    if s.quarter and s.fy_op and s.fy_op > 0 and s.op is not None:
        progress = s.op / s.fy_op * 100
        standard = s.quarter * 25
        m["progress"], m["standard"] = round(progress, 1), standard
        rising = op_yoy is None or op_yoy > 0
        if progress >= standard + PROGRESS_HIGH and rising and s.op > 0:
            goods.append(f"進捗{progress:.0f}%（標準{standard}%）")
        elif s.quarter >= 2 and progress <= standard - PROGRESS_LOW and not rising:
            bads.append(f"進捗{progress:.0f}%（標準{standard}%）")

    # 来期予想（本決算）
    if not s.quarter and s.fy_op is not None and s.op is not None:
        if s.op > 0:
            nxt = (s.fy_op - s.op) / s.op * 100
            m["next_yoy"] = round(nxt, 1)
            if nxt >= NEXT_STRONG:
                goods.append(f"来期予想{nxt:+.0f}%")
            elif nxt <= -NEXT_STRONG:
                bads.append(f"来期予想{nxt:+.0f}%")
        elif s.op < 0 < s.fy_op:
            goods.append("来期黒字予想")

    # 配当（四半期は今期予想と前期、本決算は来期予想と今期）
    new, old = (s.div, s.div_prior) if s.quarter else (s.div_next, s.div)
    if new is not None and old is not None:
        m["div"], m["div_old"] = new, old
        # 記念配当の剥落などの小さな変化は材料にしない
        # 増配は決算短信では頻出し当日反応がほぼ無い（2026年10〜11月の約1000件で中央値−0.1%）ため、
        # 表示だけして判定には数えない。減配は反応がある（中央値−0.9%）ので数える
        if new > old * (1 + DIV_MIN_CHANGE) or (old == 0 and new > 0):
            notes.append(f"増配{old:g}→{new:g}円")
        elif new < old * (1 - DIV_MIN_CHANGE):
            bads.append(f"減配{old:g}→{new:g}円")

    score = len(goods) - len(bads)
    direction = "up" if score > 0 else "down" if score < 0 else "?"
    importance = 3 if abs(score) >= 2 else 2 if abs(score) == 1 else 1
    m["sales_yoy"] = None if sales_yoy is None else round(sales_yoy, 1)
    m["op_yoy"] = None if op_yoy is None else round(op_yoy, 1)
    return EarningsRead(direction, importance, sales_yoy, op_yoy, goods, bads, m, notes)
