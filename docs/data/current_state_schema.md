# 状態・ログスキーマ（Phase 0 固定）

実体は `src/storage/models.py`（レコード）と `src/storage/schema.sql`（SQLite）。本書は
その仕様を人間可読にまとめたもの。両者が乖離したら **コードが正**。

## SQLite（短期状態） — `schema.sql`

| テーブル | 役割 | 備考 |
|---|---|---|
| `open_position` | 現在のオープンポジション（one-way, 1件） | `contracts` は CHECK で `>= 0`。SHORT も正値、方向は `side` |
| `open_orders` | entry/TP1/TP2/SL の working order | PK `(trade_id, order_role)`、`order_id` + `client_oid` |
| `mae_mfe_track` | 保有中の running high/low | close 時に MAE/MFE を算出する元データ |
| `risk_session` | daily/weekly/session pnl・連敗・cooldown | Phase 4 の器を先行用意（id=1 の単一行） |
| `log_outbox` | 確定ログの未flushキュー | `outbox_id`(PK), `kind`, `payload_json`, `flushed_at` |

### outbox パターン（整合性の要）

`close_trade` / `record_*` は **SQLite の単一トランザクション内**で状態変更と
`log_outbox` への確定イベント追記を完結させる。parquet への書き込みはここでは行わない。

```
close_trade(record):
  BEGIN
    excursion を record に反映
    INSERT OR IGNORE INTO log_outbox (kind=TRADE, ...)
    DELETE open_orders / mae_mfe_track / open_position
  COMMIT
```

別途 `outbox.flush(store, base_dir)` が未flush行を parquet へ **冪等**に書く
（id重複は no-op）。成功で `flushed_at` を打刻 → 再実行は no-op。parquet 側が壊れ続ける
場合は `data/fallback/<kind>.jsonl` へ退避（未flushのまま残し次回リトライ）。
`reintegrate_fallback` が JSONL を `outbox_id` で重複排除して parquet へ再統合する。

これにより「SQLite は close 済みだが parquet 書き込みが消えた」状態を構造的に防ぐ。

## parquet レイアウト

```
data/<dataset>/dt=YYYY-MM-DD/data.parquet   # UTC日付パーティション、追記で再書き込み
```

データセット: `trade_log` / `order_event_log` / `fill_log` / `candidate_log`。

マーケットデータ（OHLCV / funding）の parquet 基盤は別途
[market_data.md](./market_data.md) を参照。

## TradeRecord（サマリ）主要列

identity: `trade_id, signal_id, side`
entry: `entry_time, entry_price, entry_size, entry_order_id, entry_client_oid`
exit: `exit_time, exit_price, exit_reason, close_order_id, close_client_oid`
pnl/cost: `pnl_abs, pnl_pct, pnl_r, fee_abs, funding_abs, slippage_estimated`
levels: `initial_sl, initial_tp1, initial_tp2, final_sl, hit_tp1, hit_tp2, moved_to_breakeven`
**excursion（列分離）**: `mfe_price, mae_price, mfe_pct, mae_pct, mfe_r, mae_r`
context: `holding_minutes, regime, setup_name, entry_reason, confidence, ml_score, risk_size_multiplier`
再現性: `features_snapshot(JSON), features_schema_version, config_hash, strategy_params_snapshot(JSON), bot_version, strategy_version, model_version`

### MAE/MFE の定義（`ExcursionTracker`）

- favorable = トレード方向への最大逸れ、adverse = 逆方向への最大逸れ（いずれも正の大きさ）。
- LONG: `mfe_price = high - entry`, `mae_price = entry - low`
- SHORT: `mfe_price = entry - low`, `mae_price = high - entry`
- pct = price / entry_price、R = price / |entry_price − initial_sl|。
- R の分母に使った `initial_sl` は同じ行に保持し、後から検証可能にする。

## OrderEventRecord / FillRecord（事実ログ）

- `OrderEventRecord`: `event_id, ts, trade_id, signal_id, order_id, client_oid, order_role(ENTRY/TP1/TP2/SL), event_type(SUBMIT/ACK/REJECT/CANCEL/AMEND), side, reduce_only, price, stop_price, trigger_type, size, status, raw_detail(JSON)`
- `FillRecord`: `fill_id, ts, trade_id, order_id, client_oid, order_role, side, price, size, fee_abs, is_maker, liquidity, slippage_vs_intended`
- 設計意図: trade の pnl/fee/slippage を fill 群から再構築できること。

## CandidateRecord（reject）

`candidate_id, ts, signal_id, side, regime, setup_name, reject_stage, reject_reason, reject_detail, confidence, ml_score, features_snapshot(JSON), features_schema_version, config_hash, bot_version, strategy_version`

reject 分類は decision flow 全体を包含（[overview.md](../architecture/overview.md) 参照）。

## 実データ例（demo）

`python research/baseline_report.py --demo --base-dir ./data` を実行すると、open→
order_event→fill→close→flush のライフサイクルでダミーの trade/candidate が生成され、
上記レイアウトで parquet が書き出される。
