// Mirrors backend/app/models.py exactly (Phase 4/5 section). Keep in sync.

export type AgentType = "deterministic" | "llm";
export type Provider =
  | "vllm"
  | "ollama"
  | "gemini"
  | "claude"
  // OpenAI-compatible failover providers (see backend/app/llm_router.py)
  | "openrouter"
  | "groq"
  | "cerebras"
  | "github"
  | "qwen"
  | "deepseek"
  | "xai"
  | "gateway";
export type OrchestrationMode = "sequential" | "parallel" | "committee_vote";
export type Role = "admin" | "viewer";

export type AgentParams = {
  temperature: number;
  top_p: number;
  max_tokens: number;
  extra: Record<string, unknown>;
};

export type AgentConfig = {
  id?: string;
  name: string;
  role: string;
  type: AgentType;
  provider?: Provider | null;
  model?: string | null;
  fallback_models: string[];
  params: AgentParams;
  system_prompt?: string | null;
  tools: string[];
  enabled: boolean;
};

export type OrchestrationConfig = {
  id?: string;
  name: string;
  mode: OrchestrationMode;
  agent_ids: string[];
  coordinator: string;
  schedule?: string | null;
  agent_timeout_s: number;
  run_budget_s: number;
};

export type ProviderHealth = {
  provider: Provider;
  reachable: boolean;
  detail?: string | null;
  checked_at: string;
};

export type JobKind = "agent_test_run" | "orchestration_run" | "report_generate" | "my_agents_run" | "insight_narrate";
export type JobState = "queued" | "running" | "done" | "error";

export type JobStatus = {
  job_id: string;
  kind: JobKind;
  status: JobState;
  created_at: string;
  result?: Record<string, unknown> | null;
  error?: string | null;
};

export type LLMCallLog = {
  id: string;
  agent_id?: string | null;
  provider: Provider;
  model: string;
  started_at: string;
  duration_ms?: number | null;
  tokens_in?: number | null;
  tokens_out?: number | null;
  status: "running" | "ok" | "error" | "timeout";
  error?: string | null;
  estimated_cost_usd?: number | null;
};

export type PaginatedLLMCalls = { items: LLMCallLog[]; total: number };

export type AuditLogEntry = {
  id: string;
  actor: string;
  action: string;
  resource_type: string;
  resource_id?: string | null;
  detail: Record<string, unknown>;
  created_at: string;
};

export type PaginatedAuditLog = { items: AuditLogEntry[]; total: number };

export type DailyReportNarrative = {
  id: string;
  date: string;
  provider: Provider | "system"; // "system" = written by the paper-trading engine, not an LLM
  model: string;
  narrative: string;
  created_at: string;
  title?: string | null;
  profile?: string | null;
};

export const DEFAULT_AGENT_PARAMS: AgentParams = {
  temperature: 0.7,
  top_p: 1.0,
  max_tokens: 1024,
  extra: {},
};

export function emptyAgent(): AgentConfig {
  return {
    name: "",
    role: "",
    type: "deterministic",
    provider: null,
    model: null,
    fallback_models: [],
    params: { ...DEFAULT_AGENT_PARAMS },
    system_prompt: "",
    tools: [],
    enabled: true,
  };
}

export function emptyOrchestration(): OrchestrationConfig {
  return {
    name: "",
    mode: "sequential",
    agent_ids: [],
    coordinator: "vn_engine",
    schedule: "",
    agent_timeout_s: 30.0,
    run_budget_s: 120.0,
  };
}

// ---- Paper trading (admin master view) ----

export type PaperAccountKind = "profile" | "benchmark" | "control";
export type CurveMode = "backtest" | "live";
export type CurvePoint = [string, number, CurveMode];

export type PaperAccountSummary = {
  id: string;
  name: string;
  kind: PaperAccountKind;
  strategy: string;
  username: string | null;
  risk_level: string | null;
  profile: { archetype?: string; horizon_years?: number };
  equity: number;
  total_return: number;
  cagr: number | null;
  live_return: number | null;
  max_drawdown: number;
  sharpe: number;
  volatility: number;
  trade_count: number;
  cost_paid: number;
  turnover: number;
  days: number;
  live_days: number;
  inception: string | null;
  last_date: string | null;
  cash_weight: number;
  benchmark_id: string | null;
  benchmark_return?: number;
  alpha: number | null;
  live_alpha: number | null;
};

