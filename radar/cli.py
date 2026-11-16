"""
コマンドライン

  python -m radar.cli master            銘柄マスタ（JPX）を更新
  python -m radar.cli collect [--date YYYY-MM-DD] [--no-enrich]
  python -m radar.cli show [--date YYYY-MM-DD] [--min 2] [--size small,micro] [--type buyback,...]
"""
from __future__ import annotations

import argparse
import logging
from datetime import date

from radar import db
from radar.master import download_master, load_master
from radar.models import today_jst
from radar.pipeline import backfill, collect

ARROW = {"up": "↑", "down": "↓", "?": "・"}
SIZE_LABEL = {"large": "大型", "mid": "中型", "small": "小型", "micro": "超小型", "other": "他", "": "不明"}


def cmd_master(_: argparse.Namespace) -> None:
    print(f"銘柄マスタを更新: {download_master()}件")


def cmd_collect(a: argparse.Namespace) -> None:
    events = collect(a.date, enrich=not a.no_enrich)
    print(f"新規イベント {len(events)}件")


def cmd_show(a: argparse.Namespace) -> None:
    master = load_master()
    d = a.date or today_jst()
    sizes = set(a.size.split(",")) if a.size else None
    types = set(a.type.split(",")) if a.type else None
    with db.get_conn() as conn:
        rows = conn.execute(
            """SELECT d.published_at, d.code, d.company_name, d.title, d.pdf_url,
                      e.type, e.label, e.direction, e.importance, e.sales_rate, e.profit_rate
               FROM events e JOIN disclosures d USING (doc_id)
               WHERE d.published_at LIKE ? AND e.importance >= ?
               ORDER BY e.importance DESC, d.published_at DESC""",
            (f"{d.isoformat()}%", a.min),
        ).fetchall()
    shown = 0
    for r in rows:
        sec = master.get(r["code"])
        size = sec.size if sec else ""
        if sizes and size not in sizes:
            continue
        if types and r["type"] not in types:
            continue
        rate = f" 営業益{r['profit_rate']:+.0f}%" if r["profit_rate"] is not None else ""
        print(f"[{'★' * r['importance']:<3}] {r['published_at'][11:]} {r['code']} {r['company_name'][:12]:<12}"
              f" {SIZE_LABEL[size]:<3} {ARROW[r['direction']]} {r['label']}{rate}")
        print(f"        {r['title'][:70]}")
        shown += 1
    print(f"--- {shown}件 ({d})")


def cmd_earnings_sample(a: argparse.Namespace) -> None:
    """期間内の決算短信の XBRL を読み、判定結果を CSV に書く（しきい値の調整用）。中断しても再開できる。"""
    import csv
    import os
    import time
    from dataclasses import asdict

    from radar.earnings import judge
    from radar.xbrl import fetch_summary_html, parse_summary

    done: set[str] = set()
    if os.path.exists(a.out):
        with open(a.out, encoding="utf-8") as f:
            done = {r["doc_id"] for r in csv.DictReader(f)}
    with db.get_conn() as conn:
        rows = conn.execute(
            """SELECT doc_id, published_at, code, title, xbrl_url FROM disclosures
               WHERE title LIKE '%決算短信%' AND title NOT LIKE '%訂正%' AND xbrl_url <> ''
                 AND published_at >= ? AND published_at < ? ORDER BY published_at""",
            (a.start.isoformat(), (a.end.isoformat() + "~")),
        ).fetchall()
    todo = [r for r in rows if r["doc_id"] not in done]
    print(f"対象 {len(rows)}件、未処理 {len(todo)}件")
    fields = ["doc_id", "published_at", "code", "title", "direction", "importance", "goods", "bads",
              "quarter", "consolidated", "sales", "sales_prior", "op", "op_prior", "net", "net_prior",
              "fy_sales", "fy_op", "fy_net", "div", "div_prior", "div_next", "forecast_corrected",
              "progress", "next_yoy", "op_yoy", "sales_yoy", "error"]
    new_file = not os.path.exists(a.out)
    with open(a.out, "a", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        if new_file:
            w.writeheader()
        for i, r in enumerate(todo, 1):
            row = {k: r[k] for k in ("doc_id", "published_at", "code", "title")}
            try:
                html = fetch_summary_html(r["xbrl_url"])
                s = parse_summary(html) if html else None
                if s is None:
                    row["error"] = "サマリーなし"
                else:
                    e = judge(s)
                    row |= {k: v for k, v in asdict(s).items() if k != "extra"}
                    row |= {"direction": e.direction, "importance": e.importance, "goods": "|".join(e.goods),
                            "bads": "|".join(e.bads), "progress": e.metrics.get("progress"),
                            "next_yoy": e.metrics.get("next_yoy"), "op_yoy": e.metrics.get("op_yoy"),
                            "sales_yoy": e.metrics.get("sales_yoy")}
            except Exception as exc:  # noqa: BLE001
                row["error"] = str(exc)[:80]
            w.writerow(row)
            if i % 100 == 0:
                f.flush()
                print(f"{i}/{len(todo)}")
            time.sleep(a.sleep)


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    logging.getLogger("pypdf").setLevel(logging.ERROR)  # PDFのフォント警告は判定に影響しないため抑制
    p = argparse.ArgumentParser(prog="radar")
    sub = p.add_subparsers(required=True)

    sub.add_parser("master").set_defaults(func=cmd_master)

    c = sub.add_parser("collect")
    c.add_argument("--date", type=date.fromisoformat)
    c.add_argument("--no-enrich", action="store_true")
    c.set_defaults(func=cmd_collect)

    b = sub.add_parser("backfill")
    b.add_argument("--start", type=date.fromisoformat, required=True)
    b.add_argument("--end", type=date.fromisoformat, required=True)
    b.set_defaults(func=lambda a: backfill(a.start, a.end))

    s = sub.add_parser("show")
    s.add_argument("--date", type=date.fromisoformat)
    s.add_argument("--min", type=int, default=2)
    s.add_argument("--size")
    s.add_argument("--type")
    s.set_defaults(func=cmd_show)

    e = sub.add_parser("earnings-sample")
    e.add_argument("--start", type=date.fromisoformat, required=True)
    e.add_argument("--end", type=date.fromisoformat, required=True)
    e.add_argument("--out", default="data/earnings_sample.csv")
    e.add_argument("--sleep", type=float, default=0.2)
    e.set_defaults(func=cmd_earnings_sample)

    a = p.parse_args(argv)
    a.func(a)


if __name__ == "__main__":
    main()
