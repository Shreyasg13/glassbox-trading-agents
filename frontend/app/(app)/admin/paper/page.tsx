"use client";

import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, apiFetch } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { CommitteePanel } from "@/components/admin/CommitteePanel";
import { EquityCurveChart } from "@/components/admin/EquityCurveChart";
import type {
  PaperAccountDetail,
  PaperAccountSummary,
  PaperOverview,
  PaperRunResult,
  PaperScorecard,
} from "@/lib/types";

const pct = (n: number | null | undefined, digits = 1) => (n === null || n === undefined ? "—" : `${n >= 0 ? "+" : ""}${(n * 100).toFixed(digits)}%`);
const plainPct = (n: number, digits = 0) => `${(n * 100).toFixed(digits)}%`;
const money = (n: number) => `$${Math.round(n).toLocaleString()}`;
const tone = (n: number | null | undefined) => (n === null || n === undefined ? "text-t3" : n >= 0 ? "text-teal" : "text-red");

function Th({ children, right }: { children: React.ReactNode; right?: boolean }) {
  return <th className={`px-sp3 py-sp2 text-[10px] font-bold uppercase tracking-wide text-t3 ${right ? "text-right" : "text-left"}`}>{children}</th>;
}

function LeaderRow({ a, selected, onSelect }: { a: PaperAccountSummary; selected: boolean; onSelect: () => void }) {
  return (
    <tr
      onClick={onSelect}
      className={`cursor-pointer border-t border-border text-[12px] hover:bg-teal-dim/40 ${selected ? "bg-teal-dim/60" : ""}`}
      aria-selected={selected}
    >
      <td className="px-sp3 py-sp2">
        <div className="font-semibold text-t1">{a.name}</div>
        <div className="text-[10px] text-t3">{[a.profile?.archetype, a.risk_level].filter(Boolean).join(" · ") || a.strategy.replace("_", " ")}</div>
      </td>
      <td className="mono px-sp3 py-sp2 text-right text-t3">{money(a.start_equity)}</td>
      <td className="mono px-sp3 py-sp2 text-right text-t1">{money(a.equity)}</td>
      <td className={`mono px-sp3 py-sp2 text-right ${tone(a.profit)}`}>{`${a.profit < 0 ? "-" : "+"}${money(Math.abs(a.profit))}`}</td>
      <td className={`mono px-sp3 py-sp2 text-right font-semibold ${tone(a.total_return)}`}>{pct(a.total_return)}</td>
      <td className={`mono px-sp3 py-sp2 text-right ${tone(a.alpha)}`}>{pct(a.alpha)}</td>
      <td className={`mono px-sp3 py-sp2 text-right ${tone(a.live_return)}`}>{a.live_days ? `${pct(a.live_return)} · ${a.live_days}d` : "—"}</td>
      <td className="mono px-sp3 py-sp2 text-right text-red">{pct(a.max_drawdown)}</td>
      <td className="mono px-sp3 py-sp2 text-right text-t1">{a.sharpe.toFixed(2)}</td>
      <td className="mono px-sp3 py-sp2 text-right text-t3">{a.trade_count}</td>
      <td className="mono px-sp3 py-sp2 text-right text-t3">{money(a.cost_paid)}</td>
    </tr>
  );
}

