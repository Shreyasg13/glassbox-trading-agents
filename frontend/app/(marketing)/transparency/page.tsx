"use client";

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { fetchWeeklyReportPublic, fetchWeeklyReportsPublic } from "@/lib/api";
import type { WeeklyReportSummary } from "@/lib/types";

const pct = (n: number | null) => (n === null ? "—" : `${(n * 100).toFixed(1)}%`);

function ReportDetail({ id }: { id: string }) {
  const { data, isLoading, error } = useQuery({
    queryKey: ["weekly-report-public", id],
    queryFn: () => fetchWeeklyReportPublic(id),
  });

  if (isLoading) return <p className="text-[13px] text-t3">Loading…</p>;
  if (error || !data) return <p className="text-[13px] text-red">Couldn’t load this report.</p>;

  const { body } = data;
  return (
    <div className="glass-panel flex flex-col gap-sp4 p-sp5">
      <div className="grid gap-sp3 sm:grid-cols-3">
        <div>
          <div className="text-[11px] uppercase text-t3">Runs checked</div>
          <div className="mono text-[18px] text-t1">{body.runs_checked}</div>
        </div>
        <div>
          <div className="text-[11px] uppercase text-t3">A6 pass rate</div>
          <div className="mono text-[18px] text-t1">{pct(body.a6.pass_rate)}</div>
        </div>
        <div>
          <div className="text-[11px] uppercase text-t3">A7 events</div>
          <div className="mono text-[14px] text-t1">
            {body.a7_by_action.blocked} blocked · {body.a7_by_action.rewritten} rewritten · {body.a7_by_action.flagged} flagged
          </div>
        </div>
      </div>
      <div className="grid gap-sp4 sm:grid-cols-2">
        <div>
          <h3 className="mb-sp2 text-[11px] font-bold uppercase tracking-wide text-t3">Top failing checks</h3>
          {body.top_failing_checks.length === 0 && <p className="text-[12px] text-t3">None this week.</p>}
          <ul className="flex flex-col gap-1 text-[12px] text-t2">
            {body.top_failing_checks.map((c) => (
              <li key={c.check_type}>
                {c.check_type} — {c.failures}
              </li>
            ))}
          </ul>
        </div>
        <div>
          <h3 className="mb-sp2 text-[11px] font-bold uppercase tracking-wide text-t3">Top failing metrics</h3>
          {body.top_failing_metrics.length === 0 && <p className="text-[12px] text-t3">None this week.</p>}
          <ul className="flex flex-col gap-1 text-[12px] text-t2">
            {body.top_failing_metrics.map((m) => (
              <li key={m.metric}>
                {m.metric} — {m.failures}
              </li>
            ))}
          </ul>
        </div>
      </div>
    </div>
  );
}

function ReportRow({ report, selected, onSelect }: { report: WeeklyReportSummary; selected: boolean; onSelect: () => void }) {
  return (
    <button
      type="button"
      onClick={onSelect}
      className={`glass-panel flex w-full items-center justify-between p-sp3 text-left hover:border-teal/40 ${selected ? "border-teal/60" : ""}`}
    >
      <span className="mono text-[13px] text-t1">Week of {report.week_start}</span>
      <span className="text-[11px] text-t3">Published {report.published_at?.slice(0, 10)}</span>
    </button>
  );
}

export default function TransparencyPage() {
  const [selected, setSelected] = useState<string | null>(null);
  const { data, isLoading, error } = useQuery({
    queryKey: ["weekly-reports-public"],
    queryFn: fetchWeeklyReportsPublic,
  });

  const reports = data ?? [];
  const activeId = selected ?? reports[0]?.id ?? null;

  return (
    <div className="mx-auto flex max-w-[900px] flex-col gap-sp5 px-sp5 py-sp8">
      <div>
        <h1 className="text-[24px] font-bold text-t1">Transparency</h1>
        <p className="mt-sp2 max-w-[70ch] text-[13px] text-t2">
          Every Monday, GlassBox summarizes the past week's verification gate (A6) and compliance filter (A7) results:
          how many committee runs were checked, what share of numeric claims fully passed source verification, the
          checks and metrics that failed most, and compliance actions taken. Counts only — no raw payloads, no user
          data. A report is published here only after an admin review.
        </p>
      </div>

      {isLoading && <p className="text-[13px] text-t3">Loading…</p>}
      {error && <p className="text-[13px] text-red">Couldn’t load the transparency reports.</p>}
      {!isLoading && reports.length === 0 && <p className="text-[13px] text-t3">No reports have been published yet.</p>}

      {reports.length > 0 && (
        <div className="flex flex-col gap-sp4">
          <div className="flex flex-col gap-sp2">
            {reports.map((r) => (
              <ReportRow key={r.id} report={r} selected={r.id === activeId} onSelect={() => setSelected(r.id)} />
            ))}
          </div>
          {activeId && <ReportDetail id={activeId} />}
        </div>
      )}
    </div>
  );
}
