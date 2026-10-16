"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, fetchWeeklyReportsAdmin, publishWeeklyReport } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import type { WeeklyReportDetail } from "@/lib/types";

const pct = (n: number | null) => (n === null ? "—" : `${(n * 100).toFixed(1)}%`);

function ReportCard({ report, token, onPublished }: { report: WeeklyReportDetail; token: string; onPublished: () => void }) {
  const publish = useMutation({
    mutationFn: () => publishWeeklyReport(token, report.id),
    onSuccess: onPublished,
  });
  const { body } = report;

  return (
    <div className="glass-panel flex flex-col gap-sp3 p-sp4">
      <div className="flex flex-wrap items-center justify-between gap-sp2">
        <div>
          <h2 className="mono text-[14px] font-bold text-t1">Week of {report.week_start}</h2>
          <span className={`text-[11px] font-semibold uppercase ${report.status === "published" ? "text-teal" : "text-t3"}`}>
            {report.status}
            {report.status === "published" && report.published_at && ` · ${report.published_at.slice(0, 10)} by ${report.published_by}`}
          </span>
        </div>
        {report.status === "draft" && (
          <button type="button" className="btn btn-primary" disabled={publish.isPending} onClick={() => publish.mutate()}>
            {publish.isPending ? "Publishing…" : "Publish"}
          </button>
        )}
      </div>

      {publish.isError && (
        <p className="text-[11px] text-red">{publish.error instanceof ApiError ? publish.error.message : "Publish failed."}</p>
      )}

      <div className="grid gap-sp3 sm:grid-cols-3">
        <div>
          <div className="text-[11px] uppercase text-t3">Runs checked</div>
          <div className="mono text-[16px] text-t1">{body.runs_checked}</div>
        </div>
        <div>
          <div className="text-[11px] uppercase text-t3">A6 pass rate</div>
          <div className="mono text-[16px] text-t1">
            {pct(body.a6.pass_rate)} <span className="text-[11px] text-t3">({body.a6.claims_verified}/{body.a6.claims_checked})</span>
          </div>
        </div>
        <div>
          <div className="text-[11px] uppercase text-t3">A7 events</div>
          <div className="mono text-[13px] text-t1">
            {body.a7_by_action.blocked} blocked · {body.a7_by_action.rewritten} rewritten · {body.a7_by_action.flagged} flagged
          </div>
        </div>
      </div>

      <div className="grid gap-sp3 sm:grid-cols-2">
        <div>
          <h3 className="mb-1 text-[11px] font-bold uppercase text-t3">Top failing checks</h3>
          {body.top_failing_checks.length === 0 && <p className="text-[12px] text-t3">None.</p>}
          <ul className="flex flex-col gap-1 text-[12px] text-t2">
            {body.top_failing_checks.map((c) => (
              <li key={c.check_type}>
                {c.check_type} — {c.failures} ({c.top_reason ?? "—"})
              </li>
            ))}
          </ul>
        </div>
        <div>
          <h3 className="mb-1 text-[11px] font-bold uppercase text-t3">Top failing metrics</h3>
          {body.top_failing_metrics.length === 0 && <p className="text-[12px] text-t3">None.</p>}
          <ul className="flex flex-col gap-1 text-[12px] text-t2">
            {body.top_failing_metrics.map((m) => (
              <li key={m.metric}>
                {m.metric} — {m.failures} ({m.claims} claim{m.claims === 1 ? "" : "s"})
              </li>
            ))}
          </ul>
        </div>
      </div>
    </div>
  );
}

export default function AdminWeeklyReportsPage() {
  const { token } = useAuth();
  const qc = useQueryClient();
  const { data, isLoading, error } = useQuery({
    queryKey: ["weekly-reports-admin"],
    queryFn: () => fetchWeeklyReportsAdmin(token ?? undefined),
    enabled: !!token,
  });

  return (
    <div className="flex flex-col gap-sp5">
      <div>
        <h1 className="text-[18px] font-bold text-t1">Weekly Discrepancy Reports</h1>
        <p className="mt-1 max-w-[70ch] text-[12px] text-t3">
          A draft is created every Monday from the A6 verification gate and A7 compliance filter results of the
          previous week. Review it here, then publish it to the public transparency page. A published report is
          read-only.
        </p>
      </div>

      {isLoading && <p className="text-[13px] text-t3">Loading…</p>}
      {error && <p className="text-[13px] text-red">Couldn’t load weekly reports.</p>}
      {!isLoading && (data ?? []).length === 0 && <p className="text-[13px] text-t3">No weekly reports yet.</p>}

      {token && (
        <div className="flex flex-col gap-sp4">
          {data?.map((r) => (
            <ReportCard key={r.id} report={r} token={token} onPublished={() => qc.invalidateQueries({ queryKey: ["weekly-reports-admin"] })} />
          ))}
        </div>
      )}
    </div>
  );
}
