# Vision and roadmap (owner-approved direction, 2026-09-26)

**Positioning:** the trading-agent research desk that shows its receipts. Every number traced to its source, only data that
existed at the time of the call, every call written to a tamper-evident ledger before the outcome is known. Working name
**Proofbook** (rename after S3 part 1 is live and trademark, domain, GitHub and PyPI checks pass).

Live progress page: https://claude.ai/artifact/CRzwYWDBvCC4UzdQYng6aA

## Phases
1. **Go live with S3 part 1** (T0–T4, T9–T11): the point-in-time record starts. Production is Neon Postgres: test migrations
   on a Neon branch and back up with a branch before deploying.
2. **Finish S3**: T5, T12, T6, T8, T7, T13, T14, T15. Exit: 4 weekly discrepancy reports in a row, badge on every report.
   T12 supplies the paper's forward-only results on the user track-record page and the public summary endpoint.
3. **Recognition**: arXiv paper on claim-level provenance + point-in-time data + hash-chained call records; publish
   LiveTradeBench results; ask to join Agent Market Arena; score on InvestorBench; open-source `proofbook` CLI (pip).
4. **Retail adoption**: public track-record page checkable against the ledger; free tier; broker and platform partnerships.
5. **Institutional trust**: point-in-time signal API; 12–36 months live, risk-adjusted, factor-attributed, third-party
   verified. A securities lawyer and the owner's immigration attorney sign off before any paid, advisory or investor-facing launch.

## Where we fit
- Open-source agent frameworks (TradingAgents, ai-hedge-fund, FinRobot): no live verifiable track record.
- Commercial stock scores (Zacks, Seeking Alpha Quant, TipRanks, Danelfin, Kavout, Stockopedia): mostly backtests or
  self-reported; no per-number provenance or tamper-evident record. Danelfin runs an "AI Audit" page: study it.
- Live LLM arenas (TradeRank, Alpha Arena): pre-selected models, short seasons, raw returns, mostly crypto. Use as a
  reference, do not compete there.

## Not in scope during S3
Rust/Go rewrite (no measured hot path; Python ecosystem and 900+ tests are the asset). CLI and rename come after S3 part 1.
