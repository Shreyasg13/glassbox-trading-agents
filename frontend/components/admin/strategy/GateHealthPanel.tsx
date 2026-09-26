"use client";

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ApiError, fetchGateHealth } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import type { GateHealthDay } from "@/lib/types";

const DEFAULT_DAYS = 30;

/** YYYY-MM-DD in the viewer's local calendar. */
function isoDay(d: Date) {
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

function pct(v: number | null) {
  return v === null ? "—" : `${(v * 100).toFixed(1)}%`;
}

/** Whole days since the Unix epoch for a YYYY-MM-DD string (UTC, so no DST drift). */
function dayNumber(iso: string) {
  const [y, m, d] = iso.split("-").map(Number);
  return Math.round(Date.UTC(y, m - 1, d) / 86_400_000);
}

// Chart geometry. The plot has a fixed pixel height and stretches to the panel width; axis labels are HTML so they stay
// readable at any width. Inside the SVG each day of the selected range is one SLOT-wide column (viewBox units), and the
// y axis runs 0 (bottom) to 100 (top) = 0–100%.
const PLOT_PX = 160;
const SLOT = 10;
const BAR = 7;
const MARK = 2.5; // height of the baseline marks (0% day / no claims checked), ≈4px at PLOT_PX
const TICKS = [0, 0.25, 0.5, 0.75, 1];

/** Per-day share of claims that passed every gate check, placed at each date's offset in from..to so weekends and
 * missed days show as gaps. The y axis is fixed at 0–100% so days compare honestly. */
function PassRateChart({ days, from, to }: { days: GateHealthDay[]; from: string; to: string }) {
  const start = dayNumber(from);
  const slots = Math.max(1, dayNumber(to) - start + 1);
  const w = slots * SLOT;

  return (
    <div>
      <div className="flex gap-sp2">
        <div className="relative w-[34px] shrink-0" style={{ height: PLOT_PX }} aria-hidden="true">
          {TICKS.map((t) => (
            <span key={t} className="absolute right-0 -translate-y-1/2 text-[10px] leading-none text-t3" style={{ top: `${(1 - t) * 100}%` }}>
              {Math.round(t * 100)}%
            </span>
          ))}
        </div>
        <svg
          viewBox={`0 0 ${w} 100`}
          preserveAspectRatio="none"
          className="block min-w-0 flex-1"
          style={{ height: PLOT_PX }}
          role="img"
          aria-label={`Daily claim pass rate from ${from} to ${to}, 0 to 100 percent`}
        >
          {TICKS.map((t) => (
            <line
              key={t}
              x1={0}
              x2={w}
              y1={100 * (1 - t)}
              y2={100 * (1 - t)}
              stroke="var(--c-border2)"
              strokeDasharray={t === 0 ? undefined : "2 4"}
              vectorEffect="non-scaling-stroke"
            />
          ))}
          {days.map((d) => {
            const off = dayNumber(d.date) - start;
            if (off < 0 || off >= slots) return null;
            const x = off * SLOT + (SLOT - BAR) / 2;
            const label =
              d.pass_rate === null
                ? `${d.date}: no claims checked · ${d.runs_ok}/${d.runs_checked} runs ok`
                : `${d.date}: ${pct(d.pass_rate)} verified (${d.claims_verified}/${d.claims_checked} claims) · ${d.runs_ok}/${d.runs_checked} runs ok`;
            return (
              <g key={d.date} className="group">
                <title>{label}</title>
                {/* Hit target: the whole column, so a 0% day or a no-claims day can still be hovered. */}
                <rect x={off * SLOT} y={0} width={SLOT} height={100} fill="transparent" className="group-hover:fill-[var(--c-bg3)]" />
                {d.pass_rate === null ? (
                  <rect x={x} y={100 - MARK} width={BAR} height={MARK} fill="var(--c-t3, #8a94a6)" />
                ) : d.pass_rate === 0 ? (
                  <rect x={x} y={100 - MARK} width={BAR} height={MARK} fill="var(--c-red)" />
                ) : (
                  <rect x={x} y={100 * (1 - d.pass_rate)} width={BAR} height={100 * d.pass_rate} fill="var(--c-teal)" />
                )}
              </g>
            );
          })}
        </svg>
      </div>
      <div className="ml-[42px] mt-1 flex justify-between text-[10px] text-t3">
        <span className="mono">{from}</span>
        <span className="mono">{to}</span>
      </div>
      <p className="ml-[42px] mt-1 flex flex-wrap gap-x-sp3 text-[10px] text-t3">
        <span><span className="mr-1 inline-block h-[8px] w-[8px] rounded-[2px] bg-teal align-middle" />share of claims verified</span>
        <span><span className="mr-1 inline-block h-[3px] w-[10px] bg-red align-middle" />0% verified</span>
        <span><span className="mr-1 inline-block h-[3px] w-[10px] bg-t3 align-middle" />no claims checked</span>
        <span>gaps: no committee runs that day</span>
      </p>
    </div>
  );
}

export function GateHealthPanel() {
  const { token } = useAuth();
  const today = new Date();
  const [to, setTo] = useState(isoDay(today));
  const [from, setFrom] = useState(isoDay(new Date(today.getFullYear(), today.getMonth(), today.getDate() - (DEFAULT_DAYS - 1))));
  const rangeOk = !!from && !!to && from <= to;

  const q = useQuery({
    queryKey: ["admin-gate-health", from, to],
    queryFn: () => fetchGateHealth(token ?? undefined, from, to),
    enabled: !!token && rangeOk,
    retry: false,
  });

  const data = q.data;
  const empty = !!data && data.days.length === 0 && data.top_checks.length === 0;

  return (
    <div className="flex flex-col gap-sp5">
      <section className="glass-panel p-sp4">
        <h2 className="text-[15px] font-bold text-t1">Gate health</h2>
        <p className="mt-1 text-[11px] text-t3">
          How the A6 verification gate is doing on committee runs (Ask and challenger runs excluded). A claim counts as verified only
          if every check on it passed; a staleness warning alone does not disqualify it.
        </p>

        <div className="mt-sp3 flex flex-wrap items-end gap-sp2">
          <div>
            <label htmlFor="gh-from" className="mb-1 block text-[10px] uppercase tracking-wider text-t3">From</label>
            <input
              id="gh-from"
              type="date"
              value={from}
              max={to || undefined}
              onChange={(e) => setFrom(e.target.value)}
              className="rounded-r1 border border-border bg-bg2 px-sp2 py-sp1 text-[11px] text-t1"
            />
          </div>
          <div>
            <label htmlFor="gh-to" className="mb-1 block text-[10px] uppercase tracking-wider text-t3">To</label>
            <input
              id="gh-to"
              type="date"
              value={to}
              min={from || undefined}
              onChange={(e) => setTo(e.target.value)}
              className="rounded-r1 border border-border bg-bg2 px-sp2 py-sp1 text-[11px] text-t1"
            />
          </div>
          <span className="pb-1 text-[10px] text-t3">up to 366 days</span>
        </div>

        {!rangeOk && <p className="mt-sp3 text-[12px] text-red">Choose a start date on or before the end date.</p>}
        {rangeOk && q.isPending && <p className="mt-sp3 text-[12px] text-t3">Loading…</p>}
        {rangeOk && q.isError && (
          <p className="mt-sp3 text-[12px] text-red">{q.error instanceof ApiError ? q.error.message : "Could not load gate health."}</p>
        )}
        {rangeOk && empty && <p className="py-sp6 text-center text-[13px] text-t3">No gate results in this range yet.</p>}

        {rangeOk && data && !empty && (
          <>
            <p className="mt-sp3 text-[12px] text-t2">
              <b className="text-t1">{pct(data.totals.pass_rate)}</b> of claims verified
              <span className="mono text-t3"> ({data.totals.claims_verified}/{data.totals.claims_checked})</span> · runs with no failed
              check: <span className="mono">{data.totals.runs_ok}/{data.totals.runs_checked}</span>
            </p>

            {data.days.length > 0 && (
              <div className="mt-sp3">
                <h3 className="mb-1 text-[10px] uppercase tracking-wider text-t3">Claims verified per day</h3>
                <PassRateChart days={data.days} from={data.from} to={data.to} />
                <details className="mt-sp2 text-[11px]">
                  <summary className="cursor-pointer text-t3">Per-day table</summary>
                  <table className="mt-sp2 w-full text-left">
                    <thead className="text-[9.5px] uppercase tracking-wider text-t3">
                      <tr>
                        <th className="py-1 pr-sp3">Date</th>
                        <th className="py-1 pr-sp3">Pass rate</th>
                        <th className="py-1 pr-sp3">Claims verified</th>
                        <th className="py-1 pr-sp3">Runs ok</th>
                      </tr>
                    </thead>
                    <tbody>
                      {data.days.map((d) => (
                        <tr key={d.date} className="border-t border-white/[0.04]">
                          <td className="py-1 pr-sp3 font-mono text-t1">{d.date}</td>
                          <td className="py-1 pr-sp3 font-mono text-t1">{pct(d.pass_rate)}</td>
                          <td className="py-1 pr-sp3 font-mono text-t2">{d.claims_verified}/{d.claims_checked}</td>
                          <td className="py-1 pr-sp3 font-mono text-t2">{d.runs_ok}/{d.runs_checked}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </details>
              </div>
            )}
          </>
        )}
      </section>

      {rangeOk && data && !empty && (
        <div className="grid gap-sp5 md:grid-cols-2">
          <section className="glass-panel p-sp4">
            <h2 className="text-[13px] font-bold text-t1">Top failing checks</h2>
            {data.top_checks.length === 0 ? (
              <p className="mt-sp2 text-[12px] text-t3">No failed checks in this range.</p>
            ) : (
              <table className="mt-sp2 w-full text-left text-[11px]">
                <thead className="text-[9.5px] uppercase tracking-wider text-t3">
                  <tr>
                    <th className="py-1 pr-sp3">Check</th>
                    <th className="py-1 pr-sp3">Failures</th>
                    <th className="py-1 pr-sp3">Most common reason</th>
                  </tr>
                </thead>
                <tbody>
                  {data.top_checks.map((c) => (
                    <tr key={c.check_type} className="border-t border-white/[0.04]">
                      <td className="py-1 pr-sp3 font-mono text-t1">{c.check_type}</td>
                      <td className="py-1 pr-sp3 font-mono text-t1">{c.failures}</td>
                      <td className="py-1 pr-sp3 text-t2">{c.top_reason ?? "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </section>
          <section className="glass-panel p-sp4">
            <h2 className="text-[13px] font-bold text-t1">Top failing metrics</h2>
            {data.top_metrics.length === 0 ? (
              <p className="mt-sp2 text-[12px] text-t3">No failed claim checks in this range.</p>
            ) : (
              <table className="mt-sp2 w-full text-left text-[11px]">
                <thead className="text-[9.5px] uppercase tracking-wider text-t3">
                  <tr>
                    <th className="py-1 pr-sp3">Metric</th>
                    <th className="py-1 pr-sp3">Failed checks</th>
                    <th className="py-1 pr-sp3">Claims</th>
                  </tr>
                </thead>
                <tbody>
                  {data.top_metrics.map((m) => (
                    <tr key={m.metric} className="border-t border-white/[0.04]">
                      <td className="py-1 pr-sp3 font-mono text-t1">{m.metric}</td>
                      <td className="py-1 pr-sp3 font-mono text-t1">{m.failures}</td>
                      <td className="py-1 pr-sp3 font-mono text-t2">{m.claims}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </section>
        </div>
      )}
    </div>
  );
}