export type PaperOverview = {
  initialised: boolean;
  meta: { live_from: string; start: string; last_date: string | null; last_run?: string } | null;
  accounts: PaperAccountSummary[];
};

export type PaperTrade = { date: string; symbol: string; side: "BUY" | "SELL"; shares: number; price: number; cost: number; reason: string };

export type PaperAccountDetail = {
  summary: PaperAccountSummary;
  weights: Record<string, number>;
  invested: number;
  note: string;
  holdings: Record<string, { shares: number; price: number; value: number; weight: number }>;
  cash: number;
  curve: CurvePoint[];
  benchmark_curve: CurvePoint[];
  recent_trades: PaperTrade[];
};

export type SignalStat = { n: number; mean_return: number | null; hit_rate: number | null };
export type PaperScorecard = {
  horizons: number[];
  signals: Record<"BUY" | "SELL" | "HOLD" | "ALL", Record<string, SignalStat>>;
  edge_vs_average: Record<"BUY" | "SELL", Record<string, number | null>>;
  in_sample: boolean;
  as_of: string | null;
};

export type PaperRunResult = {
  bootstrapped: boolean;
  latest_data_date: string;
  live_from: string;
  accounts: number;
  new_live_days: string[];
  reports_written: number;
};

// ---- LLM routing / failover (admin) ----

export type RoutingProvider = {
  provider: string;
  label: string;
  tier: "free" | "freemium" | "paid" | "custom" | "local" | string;
  configured: boolean;
  not_configured_reason: string;
  in_failover_order: boolean;
  cooling_down_s: number;
  cooldown_reason: string;
  last: { ok: boolean; at: string; detail: string } | null;
  get_a_key: string;
};

export type RoutingStatus = { enabled: boolean; order: string[]; budget_s: number; user_runs_may_fail_over: boolean; providers: RoutingProvider[] };

export type RoutingTestResult = { ok: boolean; answered_by: string; model: string; failed_over: boolean; reply: string; skipped_or_failed: { provider: string; result: string }[] };

// ---- Daily Investment Committee (admin) ----

export type CommitteeAgentRow = { agent: string; ok: boolean; type?: string; lean?: string; summary?: string; provider?: string; model?: string; error?: string };

export type CommitteeRun = {
  id: string;
  date: string;
  symbol: string;
  why: string;
  engine_signal: "BUY" | "SELL" | "HOLD";
  engine_confidence: number;
  price: number;
  decision: "BUY" | "SELL" | "HOLD" | null;
  votes: { BUY: number; SELL: number; HOLD: number } | null;
  agrees_with_engine: boolean | null;
  agents: CommitteeAgentRow[];
  answered: number;
  total: number;
  quorum_ok: boolean;
  providers: Record<string, number>;
  error: string | null;
  seconds: number;
};

export type CommitteeRunsResponse = { running: boolean; runs: CommitteeRun[] };
export type CommitteePreview = { date: string; picked: string[]; already_done: string[]; would_run: { symbol: string; why: string }[] };
export type CommitteeScorecard = {
  runs: number;
  reliable_runs: number;
  agrees_with_engine: number | null;
  by_decision: Record<"BUY" | "SELL" | "HOLD", Record<string, { n: number; mean_return: number | null; hit_rate: number | null }>>;
};

// ---- Strategy (admin) and stance / track record (users) -------------------------------------------

export type Lean = "BUY" | "SELL" | "HOLD";
export type RiskLevel = "LOW" | "MEDIUM" | "HIGH";
export type RiskSnapshot = { level: RiskLevel; score: number; vol: number; vol_pct: number; drawdown: number; below_ma200: boolean };
export type RiskRow = RiskSnapshot & { symbol: string };

export type StrategyAgent = {
  agent: string;
  ok: boolean;
  type?: "deterministic" | "llm";
  lean?: Lean;
  summary?: string;
  provider?: string;
  model?: string;
  error?: string;
  confidence?: number;
  risk_level?: RiskLevel;
  structured?: boolean;
  latency_s?: number;
  raw?: string;
  system_prompt?: string;
  failed_over_from?: string;
};

