# pachi-border-json

パチンコ新台のボーダー情報（貸玉料金ごとの目安回転数）を [P-TOWN](https://p-town.dmm.com) から自動収集し、`suggestions.json` として公開するリポジトリです。

## データファイル

- `suggestions.json`: 収集済みの機種データ（id, name, kana, url, releaseDate, borders）

## セットアップ

```bash
pip install -r requirements.txt
playwright install --with-deps chromium
```

## コマンド

### 最新のボーダー情報を取得する

引数なしで実行すると、新台カレンダーの直近（今日以前で最新、なければ最も近い未来）の日付に発売された機種のボーダー情報を取得し、`suggestions.json` を更新します。

```bash
python scripts/scrape.py
```

### 特定年月のボーダー情報を取得する

`--month` オプションに `YYYY-MM` 形式で年月を指定すると、その月に発売された全機種のボーダー情報を取得します。複数月をスペース区切りで指定することも可能です。

```bash
python scripts/scrape.py --month 2026-05
python scripts/scrape.py --month 2026-05 2026-06
```

### 過去データの一括取込

`scripts/import_history.py` は、指定した年の範囲（1月〜12月）のカレンダーを走査し、未取得の機種データを一括取得します。既にボーダーとひらがな名が取得済みの機種はスキップされます。

```bash
python scripts/import_history.py [開始年] [終了年]

# 例: 2024年分をまとめて取得
python scripts/import_history.py 2024 2024
```

引数を省略した場合は `2024` 年のみを対象とします。

## 自動実行（GitHub Actions）

`.github/workflows/scrape.yml` により、毎週月曜 9:00 JST に `scripts/scrape.py` が自動実行され、`suggestions.json` に差分があればコミット・pushされます。

`workflow_dispatch` から手動実行する場合、`month` 入力にカンマ区切りで年月（例: `2026-05,2026-06`）を指定すると、その月を対象に取得できます。空欄の場合は自動（最新日付）で取得します。
