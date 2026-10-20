# What makes a multi-model AI benchmark influential (2023-2026), and which of designs A-D to build

Research date: 2026-10-13. All claims carry an inline source. Items marked UNVERIFIED come from secondary or aggregator sources only. Citation counts could not be retrieved: the Semantic Scholar API returned HTTP 429 on every call, and OpenAlex returned implausible numbers (for example, 0 for ForecastBench). See the Gaps sections.

## Comparables: per-benchmark record

### ForecastBench (Forecasting Research Institute; Karger et al.)
- Launch: arXiv 2409.19839, first posted Sept 2024 ([arXiv](https://arxiv.org/pdf/2409.19839v3)). Published at ICLR 2025 ([ICLR proceedings](https://proceedings.iclr.cc/paper_files/paper/2025/hash/ea74e45a229dac70b5b63b28d8934db6-Abstract-Conference.html)).
- Design: "an automatically generated and regularly updated set of 1,000 forecasting questions" about future events with no known answers when submitted ([ICLR paper](https://proceedings.iclr.cc/paper_files/paper/2025/file/ea74e45a229dac70b5b63b28d8934db6-Paper-Conference.pdf)). Self-described as "a dynamic, contamination-free benchmark of LLM forecasting accuracy with human comparison groups" ([forecastbench.org](https://www.forecastbench.org/)).
- Live vs static: live. Contamination handling: questions resolve only after the forecast date.
- Leaderboards: a Tournament board that is "open to external submissions" and allows tools, fine-tuning and ensembling; a Baseline board where models run "out of the box"; models appear "50 days after forecast submission"; human comparison groups were last surveyed in July 2024 ([leaderboards page](https://www.forecastbench.org/leaderboards/)). The board's headline metric is the "Overall Brier Index" (same source). The site also tracks a linear-trend projection of when LLMs will reach superforecaster parity ([forecastbench.org](https://www.forecastbench.org/)).
- Launch results: superforecasters scored Brier 0.093, the general public 0.107 and the best LLM 0.111 ([EA Forum announcement](https://forum.effectivealtruism.org/posts/zwzgR8iuFEcJms3Hu/announcing-forecastbench-a-new-benchmark-for-ai-and-human)). Experts beat the top LLM with p < 0.001 (ICLR paper).
- Models at launch: 17 LLMs, including GPT-4o, Claude 3.5 Sonnet and Gemini 1.5 Pro (per a search summary of the paper).
- GitHub: forecastingresearch/forecastbench has 89 stars (GitHub API, fetched 2026-10-13).
- Adoption by frontier labs: I found no frontier-lab model card reporting ForecastBench (search returned none).

### LiveBench (White, Dooley, ... LeCun, Goldstein, Goldblum; Abacus.AI, NYU and others)
- Launch: arXiv 2406.19314, 27 June 2024. ICLR 2025 Spotlight ([arXiv](https://arxiv.org/abs/2406.19314)).
- Design: questions "added and updated on a monthly basis", drawn from recent math competitions, arXiv papers, news articles and datasets. Answers are scored automatically against objective ground truth, with no LLM judge (same source).
- Models: "many prominent closed-source models" plus "dozens of open-source models ranging from 0.5B to 405B" (same source).
- Live vs static: periodically refreshed. Contamination handling: recency of question sources.
- GitHub: LiveBench/LiveBench has 1,342 stars (GitHub API, 2026-10-13).
- Adoption: lab citations of LiveBench in model cards are UNVERIFIED (not checked this session).

### Chatbot Arena / LMArena (LMSYS, UC Berkeley, later spun out as LMArena)
- Design: crowdsourced pairwise human preference votes aggregated into Elo/Bradley-Terry ratings. Live, with a public leaderboard.
- GitHub: lm-sys/FastChat, which hosts the original arena code, has 39,554 stars (GitHub API, 2026-10-13).
- Commercial and adoption signals: a "$150 million Series A in January 2026 at a $1.7 billion valuation" led by Felicis and UC Investments, after a "$100 million seed at a $600 million valuation" ([VKTR](https://www.vktr.com/ai-news/lmarena-raises-150m-series-a-at-17b-valuation/)). One source reports its commercial AI Evaluations product reached a "$30 million" annualized revenue rate within about four months (UNVERIFIED, per a search summary of [founded.com](https://www.founded.com/lmarena-arena-ai-ranking-tool-startup-founders/)).
- Integrity controversy: the "Leaderboard Illusion" paper by Cohere researchers alleged undisclosed private testing by top labs; LMArena disputed it ([Latent Space episode listing](https://metacast.app/podcast/latent-space-the-ai-engineer-podcast/uNjxKwgL/state-of-evals-lmarena-s-1-7b-vision-anastasios-angelopoulos-lmarena/kKbcbDmv)). I did not read the paper itself.
- LiveTradeBench found that "state-of-the-art models in LMArena do not exhibit state-of-the-art trading performance" ([LiveTradeBench](https://arxiv.org/html/2511.03628v1)). This shows LMArena serving as the reference point other benchmarks define themselves against.

### SWE-bench (Princeton; reference for adoption dynamics)
- GitHub: SWE-bench/SWE-bench has 6,014 stars (GitHub API, 2026-10-13).
- Lifecycle: OpenAI published SWE-bench Verified, a human-validated subset, in August 2024, and it "became a standard metric in frontier model releases." On 23 Feb 2026 OpenAI stopped reporting it, citing contamination (GPT-5.2, Claude Opus 4.5 and Gemini 3 Flash could reproduce original fixes from memory) and flawed tests (59.4% of 138 audited hard tasks). OpenAI moved to SWE-Bench Pro (Scale) and private tests ([OpenAI](https://openai.com/index/why-we-no-longer-evaluate-swe-bench-verified/); [The Decoder](https://the-decoder.com/openai-wants-to-retire-the-ai-coding-benchmark-that-everyone-has-been-competing-on/)).
- A May 2026 Datacurve audit reportedly found that Pro's graders mis-graded "roughly a third of trials" (UNVERIFIED, secondary).
- Lesson: adoption accelerated once a frontier lab co-owned a cleaned version, and static benchmarks eventually die of contamination and label errors.

### Prophet Arena (University of Chicago team; contact@prophetarena.co)
- Launch: arXiv 2510.17638, Oct 2025 ([arXiv](https://arxiv.org/html/2510.17638v2)). ICLR 2026 ([ICLR](https://iclr.cc/virtual/2026/poster/10009102)).
- Design: live Kalshi events. The paper calls the benchmark "naturally immune to data contamination" and reports three metrics: Brier score, calibration and market return ([ICLR 2026 PDF](https://proceedings.iclr.cc/paper_files/paper/2026/file/aec5e2847c5ae90f939ab786774856cc-Paper-Conference.pdf)).
- Live leaderboard (fetched 2026-10-13): 21 forecasters (16 fixed-context, 5 agentic) plus a Kalshi Markets baseline. The board ranks by 1 - Brier and also shows ECE and average return. The top row is "Agent Claude Fable 5.1 (retired)" at 0.9491; Kalshi Markets scores 0.9456. "Scores are recomputed every 3 days", with a "Submit your model" path ([leaderboard](https://www.prophetarena.co/leaderboard/forecast)).
- Media and vendor signals: Decrypt coverage ([Decrypt](https://decrypt.co/?p=336298)). A vendor claims "#1 AI Forecaster" on Prophet Arena ([Lightning Rod blog](https://blog.lightningrod.ai/p/how-we-built-the-number-1-ai-forecaster)). This shows companies using the leaderboard for marketing.
- Frontier-lab model cards: none found.

### KalshiBench (Lukas Nel, Lotus AI)
- Launch: arXiv 2512.16030, Dec 2025 ([arXiv](https://arxiv.org/abs/2512.16030v1)).
- Design: 300 Kalshi questions resolving after 1 Oct 2025, chosen to avoid memorized outcomes. Static once collected, with a temporal cutoff.
- Models: 5 (Claude Opus 4.5, GPT-5.2, DeepSeek-V3.2, Qwen3-235B, Kimi-K2).
- Findings: Opus 4.5 was best calibrated (ECE 0.120). Reasoning modes (GPT-5.2-XHigh) worsened calibration. Accuracy fell in a narrow 64-69% band, while ECE varied about 3x. Models had a 27% error rate even when they stated >90% confidence ([arXiv HTML](https://arxiv.org/html/2512.16030v1)).
- Adoption signals: none found beyond paper aggregators.

### Metaculus AI Benchmarking (quarterly bot tournaments)
- Q1 2025: 34 bot makers, $30,000 prize, 424 questions, 10 Pros on 100 of them. Pros beat the top-10 bot team (p = 0.001); the head-to-head score was -17.7 [95% CI -28.3, -7.0] ([EA Forum, Metaculus](https://forum.effectivealtruism.org/posts/mwcWxwdsEMexm98GF/q1-ai-benchmarking-results-human-pros-crush-bots)).
- Q2 2025: 54 bot makers, $30,000, 348 questions. Pros led (p = 0.00001), with a head-to-head score of -20.03 [-28.63, -11.41]. The winning bot was open source and used separate outside-view and inside-view reports ([LessWrong](https://www.lesswrong.com/posts/Surnjh8A4WjgtQTkZ/q2-ai-benchmark-results-pros-maintain-clear-lead)).
- A 2026 synthesis says Pros lead every quarter, but bots are making "consistent forward progress" ([EA Forum](https://forum.effectivealtruism.org/posts/Spyz3wESZu2eeqhDj/ai-forecasting-in-2026-what-11-analyses-say)).
- Design traits: live, open submission (anyone can enter a bot), cash prizes, a human-pro baseline, and peer scoring.

### FutureX (ByteDance Seed with Fudan, Stanford and Princeton)
- Launch: arXiv 2508.11987, Aug 2025. ICLR 2026 ([ICLR proceedings](https://proceedings.iclr.cc/paper_files/paper/2026/hash/21315abf210cef71317e887e9cda78b3-Abstract-Conference.html)).
- Design: questions collected by an automated pipeline and updated daily. Described as the "largest and most diverse live benchmark for future prediction", covering politics, economics and finance. Evaluated 25 LLMs and agents, including deep-research agents. Analyzes vulnerability to fake web pages ([arXiv](https://arxiv.org/abs/2508.11987v1)).
- Frontier-lab model cards: none found.

### Alpha Arena (Nof1)
- Season 1 (Oct-Nov 2025): 6 models, each given $10,000 of real money to trade crypto perps on Hyperliquid. Qwen3 Max finished first at about $12,231; GPT-5 finished last at about $4,126 ([Forklog](https://forklog.com/en/four-out-of-six-ai-models-suffer-losses-in-trading-tournament/amp)). The end date conflicts between sources (Nov 3 vs Nov 5).
- Nof1's founder: "LLMs do not handle numerical time series data well, but that was the entire context we provided them" (via search summary of Forklog coverage, UNVERIFIED wording).
- Season 1.5 (late Nov-Dec 2025): US stocks. A "Mystery Model" later identified as Grok 4.20 won with about +12% on average; GPT-5.1 placed second and Gemini 3 third ([Forklog](https://forklog.com/en/news/ai-model-grok-4-2-triumphs-in-trading-tournament)). Figures conflict ($12,193 vs $11,060) ([Yahoo Finance](https://finance.yahoo.com/news/elon-musks-grok-4-20-123855766.html)).
- Adoption signal: very high media reach. Elon Musk promoted the result, which amounts to de facto lab marketing through X posts rather than a model card.
- Criticism: commentators noted the short duration, small capital and lack of statistical controls (Forklog). A Towards AI piece is titled "the leaderboard lied to everyone" ([Towards AI](https://pub.towardsai.net/six-frontier-ai-models-traded-60-000-in-real-money-for-17-days-the-leaderboard-lied-to-everyone-9f73591a8d4b), paywalled, not read).
- No academic paper found. No primary Nof1 season report fetched.

### Other live trading benchmarks
- LiveTradeBench (UIUC, arXiv 2511.03628, Nov 2025): live US stocks plus Polymarket, 50-day live evaluation of 21 LLMs, with a leaderboard at trade-bench.live ([arXiv](https://arxiv.org/html/2511.03628v1); [GitHub](https://github.com/ulab-uiuc/live-trade-bench)).
- AI-Trader (arXiv 2512.10971): US, A-share and crypto markets, 6 LLMs; "most agents exhibit poor returns and weak risk management" ([arXiv](https://arxiv.org/abs/2512.10971v1)).

### Finance-specific benchmarks with wide adoption
- FinQA (Chen et al., EMNLP 2021): numerical reasoning over financial reports. GitHub czyssrs/FinQA has 402 stars. OpenAlex shows 240 citations, likely an undercount ([OpenAlex query](https://api.openalex.org/works?search=FinQA)).
- FinanceBench (Patronus AI, Nov 2023): QA over SEC filings. GitHub patronus-ai/financebench has 373 stars.
- FinBen / PIXIU (The FinAI): a holistic financial benchmark covering many tasks. GitHub The-FinAI/PIXIU has 893 stars.
- Vals AI Finance Agent (v1, v1.1, v2): this is the finance benchmark that frontier labs do cite. Anthropic cited Opus 4.7's 64.37% on Vals' Finance Agent when it launched finance agents in May 2026 (UNVERIFIED, per search summary of secondary coverage; [Vals model page](https://www.vals.ai/models/anthropic_claude-opus-5)). Opus 4.6 scored 60.7% on v1 in Feb 2026 (UNVERIFIED secondary). On the v2 board, Claude Opus 5 leads at 58.6%, ahead of Gemini 3.5 Flash at 57.9% ([BenchLM mirror](https://benchlm.ai/benchmarks/financeagentv2), UNVERIFIED mirror).
- Vending-Bench 2 (Andon Labs): an economic-agent benchmark scored in dollars earned. Reportedly cited in Anthropic's Opus 4.5 launch ("29% more Vending-Bench earnings than Sonnet 4.5", UNVERIFIED secondary) and in Google's Gemini 3 Pro comparisons ([Andon Labs](https://andonlabs.com/evals/vending-bench-arena); [Andon blog](https://andonlabs.com/blog/opus-4-6-vending-bench)).

## Which design features correlate with adoption?

### Takeaway
The benchmarks with the highest adoption (LMArena, SWE-bench, LiveBench) share four traits: one headline number, a public leaderboard, cheap or automatic scoring, and early frontier-lab co-ownership or citation. Being live is the main defense against contamination. Live status alone does not produce lab adoption, though: no live forecasting benchmark has reached model cards.

### Cited Findings
- LMArena: a single Elo-style ranking and a public board grew into a $1.7B company; labs care enough to be accused of gaming it ([VKTR](https://www.vktr.com/ai-news/lmarena-raises-150m-series-a-at-17b-valuation/); [Latent Space listing](https://metacast.app/podcast/latent-space-the-ai-engineer-podcast/uNjxKwgL/state-of-evals-lmarena-s-1-7b-vision-anastasios-angelopoulos-lmarena/kKbcbDmv)).
- SWE-bench Verified "became a standard metric in frontier model releases" once OpenAI co-built it, and was dropped once contamination and label noise surfaced ([OpenAI](https://openai.com/index/why-we-no-longer-evaluate-swe-bench-verified/)).
- LiveBench uses objective auto-scoring with monthly refresh and has 1.3k stars ([arXiv](https://arxiv.org/abs/2406.19314)). ForecastBench is also live, with open submission and a human baseline, but has only 89 stars (GitHub API).
- Vals Finance Agent is the finance benchmark that shows up in lab launch materials. It is static and task-completion style, not forecasting ([Vals](https://www.vals.ai/models/anthropic_claude-opus-5)).
- Open submission plus prizes drives participation: Metaculus drew 34 bot makers in Q1 2025 and 54 in Q2 2025 with $30k per quarter ([LessWrong](https://www.lesswrong.com/posts/Surnjh8A4WjgtQTkZ/q2-ai-benchmark-results-pros-maintain-clear-lead)).
- Vendors use leaderboard rank as marketing ("#1 AI Forecaster" on Prophet Arena) ([Lightning Rod](https://blog.lightningrod.ai/p/how-we-built-the-number-1-ai-forecaster)).

### Inferences
- Lab adoption appears to require that (a) the capability is one labs want to advertise (coding, agentic work, finance-agent tasks) and (b) scores move visibly across model generations. Forecasting scores cluster tightly; the Prophet Arena top 5 sits within 0.0013 on 1 - Brier ([leaderboard](https://www.prophetarena.co/leaderboard/forecast)). Tight clustering makes the benchmark weak for marketing.
- Having "human expert baseline" and "parity date" framing (ForecastBench, Metaculus) earns attention from the forecasting and EA community and from funders rather than from labs.

### Gaps
- Exact citation counts. Semantic Scholar was rate-limited (429) and OpenAlex results looked wrong (ForecastBench 0; Chatbot Arena 100). Check Google Scholar manually.
- I did not verify whether LiveBench or LMArena numbers appear in specific 2025-2026 model cards.

## Are frontier labs reporting forecasting or finance numbers in model cards (2025-2026)?

### Takeaway
Finance: yes, but agentic task benchmarks (Vals Finance Agent) and economic simulations (Vending-Bench), not market-prediction or calibration benchmarks. Forecasting: I found no frontier-lab model card reporting ForecastBench, Prophet Arena, FutureX, KalshiBench or Metaculus results. Live trading (Alpha Arena) reached labs only as social-media promotion (xAI and Musk for Grok 4.20).

### Cited Findings
- Anthropic referenced Opus 4.7's 64.37% on Vals Finance Agent at its May 2026 finance-agent launch (UNVERIFIED secondary; [Vals](https://www.vals.ai/models/anthropic_claude-opus-5)).
- Vending-Bench 2 was reportedly in the Opus 4.5 launch (Nov 24, 2025) and in Gemini 3 Pro comparisons (UNVERIFIED secondary; [Andon Labs](https://andonlabs.com/evals/vending-bench-arena)).
- A search for model cards citing ForecastBench, Prophet Arena or FutureX returned only papers, leaderboards and a vendor blog ([search results incl. Prophet Arena PDF](https://proceedings.iclr.cc/paper_files/paper/2026/file/aec5e2847c5ae90f939ab786774856cc-Paper-Conference.pdf)).
- Grok 4.20's Alpha Arena win was promoted through X posts and media, not a system card ([Yahoo Finance](https://finance.yahoo.com/news/elon-musks-grok-4-20-123855766.html)).

### Inferences
- The route into lab reports in finance runs through "agent does analyst work" (Vals). No route currently exists for "model predicts prices", plausibly for regulatory and reputational reasons (labs avoid implying their models give investment advice). This is an inference; no source states it.

### Gaps
- I did not open primary system-card PDFs (OpenAI GPT-5.x, Anthropic Opus 4.5-5.5, Gemini 3.x, xAI Grok 4.x, Meta). The Vals and Vending-Bench claims need primary verification.

## Do calibration (Brier) forecasting benchmarks get adopted more than return-based trading benchmarks?

### Takeaway
On academic legitimacy, calibration wins clearly: ForecastBench (ICLR 2025), Prophet Arena (ICLR 2026) and FutureX (ICLR 2026) are all peer-reviewed. On mass-media reach, return-based trading wins clearly: Alpha Arena drew mainstream and crypto press and a Musk promotion despite having no paper and no statistical controls. Neither type has reached frontier model cards.

### Cited Findings
- The calibration and forecasting line has three ICLR papers ([ForecastBench](https://proceedings.iclr.cc/paper_files/paper/2025/hash/ea74e45a229dac70b5b63b28d8934db6-Abstract-Conference.html); [Prophet Arena](https://iclr.cc/virtual/2026/poster/10009102); [FutureX](https://iclr.cc/virtual/2026/poster/10006496)).
- Alpha Arena has heavy media coverage alongside explicit criticism of short duration, small capital and no statistical controls ([Forklog](https://forklog.com/en/four-out-of-six-ai-models-suffer-losses-in-trading-tournament/amp)).
- The academic trading benchmarks (LiveTradeBench with 21 models, AI-Trader with 6) exist, but adoption signals are thin ([LiveTradeBench](https://arxiv.org/html/2511.03628v1); [AI-Trader](https://arxiv.org/abs/2512.10971v1)).
- Prophet Arena hedges by reporting Brier, calibration and market return together ([ICLR 2026 PDF](https://proceedings.iclr.cc/paper_files/paper/2026/file/aec5e2847c5ae90f939ab786774856cc-Paper-Conference.pdf)).
- Return-based results are noisy: integrity work shows a leaky oracle with Sharpe of about 35 "survives Deflated Sharpe and probability-of-backtest-overfitting testing completely" ([Gencay, arXiv 2608.27734](https://arxiv.org/pdf/2608.27734)). Parametric look-ahead from model memory inflates in-sample backtest returns, and FinCAD cuts them "by up to -67.1%" on memorized dates ([arXiv 2605.24564](https://arxiv.org/abs/2605.24564)).

### Inferences
- A hybrid that leads with Brier/ECE and shows returns as a secondary column (the Prophet Arena pattern) captures both audiences.
- Forward-only design removes the look-ahead problem that the integrity papers document. This is a concrete, defensible advantage over backtest-based finance benchmarks.

### Gaps
- No head-to-head citation comparison was possible (citation APIs failed).
- No Alpha Arena traffic or follower numbers were found.

## Is there an unoccupied niche among A-D?

### Takeaway
- (A) Generic event-forecasting calibration is crowded: ForecastBench, Prophet Arena, FutureX, KalshiBench, Metaculus. I found no benchmark dedicated to forward-only calibration on equity-relative-return questions such as P(stock beats SPY over 5 days) across many models. That is a narrow but open niche.
- (B) A faithfulness benchmark for numbers in financial rationales looks least occupied.
- (C) Live trading leaderboards are crowded and credibility-challenged.
- (D) Integrity tooling has active 2026 papers but no adopted standard toolkit.

### Cited Findings
- (A) Existing forecasting benchmarks draw on general events and prediction markets (Kalshi, Polymarket) and on multi-domain questions including finance ([FutureX](https://arxiv.org/abs/2508.11987v1); [KalshiBench](https://arxiv.org/html/2512.16030v1)). None found is equity-cross-section specific.
- (B) The closest work: FAITH (masked numeric spans in annual reports) ([arXiv 2508.05201](https://arxiv.org/pdf/2508.05201)). A summarization study measured numeric hallucination rates of 6.48%, 3.97% and 5.74% for Claude 2.0, Claude 2.1 and GPT-4 ([arXiv 2404.06162](https://arxiv.org/pdf/2404.06162)). A vendor test checks refusal to invent missing figures ([JurisTech](https://juristech.net/best-llm-tools-for-financial-analysis-2026/), vendor). None scores the share of numbers in trading rationales that the given inputs do not support.
- (C) Alpha Arena, LiveTradeBench, AI-Trader and Prophet Arena's return column already occupy this space (sources above).
- (D) "What survives honest evaluation?" builds leakage-safe, search-aware evaluation with Deflated Sharpe and a trial ledger ([arXiv 2608.27734](https://arxiv.org/pdf/2608.27734)). A Manifund project proposes look-ahead-bias audits for AI trading and forecasting benchmarks ([Manifund](https://manifund.org/projects/pit-audit-lookahead-bias-audits-for-ai-trading--forecasting-benchmarks)). These are emerging, not adopted.

### Inferences
- The strongest evidence-backed play is to combine A and B on one forward-only platform:
  - Headline: Brier/ECE on equity-relative questions across many models. This uses the calibration format that gets peer-reviewed.
  - Second axis: rationale faithfulness, the share of unsupported numbers. This is an objective, auto-scored, cheap metric in the LiveBench style that no one owns.
  - Show returns only as a secondary column.
- Use (D) as the credibility layer: pre-registration, a tamper-evident ledger and placebo controls answer the exact criticism leveled at Alpha Arena. On its own, (D) is unlikely to drive adoption.
- (B) is also the design closest to what labs already report in finance (Vals Finance Agent is about doing analyst work correctly), so it has the best odds of a model-card mention.
- Paths to impact suggested by the comparables:
  - an ICLR/NeurIPS datasets-track paper
  - open submission
  - a human or market baseline (for example, analyst consensus or the SPY base rate)
  - a single headline number
  - frequent refresh

### Gaps
- I did not search StockBench, InvestorBench, FinSearchComp or other 2025-2026 finance-forecasting papers in depth. An equity-specific calibration benchmark may exist that I missed.
- I found no data on what drives submissions to ForecastBench's open tournament (submission counts not visible).
