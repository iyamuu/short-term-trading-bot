# マーケットデータ基盤（OHLCV / funding backfill）

実体は `src/data/rest_backfill.py`。Bitget の **public REST**（CCXT 経由）で履歴データを取得し
parquet 化する。**APIキーは不要・未使用**、発注・private API は一切しない。backtest（PR #4）と
将来の特徴量/ML の共通土台。状態ログ側は [current_state_schema.md](./current_state_schema.md) 参照。

**シンボルの扱い**: ストレージ（parquet path / `symbol` 列 / id）は **市場シンボル `BTCUSDT`** を使い、
取引所 API 呼び出しのみ **CCXT unified `BTC/USDT:USDT`** に変換して用いる（`to_ccxt_symbol`）。
`/`・`:` を含む unified シンボルは path を壊すため `dataset_name` がガードして弾く。

## parquet レイアウト

path は `dataset_name()` が単一生成（規約変更を1箇所に集約）。`dt=` パーティションは
`storage.parquet_writer.append_rows` が付与する。

```
data/market/ohlcv/symbol=BTCUSDT/timeframe=1m/dt=YYYY-MM-DD/data.parquet
data/market/funding/symbol=BTCUSDT/dt=YYYY-MM-DD/data.parquet
```

## row スキーマ

OHLCV:

| 列 | 型 | 備考 |
|---|---|---|
| `candle_id` | str | `"{symbol}:{timeframe}:{timestamp}"`（**冪等キー**。dataset 混在でも衝突しない） |
| `timestamp` | int | candle open time, epoch ms |
| `open_time` | str | ISO8601（`dt=` パーティション導出用） |
| `open/high/low/close` | float | |
| `volume` | float | |
| `symbol` / `timeframe` | str | |

funding:

| 列 | 型 | 備考 |
|---|---|---|
| `funding_id` | str | `"{symbol}:{timestamp}"`（冪等キー） |
| `timestamp` | int | epoch ms |
| `time_iso` | str | ISO8601 |
| `symbol` | str | |
| `funding_rate` | float\|null | |

## 仕様

- **冪等**: 同一 `candle_id`/`funding_id` は `append_rows` が dedup（バッチ内＋既存）。再 backfill で
  行数は増えない。
- **incremental**: `since` 省略時は **最新 `dt=` partition の max(timestamp) + interval** から再開
  （`last_stored_timestamp` は全 partition を読まず最新のみ）。
- **`until` 厳守**: `fetch_ohlcv_range` は `timestamp > until_ms` の candle を必ず落とす（CCXT は
  since 以降を limit 分返すため最終ページに超過分が混ざる）。
- **gap detection**: `detect_gaps` は timeframe 間隔から欠損区間 `(start_ms, end_ms, missing_count)` を
  **報告のみ**（fail させない）。backfill 結果に同梱。
- **validation**: `validate_ohlcv_rows` がスキーマ・timestamp 単調増加・OHLC sanity・volume>=0 を検査。
- **funding も pagination + incremental**: OHLCV 同様に `since` 省略時は最新 ts から resume し、
  ページングで全件取得（cursor は `last_ts + 1`。funding は固定間隔でないため）。
- **funding は best-effort**: 取得不可/未対応でも OHLCV は止めず、結果に
  `funding_supported: bool` / `funding_error: str|None` を返す。

## live smoke（手動・任意、CI 非必須）

public エンドポイントのみ。APIキー不要。

```bash
uv run python -m src.data.rest_backfill --symbol BTCUSDT --timeframe 1m --days 1
uv run python -m src.data.rest_backfill --symbol BTCUSDT --timeframe 1m --days 1 --with-funding
```

取得件数・期間・検出 gap を stdout に要約。CI には含めない（オフラインテストは synthetic
exchange を monkeypatch して検証）。
