"use client";

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ApiError, apiFetch } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import type { PipelineRun, StrategyOverview } from "@/lib/types";
import { AccuracyPanel } from "@/components/admin/strategy/AccuracyPanel";
import { AskConsole } from "@/components/admin/strategy/AskConsole";
import { CapitalPanel } from "@/components/admin/strategy/CapitalPanel";
import { CeoView } from "@/components/admin/strategy/CeoView";
import { ResearchPanel } from "@/components/admin/strategy/ResearchPanel";
import { UsersPanel } from "@/components/admin/strategy/UsersPanel";
import { FeedbackPanel } from "@/components/admin/strategy/FeedbackPanel";
import { FlagsPanel } from "@/components/admin/strategy/FlagsPanel";
import { SnapshotsPanel } from "@/components/admin/strategy/SnapshotsPanel";
import { LedgerPanel } from "@/components/admin/strategy/LedgerPanel";
import { CompliancePanel } from "@/components/admin/strategy/CompliancePanel";
import { GateHealthPanel } from "@/components/admin/strategy/GateHealthPanel";

const TABS = [
  { id: "ceo", label: "CEO view", hint: "today's calls" },
  { id: "ask", label: "Ask the committee", hint: "prompt console" },
  { id: "accuracy", label: "Is it working?", hint: "accuracy & leaderboard" },
  { id: "capital", label: "Capital & tax", hint: "results" },
  { id: "users", label: "Users", hint: "portfolios · validation data" },
  { id: "feedback", label: "Feedback", hint: "what users say" },
  { id: "flags", label: "Flags", hint: "kill switches" },
  { id: "research", label: "Research", hint: "correlations · evidence gate" },
  { id: "snapshots", label: "Snapshots", hint: "point-in-time fetches" },
  { id: "ledger", label: "Ledger", hint: "append-only call chain" },
  { id: "compliance", label: "Compliance", hint: "A7 filter log · rules" },
  { id: "gate-health", label: "Gate health", hint: "verification pass rate" },
] as const;
type Tab = (typeof TABS)[number]["id"];

const PIPELINE_TONE: Record<string, string> = { ok: "text-teal", running: "text-t2", partial: "text-gold", failed: "text-red", no_bar: "text-t3" };

/** The last daily-pipeline run: which trading day, did it finish, and which stage (if any) did not. */
function PipelineLine({ run }: { run: PipelineRun }) {
  const stages = Object.entries(run.stages ?? {});
  const failed = run.failed_stages ?? stages.filter(([, s]) => !s.ok).map(([name]) => name);
  const label = run.status === "no_bar" ? "no final bar" : run.status;
  return (
    <p className="text-[11px] text-t3" aria-label="Daily pipeline status">
      Daily pipeline for <span className="mono">{run.target}</span>:{" "}
      <b className={PIPELINE_TONE[run.status] ?? "text-t2"}>{label}</b>
      {stages.length > 0 && <span className="mono"> · {stages.length - failed.length}/{stages.length} stages ok</span>}
      {failed.length > 0 && <span className="text-red"> · failed: {failed.join(", ")}</span>}
      {run.message ? <span> · {run.message}</span> : null}
    </p>
  );
}

/** One page to answer: what did the committee decide, is each signal actually delivering,
 * which agent is worth trusting, and what does it earn after cost and tax? */
export default function StrategyPage() {
  const { token, role } = useAuth();
  const [tab, setTab] = useState<Tab>("ceo");
  const q = useQuery({
    queryKey: ["strategy-overview"],
    queryFn: () => apiFetch<StrategyOverview>("/api/admin/strategy/overview", { token: token ?? undefined }),
    enabled: !!token && role === "admin",
    refetchInterval: 60_000,
    retry: false,
  });

  if (role !== "admin") return <p className="py-sp10 text-center text-[13px] text-t3">This page is for administrators.</p>;

  return (
    <div className="flex flex-col gap-sp5">
      <header>
        <h1 className="text-[22px] font-extrabold tracking-tight text-t1">Strategy</h1>
        <p className="mt-1 max-w-[80ch] text-[12px] text-t3">
          A long-horizon, tax-aware research system: daily closes, weekly-scale decisions, no intraday trading. Everything here is simulated paper trading — research, not investment advice.
          {q.data?.data_date && <span className="mono"> · data through {q.data.data_date}</span>}
        </p>
      </header>

      {q.data?.data_quality && !q.data.data_quality.ok && (
        <div role="alert" className="rounded-r3 border border-red/50 bg-red-dim p-sp4 text-[12px] leading-snug text-t1">
          <b className="text-red">Data quality warning.</b> The price history has {q.data.data_quality.gaps.length === 1 ? "a hole" : `${q.data.data_quality.gaps.length} holes`}:{" "}
          {q.data.data_quality.gaps.slice(0, 3).map((g) => `${g.from} → ${g.to} (${g.days} calendar days)`).join("; ")}. A return across a hole is not a daily return, so backtests,
          volatility and correlations that span it are unreliable. Run the daily sync; it now backfills from the last stored bar.
        </div>
      )}

      {q.data?.pipeline && <PipelineLine run={q.data.pipeline} />}

      <nav className="flex flex-wrap gap-sp2" aria-label="Strategy sections">
        {TABS.map((t) => (
          <button
            key={t.id}
            type="button"
            onClick={() => setTab(t.id)}
            aria-current={tab === t.id}
            className={`rounded-r2 border px-sp4 py-sp2 text-left ${tab === t.id ? "border-teal/60 bg-teal-dim" : "border-border bg-bg2/40 hover:bg-bg3"}`}
          >
            <div className={`text-[12px] font-bold ${tab === t.id ? "text-teal" : "text-t1"}`}>{t.label}</div>
            <div className="text-[10px] text-t3">{t.hint}</div>
          </button>
        ))}
      </nav>

      {tab === "accuracy" ? (
        <AccuracyPanel />
      ) : tab === "users" ? (
        <UsersPanel />
      ) : tab === "feedback" ? (
        <FeedbackPanel />
      ) : tab === "flags" ? (
        <FlagsPanel />
      ) : tab === "research" ? (
        <ResearchPanel />
      ) : tab === "snapshots" ? (
        <SnapshotsPanel />
      ) : tab === "ledger" ? (
        <LedgerPanel />
      ) : tab === "compliance" ? (
        <CompliancePanel />
      ) : tab === "gate-health" ? (
        <GateHealthPanel />
      ) : q.isPending ? (
        <p className="py-sp6 text-center text-[13px] text-t3">Loading…</p>
      ) : q.isError ? (
        <p className="py-sp6 text-center text-[13px] text-red">{q.error instanceof ApiError ? q.error.message : "Could not load the strategy view"}</p>
      ) : tab === "ceo" ? (
        <CeoView decisions={q.data.decisions} latestDate={q.data.latest_review_date} risk={q.data.risk_today} history={q.data.history} coverage={q.data.coverage} />
      ) : tab === "ask" ? (
        <AskConsole symbols={q.data.risk_today.map((r) => r.symbol).sort()} />
      ) : (
        <CapitalPanel rows={q.data.capital} curves={q.data.curves} tax={q.data.tax_assumptions} />
      )}
    </div>
  );
}
