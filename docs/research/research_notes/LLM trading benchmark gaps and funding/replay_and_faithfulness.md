# Replay-based LLM finance benchmarks, trading agents, and faithfulness/calibration/look-ahead research (2023-2026)

Compiled 2026-10-13. Sources are arXiv abstract and HTML pages and GitHub API output fetched today. The HTML full-text fetches were truncated at about 100k characters, so appendix details may be missing. **Citation counts:** the Semantic Scholar API rate-limited almost every request (HTTP 429). Only Trading-R1 = 18 and FinGPT = 513 (as of 2026-10-13) came back. Every other citation count is UNVERIFIED. GitHub stars and licenses come from `gh api repos/...` on 2026-10-13.

## (a) Per-item fact sheets: what do replay benchmarks and trading frameworks control for?

### Takeaway
Every major trading-agent paper in scope evaluates by historical replay or backtest, except the newer live arenas (LiveTradeBench, Agent Market Arena, DeepFund). Most replay test windows fall before the backbone model's knowledge cutoff. Leakage controls amount to "only data available up to each day", with no control for what the model memorized. Formal significance tests and transaction costs are rare. Negative controls (shuffled labels, random-signal agents, post-cutoff re-runs) are absent in every paper reviewed.

### Cited Findings

**TradingAgents (TauricResearch, arXiv 2412.20138)**
- Replay. The backtest window is Jan 1 to Mar 29, 2024. Table 1 covers AAPL, GOOGL and AMZN. Backbones are gpt-4o-mini/gpt-4o ("quick-thinking") and o1-preview ("deep-thinking"). — [arXiv HTML](https://arxiv.org/html/2412.20138)
- Leakage control: "Agents make decisions based solely on data available up to each trading day". There is no memorization check, and model cutoffs are not stated. Note (my inference): the 2024 Q1 test window is close to or inside the training data of o1-preview/gpt-4o, whose cutoffs are not stated in the paper. — [arXiv HTML](https://arxiv.org/html/2412.20138)
- Results: AAPL CR 26.62% / Sharpe 8.21. GOOGL CR 24.36% / Sharpe 6.39. AMZN CR 23.21% / Sharpe 5.60. A footnote says the Sharpe exceeds the authors' expected range and that they "benchmarked TradingAgents over 3 months due to intensive LLM and tool use". — [arXiv HTML](https://arxiv.org/html/2412.20138)
- No transaction costs, no statistical tests or CIs, and no repeated runs reported. Baselines are 5 rule-based strategies (Buy&Hold, MACD, KDJ+RSI, ZMR, SMA), not other LLMs. No negative controls. Explainability is argued from a single sample log, not measured. — [arXiv HTML](https://arxiv.org/html/2412.20138)
- Repo: github.com/TauricResearch/TradingAgents, **110,876 stars, Apache-2.0** (gh api, 2026-10-13). Citation count UNVERIFIED (S2 rate-limited).

**Trading-R1 (arXiv 2509.11420, TauricResearch)**
- Replay. "The backtest covers June 1 to August 31, 2024, a held-out period excluded from the training data". The paper also states the corpus spans "January 1, 2024, to May 31, 2025, across 14 major tickers". The backtest window falls inside that stated corpus range, and the paper does not explain the split, so the "held-out" claim is unverified. — [arXiv HTML](https://arxiv.org/html/2509.11420)
- Backbone is Qwen3-4B, with distillation from o3-mini, o4-mini and GPT-4.1-nano. No backbone cutoff is stated. Labels are 3/7/15-day forward returns. Leakage claim: "Trades are executed using only information available up to each trading day, eliminating look-ahead bias". — [arXiv HTML](https://arxiv.org/html/2509.11420)
- 13 models/variants compared. No transaction costs, statistical tests, CIs or seeds reported in the visible text, and no negative controls. The authors themselves frame backtests "as evidence of the model's ability to generate well-grounded reports". The NVDA Sharpe in the text (1.88) disagrees with the table (2.72). — [arXiv HTML](https://arxiv.org/html/2509.11420)
- Repo: github.com/TauricResearch/Trading-R1, **505 stars, no license file detected** (gh api). **Semantic Scholar citations: 18** (API, 2026-10-13).

**InvestorBench (arXiv 2412.18174)**
- Replay over three environments. Stocks (7 tickers): warm-up 2020-07-01 to 2020-09-30, test 2020-10-01 to 2021-05-06. Crypto (BTC, ETH): test 2023-04-05 to 2023-11-05. ETFs: test 2020-01-02 to 2020-09-21. — [arXiv HTML](https://arxiv.org/html/2412.18174)
- 13 LLMs compared: gpt-4, gpt-4o, o1-preview, Palmyra-Fin-70B, DeepSeek, Llama-3.1, Yi-1.5, Qwen2.5. Results are the "median CR, SR, AV, and MDD from five repeated epochs" at temperature 0.6. No CIs or significance tests. Knowledge cutoff and leakage are not discussed, and transaction costs are not mentioned. Data "shared under the MIT license". — [arXiv HTML](https://arxiv.org/html/2412.18174)
- Inference: every test window predates the knowledge cutoffs of all 13 models, so it falls inside pre-training data by construction. Repo URL not in the visible text. Citation count UNVERIFIED.

**FinCon (arXiv 2407.06567, NeurIPS 2024)**
- Replay. "FinCon is trained from January 3, 2022, to October 4, 2022, and tested from October 5, 2022, to June 10, 2023." "All LLM-based agents use GPT-4-Turbo, with temperature set at 0.3." — [arXiv HTML](https://arxiv.org/html/2407.06567)
- Median of 5 epochs. The Table 2 caption says top results were tested with a **Wilcoxon signed-rank test** (the only formal test in this set). Baselines include Buy&Hold, FinRL DRL agents, Generative Agent, FinGPT, FinMem and FinAgent. No transaction costs or leakage discussion. — [arXiv HTML](https://arxiv.org/html/2407.06567)
- Headline: Portfolio 1 CR 113.84% / SR 3.269. TSLA single-stock CR 82.87%. — [arXiv HTML](https://arxiv.org/html/2407.06567)
- Inference (UNVERIFIED in this session): GPT-4-Turbo's published training cutoff is late 2023, after the whole test window. If so, the test period is pre-cutoff.

**FinMem (arXiv 2311.13743)**: UNVERIFIED in this session (not fetched). Replay agent with layered memory, built on GPT-class backbones. The repo pipiku915/FinMem-LLM-StockTrading has **963 stars, MIT** (gh api). FinCon uses FinMem as a baseline. Test window, controls and citations not verified.

**FinBen / PIXIU (arXiv 2402.12659, The-FinAI)**: UNVERIFIED in this session (not fetched). It is a broad static benchmark with a small trading task. The repo The-FinAI/PIXIU has **893 stars, MIT** (gh api). Citation count UNVERIFIED.

**FinGPT / FinRobot**: FinGPT has 513 S2 citations (API). Repo AI4Finance-Foundation/FinGPT: **21,396 stars, MIT**. FinRobot: **8,204 stars, Apache-2.0** (gh api). Both are frameworks/models, not forward-evaluated benchmarks. Evaluation details not fetched.

**AlphaForgeBench (arXiv 2602.18481, v2 27 May 2026; Zhang, ..., Bo An, Shuo Sun)**
- Replay. LLMs generate executable alpha-factor code, which is run in a deterministic backtest. Test window 2021-01-01 to 2026-01-01 on BTC, ETH, AAPL, GOOGL, MSFT, NVDA and TSLA. — [arXiv HTML v2](https://arxiv.org/html/2602.18481v2)
- 6 LLMs: claude-sonnet-4.5, deepseek-v3.2, gemini-3-flash/pro-preview, gpt-5.2, grok-4.1-fast. One-way transaction cost of 0.1%, with no slippage. k=5 generations per query, reported as mean ± std, with no formal tests. — [arXiv HTML v2](https://arxiv.org/html/2602.18481v2)
- Motivating claims about direct-trading agents: "LLMs manifest extreme run-to-run variance", and "Even with temperature=0 (deterministic decoding), LLMs generate completely different trading action sequences". It calls live evaluation a "reproducibility crisis" because results "are bound to the specific execution time window". No knowledge-cutoff or leakage control was visible, even though most of the 5-year window predates every model's cutoff. — [arXiv HTML v2](https://arxiv.org/html/2602.18481v2)
- Project page: https://finbrain-lab-hkustgz.github.io/AlphaForgeBench. Code license not visible. Citations UNVERIFIED.

**Follow-ups, 2025-2026 (live or leakage-aware)**
- **LiveTradeBench (arXiv 2511.03628)**: live streaming, not backtest, from Aug 18 to Oct 24, 2025 (50 trading days). 21 LLMs on US stocks and Polymarket. Transaction costs not modeled. No significance tests. Findings: "high LMArena scores do not imply superior trading outcomes", and "Large reasoning models do not confer trading advantages". Repo: github.com/ulab-uiuc/live-trade-bench. — [arXiv HTML](https://arxiv.org/html/2511.03628v1)
- **Agent Market Arena (arXiv 2510.11695)**: described as the first lifelong, real-time multi-market benchmark. It reports that agent frameworks drive behavior more than backbones do (from search-result summary, not fetched in full). — [arXiv](https://arxiv.org/pdf/2510.11695)
- **DeepFund (arXiv 2503.18313)**: live arena with multi-agent fund roles (search summary only). — [arXiv](https://arxiv.org/html/2503.18313v2)
- **StockBench**: self-described "contamination-free" multi-month trading benchmark scored on CR, MDD and Sortino (search summary only). — [CatalyzeX](https://www.catalyzex.com/paper/stockbench-can-llm-agents-trade-stocks)
- **FINSABER (arXiv 2505.07078)**: a backtest of about 20 years and more than 100 symbols. Prior LLM-strategy advantages "deteriorate significantly under broader cross-section and over a longer-term evaluation". It attributes prior overstatement to "survivorship and data-snooping biases". — [arXiv](https://arxiv.org/abs/2505.07078)
- **Profit Mirage / FinLake-Bench / FactFin (arXiv 2510.07920)**: back-test gains of most LLM financial agents fade "after the model's knowledge window closes". The paper releases "a leakage-robust evaluation benchmark" and uses counterfactual perturbations. No headline numbers in the abstract. — [arXiv](https://arxiv.org/abs/2510.07920)

### Inferences
- The replay benchmarks (InvestorBench, TradingAgents, FinCon, Trading-R1, AlphaForgeBench) all have test windows that likely overlap their backbones' pre-training data. None runs a post-cutoff re-test or a memorization probe as a control.
- Negative controls (random agent, shuffled news, date-masked or placebo inputs) appear in none of the papers reviewed.
- Transaction costs: only AlphaForgeBench models them (0.1%). LiveTradeBench explicitly omits them.
- Statistical testing: only FinCon reports a significance test (Wilcoxon). The rest report medians or means over at most 5 runs, or a single run.

### Gaps
- Citation counts for everything except Trading-R1 and FinGPT (S2 429). FinMem and FinBen windows and controls were not fetched. The AlphaForgeBench appendix (code license, direct-trading variance numbers) was truncated.

## (b-i) Has anyone measured unsupported or hallucinated numbers in trading-agent rationales against the exact inputs the agent saw?

### Takeaway
No peer-reviewed or preprint study found checks every number or fact in a forward or live trading agent's rationale against its logged inputs. Faithfulness work in finance is almost entirely static QA or document benchmarks (FinanceBench, FAITH, FinGround). The closest item is one 2026 single-author preprint that uses a conservative "claim overreach" proxy on replayed trajectories.

### Cited Findings
- FinanceBench has 10,231 questions. "GPT-4-Turbo used with a retrieval system incorrectly answered or refused to answer 81% of questions." 16 configurations were evaluated on a 150-case sample with manual review (n=2,400). Static QA. — [arXiv 2311.11944](https://arxiv.org/abs/2311.11944)
- FAITH (arXiv 2508.05201) tests intrinsic tabular hallucination by masking numbers in annual reports and asking models to recover them. It is a benchmark, not a runtime audit. — [arXiv](https://arxiv.org/pdf/2508.05201)
- FinGround (arXiv 2604.23588) splits answers into atomic claims typed as numerical, temporal or computational, re-computes the arithmetic, and reports 93.2% faithfulness on regenerated claims (search summary). — [arXiv](https://arxiv.org/pdf/2604.23588)
- "When Financial Fine-tuning Fails" (arXiv 2609.04806) reports that domain fine-tuning can make models insert memorized figures regardless of the input (search summary, not fetched). — [arXiv](https://arxiv.org/html/2609.04806)
- "Hallucination Snowball" (arXiv 2608.14588) injects altered figures into multi-agent pipelines and checks outputs with a rule-based numeric matcher (search summary). — [arXiv](https://arxiv.org/pdf/2608.14588)
- Closest to trading agents: "Representation Signatures and Risk-Feedback Alignment in LLM Trading Agents" (Weicheng Xue, arXiv 2605.28850, v3 Aug 2026). The abstract covers audit tasks testing whether models "avoid claim overreach" and "false audits", using "replayable trajectories", "eight LLM trajectories" and a "51-stock intraday experiment". It states: "These results support a research claim, not a profitability claim." — [arXiv](https://arxiv.org/abs/2605.28850). A search-engine summary says its proxy counts unsupported external-context claims and rationales contradicting recorded price data, and is "not a full hallucination detector". That detail is unverified against the full text.
- TradingAgents argues explainability qualitatively from one sample log and does not measure it. Trading-R1 positions backtests as evidence of "well-grounded reports" but does not measure grounding. — [TradingAgents](https://arxiv.org/html/2412.20138); [Trading-R1](https://arxiv.org/html/2509.11420)

### Inferences
- Numeric-claim grounding of live, forward trading-agent rationales against the exact prompt and tool inputs appears unmeasured. That is an open gap. Existing methods (deterministic numeric matching, atomic-claim verification) are directly reusable for it.

### Gaps
- The full text of 2605.28850 was not read. Its exact metric and whether it is replay-only need confirmation. Older items (Kang & Liu 2023, "Deficiency of LLMs in finance", arXiv 2311.15548; FinDVer 2411.05764) were not fetched.

## (b-ii) Has anyone measured calibration of LLM stock-direction probabilities forward in time?

### Takeaway
No study found reports Brier or reliability scores for LLM stock-direction probabilities collected forward in time. Forward-in-time calibration work exists for prediction-market events (KalshiBench, Prophet Arena, Polymarket tests) and shows systematic overconfidence. The finance-specific calibration study (Chen et al. 2024) uses interval forecasts on historical returns.

### Cited Findings
- KalshiBench (arXiv 2512.16030): 300 Kalshi questions with outcomes "occurring after model training cutoffs". Reports "systematic overconfidence across all models". Claude Opus 4.5 had the best ECE (0.120), GPT-5.2-XHigh had ECE 0.395, and only one model had a positive Brier Skill Score. Topic mix not stated, so it may not be financial. — [arXiv](https://arxiv.org/abs/2512.16030)
- Chen, Green, Gulen, Zhou (arXiv 2409.11540): LLM 80% intervals for stock returns are "better calibrated than survey evidence but are pessimistic about outliers", and forecasts are "optimistic relative to historical and future realized returns". Whether the forecasts were made forward in time is not stated in the abstract. — [arXiv](https://arxiv.org/abs/2409.11540)
- An LLM vs. expert forecasters study (arXiv 2507.04562) shows calibration plots in which models are "typically overconfident" (search summary). — [arXiv](https://arxiv.org/pdf/2507.04562)
- Live Polymarket figures (vendor blog, not peer-reviewed): market Brier 0.17, Foresight-32B 0.199, calibration error cut from 19.2% to 6.0%. — [Lightning Rod blog](https://blog.lightningrod.ai/p/foresight-32b-beats-frontier-llms-on-live-polymarket-predictions)
- Log-probability estimator: Brier 0.186 vs. vanilla GPT-4o 0.236 vs. random 0.250, on general events. — [arXiv 2501.04880](https://arxiv.org/pdf/2501.04880)
- Related but not verified: "Ordinal Gates, Cardinal Bets: Matching LLM Confidence to the Financial Decision Operator" (arXiv 2609.00187). Amundi research finds self-declared confidence "strong biases" and that an entropy-based confidence does better (search summary). — [arXiv](https://arxiv.org/pdf/2609.00187); [Amundi](https://research-center.amundi.com/article/out-black-box-uncertainty-quantification-llms-conditional-probabilities)

### Inferences
- Forward calibration (Brier or reliability) of per-ticker up/down probabilities from trading agents appears to be an open gap. Live arenas (LiveTradeBench, AMA) score P&L, not probabilistic calibration.

### Gaps
- 2609.00187 and 2507.04562 were not read in full. A dedicated arXiv search for "directional accuracy" plus "ECE" in a stock setting might still find something.

## (b-iii) What do memorization and look-ahead studies say about backtesting LLMs on pre-cutoff dates?

### Takeaway
They agree that backtests on pre-cutoff dates are contaminated. LLMs recall exact financial values before their cutoff and show no recall after it. Prompting a model to ignore the future, or masking entity names, does not prevent recall. Measured predictive power concentrates on memorized firm-dates and vanishes after the cutoff. The remedies are post-cutoff or live evaluation, time-cutoff models, or decoding-time correction.

### Cited Findings
- Lopez-Lira, Tang, Zhu, "The Memorization Problem" (arXiv 2504.14765, v2 Dec 2025):
  - "LLMs cannot be trusted for economic forecasts during periods covered by their training data."
  - "Instructions to respect historical boundaries fail to prevent recall-level accuracy."
  - Masking fails because LLMs "reconstruct entities and dates from minimal context."
  - "Post-cutoff, we observe no recall."
  - "Memorization extends to embeddings."

  — [arXiv](https://arxiv.org/abs/2504.14765)
- Gao, Jiang, Yan, "Detecting Lookahead Bias in LLM Forecasts" (arXiv 2512.23847, v2 Jun 2026): proposes the "Lookahead Propensity" (LAP) score, which "collapses essentially to zero right after the training-data cutoff". Predictive power is larger on high-LAP firm-dates for news→returns and earnings calls→capex. — [arXiv](https://arxiv.org/abs/2512.23847). A search summary attributes to it "roughly 37% of the apparent predictive effect" being memorization-amplified (Llama-3.3). Not confirmed in the abstract.
- Li, Wang, Ma, FinCAD, "Summoning the Oracle to Slay It" (arXiv 2605.24564, EMNLP 2026 Main):
  - Defines "parametric look-ahead bias": "An LLM trained in 2024 may already encode how stocks moved during 2018-2020."
  - Decoding correction cuts in-sample returns by up to -67.1%. 2025 out-of-sample Sharpe stays within ±0.10.
  - The in-sample/out-of-sample rank Spearman rises from 0.779 to 0.846.
  - Covers 5 models of 7-14B parameters and 5 mega-caps.

  — [arXiv](https://arxiv.org/abs/2605.24564)
- Profit Mirage (arXiv 2510.07920): agents' back-test gains fade after the knowledge window, attributed to "the inherent information leakage in LLMs". — [arXiv](https://arxiv.org/abs/2510.07920)
- Merchant & Levy (arXiv 2512.06607) propose inference-time logit adjustment with forget/keep models as an alternative to retraining time-cutoff models (search summary). — [arXiv](https://arxiv.org/html/2512.06607v1)
- Not fetched this session (UNVERIFIED): Glasserman & Lin 2023, look-ahead bias in GPT sentiment (arXiv 2309.17322). ChronoBERT/ChronoGPT time-cutoff models (arXiv 2502.21206). Time Machine GPT (arXiv 2404.18543).

### Inferences
- The replay results in section (a) are pre-cutoff by construction, for example InvestorBench 2020-2021 and FinCon 2022-23. Under these studies their returns cannot be separated from memorization. Only live or post-cutoff windows (LiveTradeBench, AMA, DeepFund) avoid this. Even those carry a residual risk: tool-retrieved news may itself be contaminated or misdated.

### Gaps
- Exact effect sizes in 2504.14765 and 2512.23847 (full text not read). Knowledge-cutoff dates for each backbone were not verified in this session.
