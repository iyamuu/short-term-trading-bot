# アーキテクチャ概要

新BOT（BTCUSDT Perpetual / Bitget / one-way mode）の全体像。ロードマップ全体は
[Issue #1](https://github.com/iyamuu/short-term-trading-bot/issues/1) を参照。本ドキュメント
は前提固定（Phase 0）であり、現段階で実装済みなのは storage / 発注パラメータ / 集計と docs。

## データフロー

```
Bitget (Public/Private WS, REST)
  -> Data Ingestion (WS常駐, candle builder, REST backfill)         [Phase 5, stub]
  -> Storage / Feature Store (SQLite state, parquet/DuckDB logs)    [Phase 0/1, 実装済]
  -> Feature Engineering (EMA/RSI/ATR/ADX/BB/MACD, micro, deriv)    [stub]
  -> Signal (Rule-based core + ML meta filter)                     [Phase 7/8, stub]
  -> Decision & Risk Engine (sizing, caps, circuit breaker)        [Phase 4, stub]
  -> Execution (CCXT/REST, reduceOnly, TP/SL, reconciliation)      [Phase 6, 一部実装]
  -> Monitoring / Ops (Discord, metrics, daily LLM report)         [Phase 9, stub]
```

## Production decision flow（reject 分類の根拠）

候補が落ちる箇所を `RejectStage` / `RejectReason` として記録する（`candidate_log`）。

```
data_quality_ok? ──no→ DATA_QUALITY / DATA_QUALITY_NG
global_stop?     ──yes→ GLOBAL_STOP / GLOBAL_STOP
regime in (LOW_VOL_CHOP, HIGH_VOL_RISK)? ──yes→ REGIME / REGIME_NG
pre_signal_filters ──fail→ RISK_PRE_FILTER / {SPREAD_NG, FUNDING_NG, RISK_PRE_FILTER_NG}
rule_signal is None ──→ NO_SETUP / NO_SETUP
pre_trade_check ──fail→ RISK_TRADE / RISK_TRADE_NG
ml_score < skip ──→ ML_FILTER / ML_FILTER_NG  (else size 0.5 / 1.0)
validate_order_plan ──fail→ ORDER_PLAN_RISK / ORDER_PLAN_RISK_NG
else ──→ submit
```

## ログの責務分離（Phase 1 の要）

| データセット | 種別 | 冪等キー | 役割 |
|---|---|---|---|
| `trade_log` | サマリ | `trade_id` | 1トレード1行。pnl/R/MAE/MFE/context/再現性メタ |
| `order_event_log` | 事実 | `event_id` | 注文ライフサイクル（submit/ack/reject/cancel/amend） |
| `fill_log` | 事実 | `fill_id` | 約定単位。pnl/fee/slippage はここから再構築可能 |
| `candidate_log` | 事実 | `candidate_id` | reject された候補 + 理由 |

SQLite（短期状態）と parquet（履歴/分析）の整合は **outbox パターン**で担保する
（[current_state_schema.md](../data/current_state_schema.md) 参照）。

## 安全要件（抜粋）

- SLがない状態でポジションを保持しない。
- BOT状態と取引所状態が不一致なら新規entryを止める。
- すべての決済注文は reduceOnly 必須。SHORT の contracts は正値で扱う。
- これらは後続フェーズの execution/risk で強制し、本フェーズではスキーマ・発注パラメータ
  でその前提を固定する。
