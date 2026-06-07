# 運用・常駐構成（Phase 0 固定）

新BOTは WebSocket 常駐プロセスのため、サーバーレスではなく常駐 VM/VPS を前提とする。
第一候補は **Oracle Cloud Always Free Tier（VM.Standard.A1.Flex, ARM64）**。現行BOTとは
ログ・状態・通知・環境を分離する。

## ランタイム前提

- Ubuntu ARM64 + Docker / Docker Compose + systemd。
- Python 常駐プロセス（`python -m src.runtime.main`、現状 stub）。
- 状態: SQLite（`BOT_STATE_DB`）。履歴/ログ: local parquet + DuckDB。
- 通知: 新BOT専用 Discord webhook（`BOT_DISCORD_WEBHOOK`）。
- ARM64 で LightGBM/XGBoost/numpy/pandas/DuckDB を動かすため Docker でバージョン固定。

## 常駐サービス構成（後続 Phase 5）

```
bot_main.service        # 判断・発注ループ
data_collector.service  # Public/Private WS 常駐, candle builder
order_sync.service      # orders/fills/positions 同期, reconciliation
watchdog.service        # heartbeat 監視, 異常時アラート
```

## systemd 例（`bot_main`）

```ini
[Unit]
Description=BTC Bot Main
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=/opt/btc-bot-v2
EnvironmentFile=/opt/btc-bot-v2/.env
ExecStart=/opt/btc-bot-v2/.venv/bin/python -m src.runtime.main
Restart=always
RestartSec=5
User=btc-bot

[Install]
WantedBy=multi-user.target
```

## 失敗時挙動 / タイムアウト方針（仕様）

- WS 切断中・状態不一致・SL欠落・max daily loss 到達・manual_stop 時は **新規entryを停止**。
- max total DD 到達時は BOT 停止。
- API timeout 後は REST 照合してから再送（clientOid で冪等）。
- プロセスは systemd `Restart=always` で復帰。起動時に REST で open orders/position を照合。

## OCI 導入手順（要約）

1. A1 Flex 確保 → Ubuntu ARM64 + Docker + Compose + systemd。
2. Bitget Public WS 接続維持テスト。
3. Private WS / REST reconciliation の paper テスト。
4. SQLite / DuckDB / parquet のログ分離確認。
5. Discord 通知と critical alert 確認。
6. 2〜4週間 paper trading → 小ロット live。

## 注意点

- A1 Flex はリージョンで空きが無い場合あり。割当メモリは過剰にしない（回収対策）。
- Budget / quota / backup retention を設定し無料枠超過を防ぐ。
- 本番資金前に Public/Private WS・REST reconciliation・logrotate・Discord alert を paper 検証。

## 現段階の運用確認（実装済み範囲）

```bash
uv sync --extra dev
uv run pytest            # ロギング基盤のテスト
uv run python research/baseline_report.py --demo --base-dir ./data
```