function Leaderboard({ title, rows, selected, onSelect, note }: { title: string; rows: PaperAccountSummary[]; selected: string | null; onSelect: (id: string) => void; note?: string }) {
  if (!rows.length) return null;
  return (
    <div className="glass-panel overflow-hidden">
      <div className="px-sp4 pb-sp2 pt-sp3">
        <h2 className="text-[13px] font-bold uppercase tracking-wide text-t3">{title}</h2>
        {note && <p className="mt-1 text-[11px] text-t3">{note}</p>}
      </div>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[920px]">
          <thead>
            <tr>
              <Th>Account</Th>
              <Th right>Start</Th>
              <Th right>Equity</Th>
              <Th right>Profit</Th>
              <Th right>Return</Th>
              <Th right>Alpha vs benchmark</Th>
              <Th right>Live</Th>
              <Th right>Max DD</Th>
              <Th right>Sharpe</Th>
              <Th right>Trades</Th>
              <Th right>Costs</Th>
            </tr>
          </thead>
          <tbody>
            {rows.map((a) => (
              <LeaderRow key={a.id} a={a} selected={selected === a.id} onSelect={() => onSelect(a.id)} />
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function AccountPanel({ id, token, since }: { id: string; token: string; since: string }) {
  const { data, isLoading, error } = useQuery({
    queryKey: ["paper-account", id, since],
    queryFn: () => apiFetch<PaperAccountDetail>(`/api/admin/paper/accounts/${encodeURIComponent(id)}${since ? `?since=${since}` : ""}`, { token }),
  });
  if (isLoading) return <p className="text-[13px] text-t3">Loading account…</p>;
  if (error || !data) return <p className="text-[13px] text-red">Couldn’t load this account.</p>;
  const s = data.summary;
  const holdings = Object.entries(data.holdings).sort((a, b) => b[1].weight - a[1].weight);
  return (
    <div className="glass-panel flex flex-col gap-sp4 p-sp4">
      <div className="flex flex-wrap items-baseline justify-between gap-sp3">
        <div>
          <h2 className="text-[15px] font-bold text-t1">{s.name}</h2>
          {data.note && <p className="mt-1 max-w-[62ch] text-[11px] text-t3">{data.note}</p>}
        </div>
        <div className="mono text-right text-[12px] text-t3">
          {s.inception} → {s.last_date}
          <br />
          CAGR {pct(s.cagr)} · vol {plainPct(s.volatility)} · turnover {s.turnover.toFixed(1)}×/yr
        </div>
      </div>

      <EquityCurveChart
        curve={data.curve}
        benchmark={data.benchmark_curve}
        startCash={data.curve[0]?.[1] ?? 100000}
        label={s.name}
        benchmarkLabel="Policy benchmark"
      />

      <div className="grid gap-sp4 md:grid-cols-2">
        <div>
          <h3 className="mb-sp2 text-[11px] font-bold uppercase tracking-wide text-t3">Holdings today</h3>
          {holdings.length === 0 && <p className="text-[12px] text-t3">All cash.</p>}
          <ul className="flex flex-col gap-1">
            {holdings.map(([sym, h]) => (
              <li key={sym} className="flex items-center gap-sp3 text-[12px]">
                <span className="w-14 font-semibold text-t1">{sym}</span>
                <span className="relative h-2 flex-1 overflow-hidden rounded-full bg-border">
                  <span className="absolute inset-y-0 left-0 rounded-full bg-teal" style={{ width: `${Math.min(100, h.weight * 100)}%` }} />
                </span>
                <span className="mono w-12 text-right text-t1">{plainPct(h.weight)}</span>
                <span className="mono w-20 text-right text-t3">{money(h.value)}</span>
              </li>
            ))}
            <li className="flex items-center gap-sp3 text-[12px] text-t3">
              <span className="w-14">Cash</span>
              <span className="flex-1" />
              <span className="mono w-12 text-right">{plainPct(s.cash_weight)}</span>
              <span className="mono w-20 text-right">{money(data.cash)}</span>
            </li>
          </ul>
        </div>
        <div>
          <h3 className="mb-sp2 text-[11px] font-bold uppercase tracking-wide text-t3">Recent trades</h3>
          {data.recent_trades.length === 0 && <p className="text-[12px] text-t3">No trades yet.</p>}
          <ul className="flex flex-col gap-1">
            {data.recent_trades.slice(0, 8).map((t, i) => (
              <li key={`${t.date}-${t.symbol}-${i}`} className="flex items-baseline gap-sp2 text-[12px]">
                <span className="mono w-[5.5rem] text-t3">{t.date}</span>
                <span className={`w-9 font-bold ${t.side === "BUY" ? "text-teal" : "text-red"}`}>{t.side}</span>
                <span className="w-12 font-semibold text-t1">{t.symbol}</span>
                <span className="mono text-t3">
                  {t.shares.toFixed(1)} @ {t.price.toFixed(2)}
                </span>
                <span className="ml-auto truncate text-[10px] text-t3">{t.reason}</span>
              </li>
            ))}
          </ul>
        </div>
      </div>
    </div>
  );
}

function Scorecard({ token }: { token: string }) {
  const { data, error } = useQuery({
    queryKey: ["paper-scorecard"],
    queryFn: () => apiFetch<PaperScorecard>("/api/admin/paper/scorecard", { token }),
    staleTime: 10 * 60 * 1000,
    retry: false,
  });
  if (error) return <p className="text-[12px] text-t3">Signal scorecard unavailable ({error instanceof ApiError ? error.message : "error"}).</p>;
  if (!data) return <p className="text-[12px] text-t3">Computing scorecard…</p>;
  const hs = data.horizons.map(String);
  const rows: ("BUY" | "SELL" | "HOLD" | "ALL")[] = ["BUY", "SELL", "HOLD", "ALL"];
  return (
    <div className="glass-panel p-sp4">
      <h2 className="text-[13px] font-bold uppercase tracking-wide text-t3">Is the engine’s signal any good?</h2>
      <p className="mb-sp3 mt-1 text-[11px] text-t3">
        Forward return after each historical signal, entered at the next close. In-sample (parameters were fitted on this history) — treat as an upper bound. A good BUY beats “ALL”; a good SELL is followed by <em>less</em> than average.
      </p>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[520px] text-[12px]">
          <thead>
            <tr>
              <Th>Signal</Th>
              {hs.map((h) => (
                <Th key={h} right>
                  {h}-day mean · hit rate · n
                </Th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((sig) => (
              <tr key={sig} className="border-t border-border">
                <td className="px-sp3 py-sp2 font-bold text-t1">{sig}</td>
                {hs.map((h) => {
                  const c = data.signals[sig][h];
                  return (
                    <td key={h} className="mono px-sp3 py-sp2 text-right text-t1">
                      {pct(c.mean_return, 2)} · {c.hit_rate === null ? "—" : plainPct(c.hit_rate)} · <span className="text-t3">{c.n.toLocaleString()}</span>
                    </td>
                  );
                })}
              </tr>
            ))}
            {(["BUY", "SELL"] as const).map((sig) => (
              <tr key={`edge-${sig}`} className="border-t border-border">
                <td className="px-sp3 py-sp2 text-t3">{sig} edge vs average</td>
                {hs.map((h) => {
                  const e = data.edge_vs_average[sig][h];
                  return (
                    <td key={h} className={`mono px-sp3 py-sp2 text-right font-semibold ${tone(e)}`}>
                      {e === null ? "—" : `${e >= 0 ? "+" : ""}${(e * 100).toFixed(2)} pts`}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export default function AdminPaperPage() {
  const { token } = useAuth();
  const qc = useQueryClient();
  const [selected, setSelected] = useState<string | null>(null);
  const [since, setSince] = useState(""); // "" = whole history (backtest + live)

  const overview = useQuery({
    queryKey: ["paper-overview", since],
    queryFn: () => apiFetch<PaperOverview>(`/api/admin/paper/overview${since ? `?since=${since}` : ""}`, { token: token ?? undefined }),
    enabled: !!token,
  });

  const run = useMutation({
    mutationFn: (bootstrap: boolean) =>
      apiFetch<PaperRunResult>("/api/admin/paper/run", { method: "POST", token: token ?? undefined, body: JSON.stringify({ bootstrap }) }),
    onSuccess: () => qc.invalidateQueries({ predicate: (q) => String(q.queryKey[0]).startsWith("paper-") }),
  });

  const accounts = overview.data?.accounts ?? [];
  const profiles = accounts.filter((a) => a.kind === "profile");
  const benchmarks = accounts.filter((a) => a.kind === "benchmark");
  const controls = accounts.filter((a) => a.kind === "control");
  const twins = accounts.filter((a) => a.kind === "twin");
  const meta = overview.data?.meta;

  useEffect(() => {
    if (!selected && accounts.length) setSelected((profiles[0] ?? accounts[0]).id);
  }, [selected, accounts, profiles]);

  const initialised = overview.data?.initialised;

  return (
    <div className="flex flex-col gap-sp5">
      <div className="flex flex-wrap items-start justify-between gap-sp3">
        <div>
          <h1 className="text-[18px] font-bold text-t1">Paper Trading</h1>
          <p className="mt-1 max-w-[70ch] text-[12px] text-t3">
            Every profile trades a simulated $ portfolio on the engine’s signals, next to a same-universe benchmark and control strategies, so you can see
            whether the signals add anything. <strong className="text-t2">Simulated — not real money, not advice.</strong> Orders decided at a close fill at the next close.
          </p>
          {meta && (
            <p className="mono mt-1 text-[11px] text-t3">
              Backtest from {meta.start} (in-sample) · live since {meta.live_from} (out-of-sample) · data through {meta.last_date ?? "—"}
            </p>
          )}
          {meta && (
            <div className="mt-sp2 flex flex-wrap items-center gap-sp2 text-[12px] text-t2">
              <label htmlFor="paper-since">Measure from</label>
              <input
                id="paper-since"
                type="date"
                value={since}
                min={meta.start}
                max={meta.last_date ?? undefined}
                onChange={(e) => setSince(e.target.value)}
                className="rounded-r1 border border-border bg-bg2 px-sp3 py-sp2 text-[13px] text-t1"
              />
              {[["All history", ""], ["Live start", meta.live_from]].map(([label, d]) => (
                <button key={label} type="button" className={`btn ${since === d ? "btn-primary" : "btn-ghost"}`} onClick={() => setSince(d)}>
                  {label}
                </button>
              ))}
              {since && <span className="text-[11px] text-t3">Start = equity at the close before {since}; every column covers {since} onward.</span>}
            </div>
          )}
        </div>
        <div className="flex flex-col items-end gap-1">
          <button
            type="button"
            className="btn btn-primary"
            disabled={run.isPending || overview.isLoading}
            onClick={() => run.mutate(!initialised)}
          >
            {run.isPending ? "Running…" : initialised ? "Run cycle now" : "Initialise & backfill history"}
          </button>
          {run.isSuccess && (
            <span className="text-[11px] text-teal">
              Done — {run.data.new_live_days.length} new live day(s), {run.data.reports_written} report(s) written.
            </span>
          )}
          {run.isError && <span className="text-[11px] text-red">{run.error instanceof ApiError ? run.error.message : "Run failed."}</span>}
        </div>
      </div>

      {overview.isLoading && <p className="text-[13px] text-t3">Loading…</p>}
      {overview.isError && <p className="text-[13px] text-red">Couldn’t load paper-trading data.</p>}
      {overview.data && !initialised && (
        <div className="glass-panel p-sp5 text-[13px] text-t2">
          Nothing has been simulated yet. “Initialise &amp; backfill history” creates an account for every demo profile and control, replays the last few years
          of prices through them, and starts the live record from today.
        </div>
      )}

      {initialised && token && (
        <>
          <Leaderboard
            title="Profiles"
            rows={profiles}
            selected={selected}
            onSelect={setSelected}
            note="Alpha = the profile’s return minus its policy benchmark: same weights, same rebalancing, signals ignored. Positive alpha means the signals helped."
          />
          {selected && <AccountPanel id={selected} token={token} since={since} />}
          <Leaderboard
            title="Committee-run twins"
            rows={twins}
            selected={selected}
            onSelect={setSelected}
            note="Same stocks and cash as each profile, but follows the AI Investment Committee where it reviewed a symbol. Alpha here = versus the engine-only profile, so it is what the committee added."
          />
          <Leaderboard title="Controls" rows={controls} selected={selected} onSelect={setSelected} note="The placebo runs the engine on other symbols’ signals. If the engine can’t beat it, its signals carry no information." />
          <Leaderboard title="Policy benchmarks" rows={benchmarks} selected={selected} onSelect={setSelected} />
          <Scorecard token={token} />
          <CommitteePanel />
        </>
      )}
    </div>
  );
}