export type CeoBrief = {
  call: Lean;
  vote: Lean;
  consensus: number;
  label: "strong consensus" | "majority" | "split";
  margin: number;
  engine_trio: Lean | null;
  analyst_panel: Lean | null;
  trio_panel_agree: boolean | null;
  dissenters: string[];
  gate: string | null;
  headline: string;
};

export type StrategyDecision = {
  id: string;
  date: string;
  symbol: string;
  why: string;
  engine_signal: Lean;
  engine_confidence: number;
  price: number;
  decision: Lean | null;
  action: Lean | null;
  gate: string | null;
  risk: RiskSnapshot | null;
  analyst_risk: Record<RiskLevel, number> | null;
  votes: { BUY: number; SELL: number; HOLD: number } | null;
  ceo: CeoBrief | null;
  context: string | null;
  prompt?: string;
  engine: string;
  agrees_with_engine: boolean | null;
  agents: StrategyAgent[];
  answered: number;
  total: number;
  quorum_ok: boolean;
  error: string | null;
  seconds: number;
  question?: string;
  status?: "running" | "done" | "error";
};

export type CapitalRow = {
  id: string;
  name: string;
  strategy: string;
  equity: number;
  total_return: number;
  live_return: number | null;
  max_drawdown: number;
  sharpe: number;
  turnover: number;
  cost_paid: number;
  live_days: number;
  days: number;
  tax_tracked: boolean;
  tax_status?: "taxable" | "sheltered";
  wash_disallowed?: number | null;
  deferred_sells?: number | null;
  deferred_notional?: number | null;
  est_tax: number | null;
  after_tax_return: number | null;
  after_tax_liquidated_return?: number | null;
  liquidation_tax?: number | null;
  tax_drag: number | null;
  avg_holding_days: number | null;
  realized_st?: number | null;
  realized_lt?: number | null;
  unrealized_st?: number | null;
  unrealized_lt?: number | null;
};

export type StrategyCurves = Record<string, CurvePoint[]>;

export type CoverageRow = {
  symbol: string;
  name: string;
  engine_signal: Lean;
  engine_confidence: number;
  risk: { level: RiskLevel; score: number } | null;
  reviewed: boolean;
  committee_action: Lean | null;
};
export type Coverage = { data_date: string | null; review_date: string | null; reviewed: number; total: number; rows: CoverageRow[] };

export type PipelineRun = {
  target: string;
  status: "running" | "ok" | "partial" | "failed" | "no_bar" | string;
  message?: string;
  started?: string;
  finished?: string;
  stages: Record<string, { ok: boolean; seconds?: number; detail?: string | null }>;
  failed_stages?: string[];
  sync?: { attempts?: number; coverage?: number; new_bars?: number | null; rejected?: number };
};

export type StrategyOverview = {
  data_date: string | null;
  pipeline?: PipelineRun | null;
  coverage?: Coverage;
  data_quality?: { ok: boolean; gaps: { from: string; to: number | string; days: number }[] };
  latest_review_date: string | null;
  decisions: StrategyDecision[];
  history: { date: string; symbol: string; decision: Lean | null; action: Lean | null; engine_signal: Lean; consensus: string | null; answered: number; total: number }[];
  risk_today: RiskRow[];
  capital: CapitalRow[];
  curves: StrategyCurves;
  meta: { live_from?: string } | null;
  tax_assumptions: { short_term: number; long_term: number };
};

export type RiskBucket = { n: number; fwd_vol: number | null; fwd_worst_dip: number | null; fwd_return: number | null; p_drop: number | null };
export type LeaderRow = {
  agent: string;
  type: "deterministic" | "llm" | null;
  answers: number;
  agrees_with_committee: number;
  agrees_with_engine: number;
  avg_confidence: number | null;
  directional_calls: number;
  hit_rate: number | null;
  mean_edge: number | null;
  ranked: boolean;
};
export type StrategyAccuracy = {
  signal: PaperScorecard;
  risk: {
    horizons: number[];
    drop_threshold: Record<string, number>;
    levels: Record<"LOW" | "MEDIUM" | "HIGH" | "ALL", Record<string, RiskBucket>>;
    lift: Record<string, { vol: number | null; dip: number | null; p_drop: number | null }>;
    note: string;
  };
  committee: CommitteeScorecard;
  leaderboard: { horizon: number; min_ranked: number; agents: LeaderRow[]; note: string };
};

