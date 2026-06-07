"""Data ingestion layer.

Implemented:
- ``rest_backfill`` — Bitget **public** REST OHLCV/funding backfill into parquet
  (no API key, no orders). The historical-data foundation for the backtester.

Stubs (later phases): Public/Private WebSocket residency, 1m candle building,
order/fill/position sync.
"""
