"use client";

import { Fragment, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, apiFetch } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import type { CommitteePreview, CommitteeRun, CommitteeRunsResponse, CommitteeScorecard } from "@/lib/types";

const pct = (n: number | null | undefined) => (n === null || n === undefined ? "—" : `${n >= 0 ? "+" : ""}${(n * 100).toFixed(2)}%`);
const badge = (d: string | null | undefined) =>
  d === "BUY" ? "text-teal" : d === "SELL" ? "text-red" : d === "HOLD" ? "text-t2" : "text-t3";

function Votes({ v }: { v: CommitteeRun["votes"] }) {
  if (!v) return <span className="text-t3">—</span>;
  return (
    <span className="mono text-[11px] text-t2">
      <span className="text-teal">B {v.BUY.toFixed(1)}</span> · <span className="text-red">S {v.SELL.toFixed(1)}</span> · H {v.HOLD.toFixed(1)}
    </span>
  );
}

/** The daily Investment Committee: what it reviewed, what it decided, whether it
 * agreed with the plain engine, and (once enough history exists) how its calls did. */
export function CommitteePanel() {
  const { token } = useAuth();
  const qc = useQueryClient();
  const [open, setOpen] = useState<string | null>(null);

  const runs = useQuery({
    queryKey: ["committee-runs"],
    queryFn: () => apiFetch<CommitteeRunsResponse>("/api/admin/committee/runs?limit=30", { token: token ?? undefined }),
    enabled: !!token,
    refetchInterval: (q) => (q.state.data?.running ? 5_000 : 30_000),
  });
  const score = useQuery({
    queryKey: ["committee-scorecard"],
    queryFn: () => apiFetch<CommitteeScorecard>("/api/admin/committee/scorecard", { token: token ?? undefined }),
    enabled: !!token,
    retry: false,
    staleTime: 10 * 60 * 1000,
  });
  const preview = useMutation({
    mutationFn: () => apiFetch<CommitteePreview>("/api/admin/committee/run", { method: "POST", token: token ?? undefined, body: JSON.stringify({ dry_run: true }) }),
  });
  const start = useMutation({
    mutationFn: () => apiFetch<{ started: boolean }>("/api/admin/committee/run", { method: "POST", token: token ?? undefined, body: "{}" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["committee-runs"] }),
  });

  const rows = runs.data?.runs ?? [];
  const running = !!runs.data?.running;
  const sc = score.data;
  const err = (e: unknown) => (e instanceof ApiError ? e.message : "failed");

  return (
    <div className="glass-panel p-sp4">
      <div className="flex flex-wrap items-start justify-between gap-sp3">
        <div>
          <h2 className="text-[13px] font-bold uppercase tracking-wide text-t3">Investment Committee — daily review</h2>
          <p className="mt-1 max-w-[72ch] text-[11px] text-t3">
            Every weekday at 22:15 UTC the committee (3 quant-engine agents + 7 AI analysts) reviews a few symbols: anything the engine flags BUY/SELL, anything that just
            changed, topped up with the biggest movers. Each analyst is shown the real numbers and told to use only those. Decisions are saved so they can be scored.
          </p>
        </div>
        <div className="flex gap-sp2">
          <button type="button" className="btn btn-ghost" disabled={preview.isPending} onClick={() => preview.mutate()}>
            {preview.isPending ? "…" : "Preview today’s picks"}
          </button>
          <button type="button" className="btn btn-primary" disabled={running || start.isPending} onClick={() => start.mutate()}>
            {running ? "Running…" : "Review now"}
          </button>
        </div>
      </div>

      {preview.isSuccess && (
        <p className="mt-sp3 text-[12px] text-t2">
          For {preview.data.date}: {preview.data.would_run.length ? preview.data.would_run.map((w) => `${w.symbol} (${w.why})`).join("; ") : "nothing new — already reviewed"}.
        </p>
      )}
      {preview.isError && <p className="mt-sp3 text-[12px] text-red">{err(preview.error)}</p>}
      {start.isError && <p className="mt-sp3 text-[12px] text-red">{err(start.error)}</p>}
      {start.isSuccess && !running && <p className="mt-sp3 text-[12px] text-teal">Started — decisions appear below as each symbol finishes (a full review takes a few minutes).</p>}

      {sc && sc.reliable_runs > 0 && (
        <p className="mt-sp3 text-[12px] text-t2">
          {sc.reliable_runs} reliable decision(s) so far · agreed with the engine {sc.agrees_with_engine === null ? "—" : `${Math.round(sc.agrees_with_engine * 100)}%`} of the time ·{" "}
          {(["BUY", "SELL"] as const).map((d) => {
            const c = sc.by_decision[d]["5"];
            return (
              <span key={d} className="mr-sp3">
                <span className={badge(d)}>{d}</span> 5-day: {c.n ? `${pct(c.mean_return)}, ${c.hit_rate === null ? "hit n/a" : `hit ${Math.round(c.hit_rate * 100)}%`} (n=${c.n})` : "no scored calls yet"}
              </span>
            );
          })}
        </p>
      )}

      <div className="mt-sp3 overflow-x-auto">
        {rows.length === 0 ? (
          <p className="py-sp3 text-[12px] text-t3">No reviews yet. The first one runs automatically on the next weekday at 22:15 UTC, or click “Review now”.</p>
        ) : (
          <table className="w-full min-w-[720px] text-[12px]">
            <thead>
              <tr className="text-left text-[10px] font-bold uppercase tracking-wide text-t3">
                <th className="px-sp3 py-sp2">Date</th>
                <th className="px-sp3 py-sp2">Symbol</th>
                <th className="px-sp3 py-sp2">Engine</th>
                <th className="px-sp3 py-sp2">Committee</th>
                <th className="px-sp3 py-sp2">Votes</th>
                <th className="px-sp3 py-sp2">Answered</th>
                <th className="px-sp3 py-sp2">Why reviewed</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <Fragment key={r.id}>
                  <tr className="cursor-pointer border-t border-border hover:bg-teal-dim/40" onClick={() => setOpen(open === r.id ? null : r.id)}>
                    <td className="mono px-sp3 py-sp2 text-t3">{r.date}</td>
                    <td className="px-sp3 py-sp2 font-semibold text-t1">{r.symbol}</td>
                    <td className={`px-sp3 py-sp2 font-semibold ${badge(r.engine_signal)}`}>{r.engine_signal}</td>
                    <td className={`px-sp3 py-sp2 font-bold ${badge(r.decision)}`}>
                      {r.decision ?? "failed"}
                      {r.agrees_with_engine === false && <span className="ml-1 text-[10px] font-semibold text-gold">≠ engine</span>}
                    </td>
                    <td className="px-sp3 py-sp2">
                      <Votes v={r.votes} />
                    </td>
                    <td className={`mono px-sp3 py-sp2 ${r.quorum_ok ? "text-t2" : "text-red"}`}>
                      {r.answered}/{r.total}
                      {!r.quorum_ok && " ⚠"}
                    </td>
                    <td className="px-sp3 py-sp2 text-t3">{r.why}</td>
                  </tr>
                  {open === r.id && (
                    <tr className="bg-bg2/50">
                      <td colSpan={7} className="px-sp4 py-sp3">
                        {r.error && <p className="mb-sp2 text-[11px] text-red">Error: {r.error}</p>}
                        <ul className="flex flex-col gap-1">
                          {r.agents.map((a) => (
                            <li key={a.agent} className="flex gap-sp3 text-[11px]">
                              <span className="w-44 shrink-0 font-semibold text-t1">{a.agent}</span>
                              {a.ok ? (
                                <>
                                  <span className={`w-10 shrink-0 font-bold ${badge(a.lean)}`}>{a.lean}</span>
                                  <span className="text-t3">{a.summary}</span>
                                </>
                              ) : (
                                <span className="text-red">did not answer — {a.error}</span>
                              )}
                            </li>
                          ))}
                        </ul>
                        <p className="mono mt-sp2 text-[10px] text-t3">
                          price {r.price.toFixed(2)} · {r.seconds}s · providers {Object.entries(r.providers).map(([p, n]) => `${p}×${n}`).join(", ") || "—"}
                        </p>
                      </td>
                    </tr>
                  )}
                </Fragment>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
