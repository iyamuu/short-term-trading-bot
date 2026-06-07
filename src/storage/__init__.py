"""Storage / logging layer — the implemented core of Phase 0/1.

Responsibility split (kept deliberately separate so post-hoc analysis is possible):

- ``models``        — single source of truth for record schemas + enums.
- ``state_store``   — SQLite in-flight state (position/orders/risk) + log outbox.
- ``outbox``        — append-only outbox in SQLite, idempotently flushed to parquet.
- ``trade_log``     — finalized trade *summaries* (parquet).
- ``event_log``     — order/fill *facts* (parquet), from which a summary is rebuildable.
- ``candidate_log`` — rejected entry candidates with reject stage/reason/detail.
- ``parquet_writer``— date-partitioned, idempotent parquet append primitive.
"""
