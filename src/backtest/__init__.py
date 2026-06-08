"""Event-driven backtester (Issue Phase 2).

Strategy-agnostic: entries are injected as ``EntryIntent`` (rule-based generation lives
in a later phase). Simulates realistic execution — partial TP1/TP2, SL-to-breakeven,
2R trailing, fee/slippage/funding costs, and **conservative intra-candle TP/SL
collision** (SL wins). Outputs reuse ``storage.models.TradeRecord``.

Intra-candle ordering is fixed and conservative: ``SL -> TP1 -> TP2 -> trailing``.
"""
