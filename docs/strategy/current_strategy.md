# 戦略仕様（Phase 0 固定）

新BOTの売買仕様。MLを入れる前に、Rule-based core だけで期待値のある単純で堅牢な戦略を
固定し、以降の改善で「何が変わったか」を追跡可能にする。本書は intended spec（既存BOTは
無いため新規定義）。トレードパラメータの実体は `src/config.py` の `StrategyConfig`、
変更時は `config_hash()` が trade ログに残る。

> 注意: `StrategyConfig` のトレードパラメータを変更した場合は、本書に変更理由と期待効果を
> 追記する（Issue Phase 7 の方針）。

## レジーム分類

`TREND_UP, TREND_DOWN, RANGE, LOW_VOL_CHOP, HIGH_VOL_RISK`。
no-trade 条件（`LOW_VOL_CHOP` / `HIGH_VOL_RISK` 等）は **エントリー条件より先に評価**する。

## Core strategy

### 1. Trend Pullback
- 1h/15m でトレンド方向を確認（EMA整列 / ADX）。
- 5m/1m で押し目・戻りを検出。
- ATR / ADX / BB width で低ボラ・レンジを回避。

### 2. Volatility Breakout
- squeeze 後の拡大を狙う。
- false breakout 回避のため出来高 / ADX / 上位足方向を確認。
- config で有効/無効を切替可能にする。

### 3. No-trade Regime
- low volatility chop / high spread / funding extreme / event risk / post-loss cooldown。

## エントリー

- no-trade 条件を通過し、regime が許容内であること。
- setup（trend_pullback / breakout）が成立すること。
- `entry_reason` を feature 単位で `candidate_log` / `trade_log` に保存する。

## 決済（exit plan）

- TP1（`tp1_r`, 既定 1.0R） / TP2（`tp2_r`, 既定 2.0R）の部分利確。
- TP1 約定後、残ポジションの SL を建値へ移動（`move_sl_to_breakeven_after_tp1`）。
- 2R 到達後（`trailing_after_r`）に trailing stop。
- すべての決済注文は reduceOnly。SL は market trigger（mark price 基準）。
  発注引数は CCXT-unified 形（`triggerPrice` + `triggerType` passthrough）で
  `src/execution/bitget_client.py` に固定・テスト済み。生の Bitget REST へのマッピングは
  CCXT 側の責務として後続の execution フェーズで mock 固定する。

## サイズ計算

Kelly sizing は不採用。保守的な volatility targeting + hard cap。

```
base_size       = account_equity * risk_per_trade / sl_distance
vol_multiplier  = target_vol / current_vol
confidence_mult = 0.0 / 0.5 / 1.0   # ML meta filter の出力
final_size      = min(base_size * vol_multiplier * confidence_mult, max_notional_cap)
```

## ML meta filter（後続 Phase 8）

- 方向は決めない。Rule-based 候補に対する skip / half / full size のみ。
- ラベルは未来リターンsignではなく triple-barrier（+2R / -1R / time barrier）。
- `ml_skip_threshold` / `ml_full_size_threshold` は `StrategyConfig` 管理。
- model 不在時は Rule-based のみで安全に動作（fallback）。