export type AskSummary = { id: string; symbol: string; question: string; status: "running" | "done" | "error"; created_at: string; action: Lean | null; decision: Lean | null };

export type StanceRow = {
  symbol: string;
  name: string;
  price: number | null;
  engine: { signal: Lean; confidence: number };
  committee: { action: Lean | null; consensus: string | null; date: string; headline: string | null } | null;
  risk: { level: RiskLevel; score: number } | null;
  fundamentals?: { revenue_growth?: number | null; net_margin?: number | null; pe?: number | null; roe?: number | null } | null;
  attention: boolean;
  watch: boolean;
  reasons: string[];
  summary: string;
};
export type StanceResponse = { as_of: string | null; rows: StanceRow[]; attention: number; watch: number; quiet: number; macro_line?: string | null };

export type TrackStrategy = Omit<CapitalRow, "realized_st" | "realized_lt" | "unrealized_st" | "unrealized_lt">;
export type TrackRecord = {
  initialised: boolean;
  live_from: string | null;
  strategies: TrackStrategy[];
  curves: StrategyCurves;
  tax_assumptions: { short_term: number; long_term: number };
};

export type GateResult = { n_days: number; needed_days: number; mean_excess_bps: number | null; ci_low_bps: number | null; ci_high_bps: number | null; verdict: "insufficient" | "edge" | "no edge yet" | "worse" };
export type EvidenceRow = { id: string; name: string; challenger?: boolean; live_days: number; live_return: number | null; vs: Record<string, GateResult> };
export type ResearchView = {
  as_of: string | null;
  associations: {
    date: string;
    window_days: number;
    symbols: number;
    cohesion: { value: number | null; percentile: number | null; label: string; samples: number };
    clusters: string[][];
    strongest_pairs: { a: string; b: string; r: number }[];
    weakest_pairs: { a: string; b: string; r: number }[];
    lead_lag: { tests: number; window: number; threshold_r: number | null; findings: { leader: string; follower: string; lag_days: number; r: number }[]; note?: string };
  } | null;
  evidence: EvidenceRow[];
  min_live_days: number;
  baselines: Record<string, string>;
  proposals: string[];
  latest_digest: { title: string; date: string; narrative: string } | null;
};

export type FreeFundamentals = {
  fiscal_year_end: string;
  filed: string;
  age_days: number;
  revenue_growth?: number;
  net_margin?: number;
  operating_margin?: number;
  roe?: number;
  fcf_margin?: number;
  debt_to_equity?: number;
  liabilities_to_equity?: number;
  eps?: number;
  pe?: number;
};
export type FreeEvent = { filed: string; form: string; items: string[]; text: string; flag: boolean };
export type DataSources = {
  as_of: string | null;
  sec_configured: boolean;
  macro_line: string | null;
  status: Record<string, { ok: boolean; detail: string; count: number; at: string }>;
  rows: { symbol: string; fundamentals: FreeFundamentals | null; events: FreeEvent[] }[];
};

// ---- Snapshot store (admin) ----

export type Snapshot = {
  id: string;
  source: string;
  ticker: string;
  as_of: string;
  fetched_at: string;
  payload_hash: string;
};

export type SnapshotDetail = Snapshot & {
  payload: unknown;
};

// ---- Ledger (admin) ----

export type LedgerRow = {
  seq: number;
  call_id: string;
  ticker: string;
  call_type: string;
  recorded_at: string;
  hash: string;
};

export type LedgerVerify = {
  ok: boolean;
  rows: number;
  first_bad_seq: number | null;
  reason: string;
};

// ---- Compliance filter (admin) ----

export type ComplianceAction = "blocked" | "rewritten" | "flagged";

export type ComplianceEvent = {
  id: string;
  run_id: string | null;
  channel: string;
  rule_id: string;
  matched_text: string;
  action: ComplianceAction;
  created_at: string;
};

export type ComplianceRule = {
  id: string;
  description: string;
  kind: "phrase" | "regex" | "requires";
  action: "block" | "rewrite" | "flag";
  patterns?: string[];
  allow?: string[];
  unless?: string;
  pattern?: string;
  replacement?: string;
};
