# 開示レーダー（disclosure-radar）

東証の適時開示（TDnet）を全上場銘柄について取得し、タイトルからイベントを分類して重要なものを知らせる個人用ツール。無料のデータ源だけで動く。

## 現在の状態（Phase 1）

- 取得: 非公式TDnet API（webapi.yanoshin.jp）。障害時は TDnet 閲覧ページから取得
- 分類: タイトルの正規表現で14種類のイベントに分ける（LLMは使わない）
- 補完: 業績予想の修正・差異は開示PDFの表を読み、上方／下方と営業利益の増減率を判定
- 保存: SQLite（`data/radar.db`）
- 銘柄マスタ: JPX 上場銘柄一覧（規模区分で 大型／中型／小型／超小型 を判定）

## 使い方

```bash
python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m radar.cli master                      # 銘柄マスタ更新
.venv/bin/python -m radar.cli collect                     # 今日の開示を取り込み
.venv/bin/python -m radar.cli collect --date 2026-11-13   # 日付指定
.venv/bin/python -m radar.cli show --min 3 --size small,micro
.venv/bin/pytest -q
```

## イベント種別と暫定の重要度

| 種別 | 方向 | 重要度 |
|---|---|---|
| 自社株買い（取得決定） | ↑ | 3 |
| TOB・MBO・再編 | 対象側なら↑ | 3 |
| 上場廃止・不正・継続疑義 | ↓ | 3 |
| 業績予想の修正／予想と実績の差異 | PDFで判定 | 2（営業利益±30%以上で3） |
| 配当の変更・株式分割・株主優待・増資／売出し・資本業務提携 | タイトルで判定 | 2 |
| 決算短信 | XBRLの数値で判定（`radar/earnings.py`） | 1〜3 |
| 業務提携・受注・特別損益・中期経営計画 | — | 1 |

重要度は暫定値。Phase 2 で過去の株価反応を集計して置き換える。

## 今後

1. Phase 2: 2016年以降の開示 × 株価でイベントスタディ → 重要度とプッシュ対象を決める
2. Phase 3: GitHub Actions（公開リポジトリ）で15分ごとに実行、Firestore、PWA＋FCMプッシュ通知
3. Phase 4: 決算短信の読解（XBRL数値の判定は実装済み。結果は docs/FINDINGS.md。文章の比較は未着手）
