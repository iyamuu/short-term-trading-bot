# short-term-trading-bot

BTCUSDT Perpetual（Bitget / one-way mode）向け短期売買BOT。数十分〜数時間保有。
Rule-based core + ML meta filter + WebSocket常駐 + strict risk engine + research/validation の段階構築。

ロードマップ全体は [Issue #1](https://github.com/iyamuu/short-term-trading-bot/issues/1) を参照。

## 現在のスコープ: 基盤 + Phase 0/1（ログ完全化）

最重要は AI ではなく **リスク管理・実行品質・状態同期・過学習防止・データ品質**。
本リポジトリの現段階は、後続フェーズすべての土台となるロギング基盤を実装している。

- `src/storage/` — trade サマリ / order・fill 事実ログ / candidate(reject) ログ / SQLite 状態 + outbox
- `src/execution/bitget_client.py` — 発注パラメータ生成 pure function（送信はしない）
- `research/baseline_report.py` — DuckDB によるベースライン集計
- `docs/` — 戦略 / 運用 / データスキーマ / アーキテクチャの前提固定ドキュメント

`src/data` `src/features` `src/strategy` `src/ml` `src/risk` `src/monitoring` `src/runtime` は
後続フェーズ用の stub（module docstring + 最小 interface）。

## セットアップ

```bash
# uv のインストール (未導入の場合)
curl -LsSf https://astral.sh/uv/install.sh | sh

# 依存解決 + 仮想環境作成
uv sync --extra dev

# 環境変数
cp .env.example .env   # 値を埋める（.env はコミットしない）
```

## 開発コマンド

```bash
uv run pytest            # テスト
uv run ruff check .      # lint
uv run mypy src          # 型チェック

# ベースラインレポート（ダミーデータで動作確認）
uv run python research/baseline_report.py --demo
```

## ディレクトリ責務

| パス | 役割 |
|------|------|
| `src/config.py` | 接続/環境設定 + 型付き `StrategyConfig` |
| `src/storage/` | 状態管理・ログ（**実装本体**） |
| `src/execution/` | 発注パラメータ生成（Phase 0） / 他は stub |
| `research/` | backtest / walk-forward / 集計（baseline のみ実装） |
| `docs/` | 前提固定ドキュメント（Phase 0 成果物） |
