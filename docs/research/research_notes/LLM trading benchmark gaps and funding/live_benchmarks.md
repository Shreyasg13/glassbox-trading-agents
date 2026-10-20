# Live/forward and point-in-time LLM trading and portfolio-agent benchmarks (2024-2026): per-benchmark fact sheet

Scope note: The sources are arXiv HTML full text (read through the WebFetch summarizer), GitHub READMEs, and a few news pages. The research stopped at about 20 tool calls, so long papers were read only partly; places where the text was truncated are marked. "Not reported" means the text I read does not mention the feature. It does not prove the feature is absent from appendices I did not read. Anything marked "not verified" came from a secondary source only, or could not be checked.

## Q1: Per-benchmark fact sheets

### Takeaway
Every benchmark below says it controls leakage. Some do it with live data (AMA, LiveTradeBench, DeepFund, Alpha Arena, the PortBench real-time leg, the CLQT live leg). Others enforce point-in-time access in code (CLQT, OpenPM, PolyBench). Only PortBench reports bootstrap CIs and FDR correction. Only CLQT has a hash chain, and it is not externally anchored. Only OpenPM has a random agent and a contamination certificate. None pre-registers its claims, checks numbers in rationales against source data, or measures calibration with Brier scores.

### Cited Findings

**StockBench (arXiv 2510.02209)**
- Authors: Yanxu Chen, Zijun Yao, Yantao Liu, Amy Xin, Lei Hou, Juanzi Li (Tsinghua) and Jin Ye, Jianing Yu (BUPT). The version read is v2 of 02 Mar 2026. Repo: github.com/ChenYXxxx/stockbench, with no code license stated. Project page: stockbench.github.io. — [arXiv HTML](https://arxiv.org/html/2510.02209)
- Historical "back-trading environment", not live. Window: "March 3, 2025 to June 30, 2025" (82 trading days). Leakage control is only an assertion: "This period also falls after the knowledge cutoff of mainstream LLMs, ensuring no data leakage." — [arXiv HTML](https://arxiv.org/html/2510.02209)
- About 14 models: Kimi-K2, Qwen3 variants, GLM-4.5, GPT-5, o3, Claude-4-Sonnet, DeepSeek-V3 and V3.1, GPT-OSS-120B and 20B. Universe: "top 20 DJIA stocks". Daily decisions at market open, $100k starting cash. — [arXiv HTML](https://arxiv.org/html/2510.02209)
- Statistics: "each LLM agents would be run three times with different random seeds, and the average performance is reported". No CIs, bootstrap, permutation tests or deflated Sharpe. — [arXiv HTML](https://arxiv.org/html/2510.02209)
- Baseline: passive equal-weight buy-and-hold only. Costs: the paper does "not model trading costs, slippage, or liquidity limits." No audit hashing, pre-registration, faithfulness check, calibration or leaderboard is reported. — [arXiv HTML](https://arxiv.org/html/2510.02209)

**Agent Market Arena, AMA (arXiv 2510.11695)**
- Authors: Lingfei Qian, Xueqing Peng, Jimin Huang (corresponding) and others at The Fin AI, plus Stevens, Columbia, Georgia Tech, Harvard, U. Florida (Lopez-Lira), U. Montréal and U. Manchester (Ananiadou). The version read is v2 (30 Oct 2025). No AMA code repo is given. The paper is CC BY 4.0. — [arXiv HTML](https://arxiv.org/html/2510.11695)
- Live: "Real-time evaluation begins on 2025-08-01", with results through 2025-09-30 and a warm-up from 2025-05-01 to 07-31. It describes no explicit leakage test beyond being live. — [arXiv HTML](https://arxiv.org/html/2510.11695)
- 5 backbones (GPT-4o, GPT-4.1, Gemini-2.0-flash, Claude-3.5-haiku, Claude-sonnet-4) plus a majority-vote ensemble. 4 agents (InvestorAgent, TradeAgent, HedgeFundAgent, DeepFundAgent). Assets: TSLA, BMRN, BTC, ETH. One BUY/SELL/HOLD decision per day. — [arXiv HTML](https://arxiv.org/html/2510.11695)
- No CIs, seeds or significance tests. Baseline: buy-and-hold only. Trades are "logged through the AMA evaluation pipeline", with no hashing. The dashboard shows "cumulative return (with and without fees)" but the paper gives no fee model. It has a public live leaderboard at https://ama.thefin.ai/live. — [arXiv HTML](https://arxiv.org/html/2510.11695)

**LiveTradeBench (arXiv 2511.03628)**
- Authors: Haofei Yu, Fenghai Li, Jiaxuan You (UIUC), 05 Nov 2025. Repo: github.com/ulab-uiuc/live-trade-bench, PolyForm Noncommercial 1.0.0 (a commercial license is offered separately), 168 stars when checked. Site: trade-bench.live. — [arXiv HTML](https://arxiv.org/html/2511.03628v1); [GitHub](https://github.com/ulab-uiuc/live-trade-bench)
- Live: "from August 18, 2025, to October 24, 2025—a total of 50 trading days". Leakage control: "Live data streaming of market prices and news, eliminating dependence on offline backtesting and preventing information leakage." — [arXiv HTML](https://arxiv.org/html/2511.03628v1)
- 21 LLMs, including Claude Sonnet-3.7, Opus-4, Sonnet-4 and Opus-4.1, Grok-3 and 4, Qwen, Llama 3.3 and 4, GPT-4o, 4.1, 5 and o3, Gemini-2.5 Flash and Pro, Kimi-K2, DeepSeek-V3.1 and R1. Universe: 15 US stocks plus cash, and 10 Polymarket binary markets. Cadence: daily. — [arXiv HTML](https://arxiv.org/html/2511.03628v1)
- Statistics: Spearman correlations ("the Spearman correlation between LMArena scores and cumulative returns is only 0.054") and 25-75% bands across models. No seeds, CIs or significance tests. No random baseline; a "rolling-k delta" analysis tests whether agents act randomly. Costs are excluded: the environment does "not account for transaction fees, bid–ask spreads, liquidity constraints". No hashing, pre-registration or calibration. The paper does not call trade-bench.live a leaderboard. — [arXiv HTML](https://arxiv.org/html/2511.03628v1) (first 100k of 136k characters read)

**DeepFund ("Time Travel is Cheating", arXiv 2505.11065; NeurIPS 2025 Datasets and Benchmarks)**
- Authors: Changlun Li, Yao Shi, ... Nan Tang, Yuyu Luo (HKUST-Guangzhou and Paradoox AI Research). The version read is v2 (14 Oct 2025). Repo: github.com/HKUSTDial/DeepFund; the paper states MIT for the code. Star count not checked. — [arXiv HTML](https://arxiv.org/html/2505.11065v2); [NeurIPS poster](https://neurips.cc/virtual/2025/poster/121642)
- Live from March 17 to April 17, 2025 (24 trading days), extended to Q2 2025 in an appendix. It uses "data published after each model's pretraining cutoff", but lists cutoffs as NA for 3 of the 9 models. — [arXiv HTML](https://arxiv.org/html/2505.11065v2)
- 9 LLMs: GPT-4.1, Llama 4 Scout, Gemini 2.5 Flash, Claude 3.7 Sonnet, Grok 3 mini, DeepSeek-V3, Qwen2.5-Max, Doubao-1.5-pro, GLM-4-Air. Universe: AAPL, AXP, BAC, KO, CVX (an appendix uses AMT instead of CVX, unexplained). Daily cadence. — [arXiv HTML](https://arxiv.org/html/2505.11065v2)
- No CIs or seeds; temperature is 0.5. Logs go to Supabase with a claim of "complete traceability", but no hashing. Baselines are buy-and-hold and the S&P 500. Fees are not modeled (stated in Limitations). Live leaderboard: https://deepfund.paradoox.ai/. — [arXiv HTML](https://arxiv.org/html/2505.11065v2)

**AI-Trader (HKUDS, HKU Data Intelligence Lab)**
- Repo: HKUDS/AI-Trader, MIT, about 22.8k stars when checked. It now calls itself "AI-Trader: 100% Fully-Automated Agent-Native Trading", a live platform (ai4trade.ai) with paper trading on simulated capital across stocks, crypto, forex, options, futures and Polymarket. — [GitHub](https://github.com/HKUDS/AI-Trader)
- The README states no date window, leakage control, compared models, statistics, audit logging, baselines or transaction costs. The changelog refers to leaderboards. No arXiv ID appears on the README page. — [GitHub](https://github.com/HKUDS/AI-Trader)
- Not verified: whether an AI-Trader arXiv paper exists and what it says. CLQT's comparison table lists "AI-Trader" as a benchmark ([CLQT HTML](https://arxiv.org/html/2606.29771)), but I did not find or read a paper.

**PortBench (arXiv 2605.27887)**
- Authors: Yuxuan Zhao, Sijia Chen, Ningxin Su (Yantai Research Institute of Harbin Engineering University and HKUST-Guangzhou). The version read is v6, 09 Oct 2026. Repo: github.com/AgenticFinLab/portbench, with no code license stated. — [arXiv HTML](https://arxiv.org/html/2605.27887)
- Hybrid. The historical part is "replayed point-in-time" over Jan 2015 to Dec 2025, with stress windows in 2015, 2020 and 2022. A real-time leg ran 12 trading days, 2026-07-16 to 07-31, where "the model receives only data available through the previous close." The authors concede historical replay "risks leakage". — [arXiv HTML](https://arxiv.org/html/2605.27887)
- 10 LLMs, all Chinese-lab: Kimi-K2.6, Qwen3.7-Max, Qwen3.6-Plus, Qwen3.6-35B-A3B, GLM-5.1, Doubao-Seed-2.0 Lite and Pro, Hunyuan3-Preview, DeepSeek-V4 Flash and Pro. 183 series across 6 asset classes. Cadence: monthly normally, weekly in stress windows, daily in the real-time leg. — [arXiv HTML](https://arxiv.org/html/2605.27887)
- Statistics are the strongest of the set: "Percentile intervals use 10,000 paired model-bootstrap draws". Circular moving-block bootstrap. Benjamini–Hochberg FDR on association tests. The authors state "Intervals ... are not adjusted for multiple comparisons." Fixed seed 20261006. — [arXiv HTML, App. C.5/F.8](https://arxiv.org/html/2605.27887)
- Costs: "Commission of 5 bps and slippage of 10 bps are charged on the absolute changes in security weights." Baselines: EqW, 60/40, risk parity, CovRiskPar, MinVar, Black-Litterman; no random baseline. Logging is a "stateful sandbox" with no hashing. No pre-registration, faithfulness check, calibration or leaderboard found. The final 15k characters were not read. — [arXiv HTML](https://arxiv.org/html/2605.27887)

**CLQT (arXiv 2606.29771)**
- Authors: Bo Qu (Independent Researcher) and Mingguang Chen (DeepGrounding). v1 29 Jun 2026, v3 12 Sep 2026. CC BY 4.0. No public repo; the artifacts are "available on request (Appendix G)". — [arXiv abs](https://arxiv.org/abs/2606.29771); [arXiv HTML](https://arxiv.org/html/2606.29771)
- Point-in-time backtest of 26 bi-weekly rounds, 2025-06-16 to 2026-06-12, plus a four-week broker paper-trading live leg ("eighteen valid NYSE days"; exact dates not in the text read). A "hard TimeGate" enforces "∀d∈𝒟t: timestamp(d) ≤ t_as_of". It filters by per-model cutoff and uses point-in-time universe membership. Canary and future-shift leakage probes exist but "were not exercised in this single, benign campaign." — [arXiv HTML](https://arxiv.org/html/2606.29771)
- 5 models (qwen3-235b, gemini-2.5-flash-lite, deepseek-chat-v3.1, gpt-5-mini, claude-haiku-4.5), each run in structured and autonomous modes. The universe is 72 symbols derived from the S&P 100. — [arXiv HTML](https://arxiv.org/html/2606.29771)
- Statistics: 5 seeds per reliable configuration and a ±0.42 Sharpe noise band. The authors say "bootstrapped CIs and Kendall-τ rank-stability are a planned broader-run pass" (i.e., not done). — [arXiv HTML](https://arxiv.org/html/2606.29771)
- Audit: each round is sealed with content hashes of the inputs and the decision, folded into the next round's hash. The authors disclaim that "it is not an externally anchored cryptographic signature and does not provide non-repudiation against wholesale replacement and recomputation of the store." — [arXiv HTML](https://arxiv.org/html/2606.29771)
- Rationale-action consistency: a "Coherence" axis combines mechanical signal-action agreement with a held-out LLM judge, plus a blinded human-anchoring study in v3. The abstract says "allocations often diverge from the agents' own stated analysis" (I paraphrased the abstract; this exact wording is not verified). There is no check of numbers inside rationales against data. — [arXiv abs](https://arxiv.org/abs/2606.29771); [arXiv HTML](https://arxiv.org/html/2606.29771)
- Costs: four market-impact models, commission, spread, slippage and financing accrual; no taxes. Baselines are passive or rule-based only (SPY, IEF, 60/40, EW, risk parity, 12-1 momentum); no random agent. It has no leaderboard and argues against leaderboard ranking. No pre-registration or calibration. The final 67k of 167k characters were not read. — [arXiv HTML](https://arxiv.org/html/2606.29771)

**OpenPM (arXiv 2608.09988)**
- Authors: Xinying Cai, Minghao Guo, ... Dimitris N. Metaxas, Raymond Li, from Rutgers, DTU, NJIT, NYU, Columbia, UIUC and UBC. v1 6 Aug 2026. Code and data: github.com/aslcai/OpenPM-Bench and HF datasets/aslcai/OpenPM-Bench, with no code or data license stated. Star count not checked. — [arXiv abs](https://arxiv.org/abs/2608.09988); [arXiv HTML](https://arxiv.org/html/2608.09988)
- Point-in-time replay (not live) over a single frozen window, 2026-03-02 to 2026-05-01 (44 days). The S&P 500 long-only $1M book uses 5-minute bars. Each run gets a contamination certificate (PASS/FAIL/UNKNOWN); "PASS for all five constructors". The thinnest cutoff margin is Opus-4.7, around 2026-01. — [arXiv HTML](https://arxiv.org/html/2608.09988)
- Constructors: gpt-5, claude-opus-4.7, DeepSeek-V3.2, qwen3-max, gpt-4o-mini. Analysts: DeepSeek-V3.2 and gpt-5. Rebalancing is hourly, daily or once-then-hold. — [arXiv HTML](https://arxiv.org/html/2608.09988)
- Statistics: 3 seeds per analyst backbone; Random-N baseline with 10 seeds. The authors state "We therefore report relative ordering and point estimates, not calibrated effect sizes or statistical alpha." — [arXiv HTML](https://arxiv.org/html/2608.09988)
- Audit: content hash of the frozen data snapshot, capture hashes, a run manifest (config and tokens), per-stage logs, a constraint-adherence report, a backtest-bias checklist and redacted prompt logs. No signing or tamper-evidence. — [arXiv HTML](https://arxiv.org/html/2608.09988)
- Negative-control-style baselines: Random-N, always-flat cash, equal-weight, inverse-vol, SPY. Costs: ask/bid fills with a half-spread on IEX quotes, and a cost-sensitivity curve from 0 to 2x. No slippage or impact, no taxes. Claims were "verified by the authors against the saved run artifacts", which is manual, not automated rationale checking. No calibration or leaderboard. — [arXiv HTML](https://arxiv.org/html/2608.09988)

**PolyBench (arXiv 2604.14199)**
- Authors: Pu Cheng, Juncheng Liu, Yunshen Long (Sichuan University), 3 Apr 2026. Repo: github.com/PolyBench/PolyBench. The paper is CC BY-NC-SA 4.0. Star count not checked. — [arXiv abs](https://arxiv.org/abs/2604.14199); [arXiv HTML](https://arxiv.org/html/2604.14199)
- Uses point-in-time live Polymarket snapshots collected Feb 6-12, 2026, covering 38,666 binary markets across 4,997 events, with order books and news. Leakage control: unresolved events plus "all models' knowledge cut-off date is before the benchmark." — [arXiv HTML](https://arxiv.org/html/2604.14199)
- 7 LLMs: GPT-OSS-120B, DeepSeek-V3.2, MiMo-V2-Flash, MiniMax-M2.5, Trinity-Large-Preview, Gemini-3-Flash-Preview, Grok-4.1-Fast. They made 36,165 predictions. Only MiMo-V2-Flash (17.6% CWR) and Gemini-3-Flash (6.2% CWR) had positive returns. — [arXiv abs](https://arxiv.org/abs/2604.14199)
- Calibration was rejected explicitly: "we do not adopt traditional proper scoring rules such as the Brier Score". It uses a Confidence-Weighted Return instead. There are no CIs or seeds, no random baseline, and no fees (order-book slippage only). No hashing, pre-registration or leaderboard. — [arXiv HTML](https://arxiv.org/html/2604.14199)

**MarketSenseAI 2.0 (arXiv 2502.00415)**
- Authors: George Fatouros, Kostas Metaxas, John Soldatos, Manos Karathanassis (Alpha Tensor Technologies, Innov-Acts, KM Cube Asset Management). v2 03 Oct 2025. No code repo; commercial site at marketsense-ai.com. — [arXiv HTML](https://arxiv.org/html/2502.00415v2)
- Historical backtest only: S&P 100 Jan 2023 to Dec 2024, and S&P 500 in 2024. GPT-4o, monthly signals. No leakage test, even though the training cutoff of GPT-4o overlaps the window (inference from the dates, not stated by the paper). — [arXiv HTML](https://arxiv.org/html/2502.00415v2)
- Costs: returns "after transaction costs (10bps/trade)". Ragas faithfulness of 0.86-0.98 applies only to the macro RAG retrieval, not to the trading rationales. No CIs, seeds, random baseline or leaderboard. Reported 125.9% vs 73.5% for the S&P 100. — [arXiv HTML](https://arxiv.org/html/2502.00415v2)

**Alpha Arena (Nof1)**: secondary sources only; not verified against a primary source (the nof1.ai blog URL I tried returned 404)
- Season 1, Oct 18 to Nov 3, 2025: six models (Qwen 3 Max, DeepSeek v3.1, GPT-5, Gemini 2.5 Pro, Claude Sonnet 4.5, Grok 4), each with $10,000 of real capital on Hyperliquid crypto perpetuals. Qwen 3 Max won with about +22%; GPT-5 ended near $3,733. — [GNcrypto (Russian)](https://www.gncrypto.news/ru/news/qwen-wins-alpha-arena-season-1-with-22-percent-returns/)
- Season 1.5 ended Dec 3, 2025. A "Mystery Model", later identified as Grok 4.20, won at about +12% on average across four competitions. Sources conflict on 2nd and 3rd place. Live leaderboard at nof1.ai/leaderboard. — [Forklog](https://forklog.com/en/ai-model-grok-4-2-triumphs-in-trading-tournament/amp); [OneDayAdvisor](https://www.onedayadvisor.com/2025/12/nof1ai-alpha-arena-review-season-15.html)
- Not verified: statistics, seeds and replicates, audit features, and fee handling.

**Other benchmarks named but not researched**: CLQT's Table 1 also lists InvestorBench, FINSABER, "When Agents Trade", TraderBench, OpenFinGym and NextFund. OpenFinGym, NextFund and OpenPM score 5/7 on CLQT's own feature grid, and CLQT scores itself 7/7. OpenPM's Table 1 adds FinBen, FinMem/TradingAgents, FinCon and QuantAgent. — [CLQT HTML](https://arxiv.org/html/2606.29771); [OpenPM HTML](https://arxiv.org/html/2608.09988)

### Inferences
- The field moved from asserting no leakage (StockBench, MarketSenseAI) to running live (DeepFund, AMA, LiveTradeBench, Alpha Arena) and then to machine-checked point-in-time access with artifacts (CLQT TimeGate, OpenPM contamination certificate).
- The live benchmarks are the weakest on statistics: one run, short windows (24-50 days), no CIs, no random agents.

### Gaps
- GitHub stars were checked only for AI-Trader (about 22.8k) and LiveTradeBench (168). They were not checked for StockBench, DeepFund, PortBench, OpenPM or PolyBench.
- AI-Trader paper: arXiv ID and content not verified.
- Alpha Arena: no primary-source methodology was read.
- Appendix tails were not read for CLQT (last 67k characters), PortBench (last 15k) and LiveTradeBench (last 36k).
- "FinSearchComp-style" live benchmarks and other LiveTradeBench variants were not searched for.

## Q2: Which integrity features exist in at least one benchmark, and which in none?

### Takeaway
Each feature that exists appears in only one or two benchmarks. Five features appear in none of the benchmarks examined: (1) pre-registration, (2) checking numbers or facts inside LLM rationales against source data, (3) proper-score calibration (Brier or reliability curves), (4) externally anchored or signed tamper-evident logs, and (5) deflated Sharpe or a correction for multiple strategies. Taxes are also modeled by none.

### Cited Findings
- **Live/forward evaluation:** DeepFund, AMA, LiveTradeBench, Alpha Arena (secondary sources), AI-Trader platform, the PortBench real-time leg, the CLQT live paper leg. — sources above
- **Enforced point-in-time access with a machine-checked leakage artifact:** CLQT (TimeGate, probes not exercised) and OpenPM (contamination certificate PASS/FAIL/UNKNOWN). — [CLQT](https://arxiv.org/html/2606.29771); [OpenPM](https://arxiv.org/html/2608.09988)
- **Bootstrap CIs:** PortBench only (10,000 paired model-bootstrap and moving-block bootstrap). — [PortBench](https://arxiv.org/html/2605.27887)
- **Multiple-testing correction:** PortBench only, as BH-FDR on Spearman and intervention tests. Its main intervals are explicitly "not adjusted for multiple comparisons". — [PortBench](https://arxiv.org/html/2605.27887)
- **Multiple seeds/runs:** StockBench (3), OpenPM (3 per backbone, 10 for Random-N), CLQT (5). — [StockBench](https://arxiv.org/html/2510.02209); [OpenPM](https://arxiv.org/html/2608.09988); [CLQT](https://arxiv.org/html/2606.29771)
- **Permutation or shuffle tests:** none found. CLQT plans bootstrap CIs but has not run them. — [CLQT](https://arxiv.org/html/2606.29771)
- **Deflated Sharpe:** none found in any benchmark read.
- **Hash chain:** CLQT only, and it is not externally anchored ("does not provide non-repudiation against wholesale replacement and recomputation of the store"). — [CLQT](https://arxiv.org/html/2606.29771)
- **Data snapshot hash plus run manifest:** OpenPM. — [OpenPM](https://arxiv.org/html/2608.09988)
- **Public anchors or signatures:** none found.
- **Pre-registration:** none found.
- **Negative control or random agent:** OpenPM only (Random-N with 10 seeds, plus always-flat cash). LiveTradeBench has a "rolling-k delta" randomness analysis, but no random agent. — [OpenPM](https://arxiv.org/html/2608.09988); [LiveTradeBench](https://arxiv.org/html/2511.03628v1)
- **Shuffled-signal controls:** none found.
- **Rationale-action consistency:** CLQT's Coherence axis (mechanical agreement plus LLM judge). — [CLQT](https://arxiv.org/html/2606.29771)
- **Checking numbers or facts inside rationales against source data:** none found. OpenPM did this manually ("verified by the authors against the saved run artifacts"). MarketSenseAI's Ragas faithfulness covers only the RAG retrieval. — [OpenPM](https://arxiv.org/html/2608.09988); [MarketSenseAI](https://arxiv.org/html/2502.00415v2)
- **Calibration:** none found. PolyBench explicitly declines the Brier score. — [PolyBench](https://arxiv.org/html/2604.14199)
- **Transaction costs:** CLQT (impact, spread, commission, financing), PortBench (5 bps commission + 10 bps slippage), OpenPM (spread and a 0-2x sensitivity curve), MarketSenseAI (10 bps). They are excluded in StockBench, LiveTradeBench, DeepFund and PolyBench. AMA's dashboard shows a with-fees view, but the paper gives no model. — sources above
- **Taxes:** none found.
- **Public live leaderboard:** AMA (ama.thefin.ai/live), DeepFund (deepfund.paradoox.ai), Alpha Arena (nof1.ai/leaderboard, not verified). LiveTradeBench has a live site (trade-bench.live) but does not call it a leaderboard. — [AMA](https://arxiv.org/html/2510.11695); [DeepFund](https://arxiv.org/html/2505.11065v2)

### Inferences
- No single benchmark combines live evaluation, bootstrap or permutation statistics, a random-agent control, and a tamper-evident log. The closest are CLQT (hash chain, costs, live leg, consistency axis, but no CIs or random agent) and OpenPM (certificate, manifest, random agent, but no CIs, not live, no signing).
- Unclaimed space: pre-registered claims, an externally anchored audit (e.g. a public timestamp or transparency log), automated checking of numbers in rationales against source data, Brier or reliability calibration for LLM trading confidence, deflated Sharpe, and tax-aware PnL.

### Gaps
- Absence claims are limited to the portions read; truncated appendices might mention some of these features.
- The CLQT and OpenPM authors' own comparison tables were not fully extracted cell by cell.
