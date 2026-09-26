"use client";

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ApiError, fetchComplianceEvents, fetchComplianceRules } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import type { ComplianceAction, ComplianceRule } from "@/lib/types";

const ACTION_TONE: Record<string, string> = { blocked: "text-red", flagged: "text-gold", rewritten: "text-teal" };

const CHANNEL_LABEL: Record<string, string> = {
  committee_report: "Committee report",
  user_digest: "User digest",
  assistant: "Assistant",
};

function fmtDate(iso: string) {
  return iso.slice(0, 19).replace("T", " ");
}

function rulePatterns(r: ComplianceRule): string {
  if (r.kind === "requires") return r.pattern ?? "";
  return (r.patterns ?? []).join(" · ");
}

export function CompliancePanel() {
  const { token } = useAuth();
  const [actionFilter, setActionFilter] = useState<ComplianceAction | "">("");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");

  const events = useQuery({
    queryKey: ["admin-compliance-events", actionFilter, from, to],
    queryFn: () => fetchComplianceEvents(token ?? undefined, { action: actionFilter, from, to, limit: 200 }),
    enabled: !!token,
    refetchInterval: 30_000,
    retry: false,
  });

  const rules = useQuery({
    queryKey: ["admin-compliance-rules"],
    queryFn: () => fetchComplianceRules(token ?? undefined),
    enabled: !!token,
    retry: false,
  });

  return (
    <div className="flex flex-col gap-sp5">
      <section className="glass-panel p-sp4">
        <h2 className="text-[15px] font-bold text-t1">Compliance log</h2>
        <p className="mt-1 text-[11px] text-t3">
          Hits of the A7 compliance filter: the rule, the text it matched and the action taken. <b>Blocked</b> text must not be
          published, <b>flagged</b> text needs a look, <b>rewritten</b> means the missing disclaimer was appended. The filter is
          not yet wired into the outputs (T5 does that), so this log only fills once it is.
        </p>

        <div className="mt-sp3 flex flex-wrap items-center gap-sp2">
          <select
            value={actionFilter}
            onChange={(e) => setActionFilter(e.target.value as ComplianceAction | "")}
            className="rounded-r1 border border-border bg-bg2 px-sp2 py-sp1 text-[11px] text-t1"
            aria-label="Filter by action"
          >
            <option value="">All actions</option>
            <option value="blocked">Blocked</option>
            <option value="flagged">Flagged</option>
            <option value="rewritten">Rewritten</option>
          </select>
          <label className="text-[11px] text-t3">
            From{" "}
            <input
              type="date"
              value={from}
              onChange={(e) => setFrom(e.target.value)}
              className="rounded-r1 border border-border bg-bg2 px-sp2 py-sp1 text-[11px] text-t1"
            />
          </label>
          <label className="text-[11px] text-t3">
            To{" "}
            <input
              type="date"
              value={to}
              onChange={(e) => setTo(e.target.value)}
              className="rounded-r1 border border-border bg-bg2 px-sp2 py-sp1 text-[11px] text-t1"
            />
          </label>
        </div>

        {events.isPending && <p className="mt-sp2 text-[12px] text-t3">Loading…</p>}
        {events.isError && (
          <p className="mt-sp2 text-[12px] text-red">{events.error instanceof ApiError ? events.error.message : "Could not load compliance events."}</p>
        )}

        {events.data && (
          <div className="mt-sp3 overflow-x-auto">
            <table className="w-full text-left text-[11px]">
              <thead className="text-[9.5px] uppercase tracking-wider text-t3">
                <tr>
                  <th className="py-1 pr-sp3">Time (UTC)</th>
                  <th className="py-1 pr-sp3">Channel</th>
                  <th className="py-1 pr-sp3">Rule</th>
                  <th className="py-1 pr-sp3">Action</th>
                  <th className="py-1 pr-sp3">Matched text</th>
                  <th className="py-1">Run</th>
                </tr>
              </thead>
              <tbody>
                {events.data.map((e) => (
                  <tr key={e.id} className="border-t border-white/[0.04] hover:bg-bg3">
                    <td className="py-1 pr-sp3 font-mono text-t1">{fmtDate(e.created_at)}</td>
                    <td className="py-1 pr-sp3 text-t2">{CHANNEL_LABEL[e.channel] ?? e.channel}</td>
                    <td className="py-1 pr-sp3 font-mono text-t2">{e.rule_id}</td>
                    <td className={`py-1 pr-sp3 font-semibold ${ACTION_TONE[e.action] ?? "text-t2"}`}>{e.action}</td>
                    <td className="py-1 pr-sp3 text-t1">{e.matched_text ? <q>{e.matched_text}</q> : <span className="text-t3">—</span>}</td>
                    <td className="py-1 font-mono text-t3">{e.run_id ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {events.data.length === 0 && <p className="py-sp4 text-center text-[12px] text-t3">No compliance events match the filters.</p>}
          </div>
        )}
      </section>

      <section className="glass-panel p-sp4">
        <h3 className="text-[15px] font-bold text-t1">Rules</h3>
        <p className="mt-1 text-[11px] text-t3">
          Loaded from <code className="mono">backend/config/compliance_rules.json</code>. Matching is case-insensitive;{" "}
          <code className="mono">{"{{disclaimer}}"}</code> stands for the research disclaimer.
        </p>
        {rules.isPending && <p className="mt-sp2 text-[12px] text-t3">Loading…</p>}
        {rules.isError && (
          <p className="mt-sp2 text-[12px] text-red">{rules.error instanceof ApiError ? rules.error.message : "Could not load the rules."}</p>
        )}
        {rules.data && (
          <div className="mt-sp3 overflow-x-auto">
            <table className="w-full text-left text-[11px]">
              <thead className="text-[9.5px] uppercase tracking-wider text-t3">
                <tr>
                  <th className="py-1 pr-sp3">Rule</th>
                  <th className="py-1 pr-sp3">Kind</th>
                  <th className="py-1 pr-sp3">Action</th>
                  <th className="py-1">What it checks</th>
                </tr>
              </thead>
              <tbody>
                {rules.data.map((r) => (
                  <tr key={r.id} className="border-t border-white/[0.04] align-top">
                    <td className="py-1 pr-sp3 font-mono text-t1">{r.id}</td>
                    <td className="py-1 pr-sp3 text-t2">{r.kind}</td>
                    <td className="py-1 pr-sp3 text-t2">{r.action}</td>
                    <td className="py-1 text-t2">
                      <div>{r.description}</div>
                      <div className="mt-0.5 break-all font-mono text-[10px] text-t3">{rulePatterns(r)}</div>
                      {r.unless && <div className="mt-0.5 font-mono text-[10px] text-t3">unless: {r.unless}</div>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
