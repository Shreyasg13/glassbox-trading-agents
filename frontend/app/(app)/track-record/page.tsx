"use client";

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ApiError, apiFetch, fetchLedgerTrackRecord } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import type { TrackRecord, LedgerTrackRecord, LedgerMetrics, LedgerScoredCall } from "@/lib/types";
import { MultiCurveChart } from "@/components/charts/MultiCurveChart";
import { Section, Th, WrapperToggle, inWrapper, pct, plainPct, tone, type Wrapper } from "@/components/admin/strategy/shared";

const FRIENDLY: Record<string, { name: string; what: string }> = {
  ctl_engine: { name: "Signal engine", what: "Trades the engine's BUY / SELL / HOLD signals across the whole universe." },
  ctl_taxaware: { name: "Tax-aware engine", what: "Built for a taxable account: sells losses and long-term gains first, holds short-term gains unless risk turns high, acts only on signals that last, trades less." },
  ctl_trend: { name: "Trend filter (200-day)", what: "Holds each stock only while its price at last month-end was above its 200-day average; otherwise cash. Checked monthly, so it rarely trades." },
  ctl_trend_ira: { name: "Trend filter in an IRA / 401k", what: "The same trades as the trend filter, inside a tax-sheltered account." },
  ctl_voltarget: { name: "Volatility-targeted", what: "Same stocks, but the whole position shrinks when markets turn turbulent and is restored when calm, aiming for steady risk. Never borrows." },
  ctl_voltarget_ira: { name: "Volatility-targeted in an IRA / 401k", what: "The same trades as the volatility-targeted strategy, inside a tax-sheltered account." },
  ctl_engine_ira: { name: "Engine in an IRA / 401k", what: "The same trades as the signal engine, inside a tax-sheltered account: no tax on trading." },
  ctl_placebo_ira: { name: "Placebo in an IRA / 401k", what: "The scrambled-signal placebo inside a sheltered account: the bar the sheltered engine has to clear." },
  ctl_committee: { name: "Engine + committee", what: "Same, but where the AI committee reviewed a stock it follows the committee's risk-checked call. Only differs on live days." },
  ctl_placebo: { name: "Placebo", what: "The engine acting on scrambled signals. If the real engine can't beat this, its signals carry no information." },
  ctl_spy: { name: "The market (SPY)", what: "Buy once, never trade. The bar every strategy has to clear." },
  ctl_equal: { name: "Equal-weight hold", what: "Buy every tracked stock in equal parts, never trade." },
  ctl_cash: { name: "Cash", what: "Do nothing. The floor." },
};

/** The system's own simulated track record, with the caveats attached to the numbers rather
 * than hidden below them: the backtest is in-sample, the live stretch is the only real evidence,
 * and tax changes the ranking. */
export default function TrackRecordPage() {
  const { token } = useAuth();
  const [wrapper, setWrapper] = useState<Wrapper>("taxable");
  const q = useQuery({
    queryKey: ["track-record"],
    queryFn: () => apiFetch<TrackRecord>("/api/me/track-record", { token: token ?? undefined }),
    enabled: !!token,
    staleTime: 5 * 60 * 1000,
    retry: false,
  });
  const ledgerQ = useQuery({
    queryKey: ["ledger-track-record"],
    queryFn: () => fetchLedgerTrackRecord(token ?? undefined),
    enabled: !!token,
    staleTime: 5 * 60 * 1000,
    retry: false,
  });

  if (q.isPending) return <p className="py-sp10 text-center text-[13px] text-t3">Loading…</p>;
  if (q.isError) return <p className="py-sp10 text-center text-[13px] text-red">{q.error instanceof ApiError ? q.error.message : "Could not load the track record"}</p>;
  const t = q.data;
  if (!t.initialised || !t.strategies.length) return <p className="py-sp10 text-center text-[13px] text-t3">The simulated track record has not started yet.</p>;

  const liveDays = Math.max(...t.strategies.map((s) => s.live_days));
  const rows = t.strategies.filter((s) => inWrapper(s, wrapper));
  const taxable = wrapper === "taxable";
  const series = rows.filter((s) => t.curves[s.id]).map((s) => ({ id: s.id, label: FRIENDLY[s.id]?.name ?? s.name, curve: t.curves[s.id] }));
  const engine = t.strategies.find((s) => s.id === "ctl_engine");
  const market = t.strategies.find((s) => s.id === "ctl_spy");

  return (
    <div className="flex flex-col gap-sp5">
      <header>
        <h1 className="text-[22px] font-extrabold tracking-tight text-t1">Track record</h1>
        <p className="mt-1 max-w-[80ch] text-[12px] text-t3">
          How the system&rsquo;s rules would have performed as simulated portfolios. Nothing here is real money, and nothing here is advice.
        </p>
      </header>

      <div className="rounded-r3 border border-gold/40 bg-gold-dim/40 p-sp4 text-[12px] leading-snug text-t2">
        <b className="text-gold">How much to trust this.</b> Everything before the dashed line is a <b>backtest</b>: the engine&rsquo;s settings were tuned on that same history, so it looks better than it
        should. Only the stretch after {t.live_from ?? "the live date"} is genuine out-of-sample trading — and that is just <b>{liveDays} trading day{liveDays === 1 ? "" : "s"}</b> so far,
        far too few to conclude anything. It will fill in as days pass.
      </div>

      <WrapperToggle value={wrapper} onChange={setWrapper} />

      <Section title="Growth of $100" note="Each line starts at 100, before tax. Click a name to hide it.">
        <MultiCurveChart series={series} />
      </Section>

      <Section
        title={taxable ? "Results in a taxable account, after tax" : "Results in a tax-sheltered account (IRA · 401k · Roth)"}
        note={
          taxable
            ? `Long-term investing keeps more of what it earns: gains held over a year are taxed at a lower rate than short-term ones (assumed ${plainPct(t.tax_assumptions.long_term)} vs ${plainPct(t.tax_assumptions.short_term)} here, wash-sale rule applied — an estimate, not tax advice).`
            : "Inside these accounts trading is not taxed, so the pre-tax result is what you keep. Holding the market and cash owe no tax anywhere."
        }
      >
        <ul className="flex flex-col gap-sp3 md:hidden">
          {rows.map((s) => (
            <li key={s.id} className="rounded-r2 border border-border bg-bg2/40 p-sp3">
              <div className="text-[13px] font-semibold text-t1">{FRIENDLY[s.id]?.name ?? s.name}</div>
              <div className="mt-[2px] text-[10px] leading-snug text-t3">{FRIENDLY[s.id]?.what}</div>
              <dl className="mono mt-sp2 grid grid-cols-2 gap-x-sp3 gap-y-sp2 text-[12px]">
                <div><dt className="text-[9px] font-bold uppercase tracking-wide text-t3">Total return</dt><dd className={`font-semibold ${tone(s.total_return)}`}>{pct(s.total_return)}</dd></div>
                <div><dt className="text-[9px] font-bold uppercase tracking-wide text-t3">Live so far</dt><dd className={tone(s.live_return)}>{s.live_days ? pct(s.live_return, 2) : "—"}</dd></div>
                <div><dt className="text-[9px] font-bold uppercase tracking-wide text-t3">Worst drop</dt><dd className="text-red">{pct(s.max_drawdown)}</dd></div>
                <div><dt className="text-[9px] font-bold uppercase tracking-wide text-t3">{taxable ? "After tax, if sold" : "Trades / yr"}</dt><dd className={taxable ? `font-semibold ${tone(s.after_tax_liquidated_return)}` : "text-t2"}>{taxable ? (s.tax_tracked ? pct(s.after_tax_liquidated_return) : "—") : `${s.turnover.toFixed(1)}× capital`}</dd></div>
              </dl>
            </li>
          ))}
        </ul>
        <div className="hidden overflow-x-auto md:block">
          <table className="w-full min-w-[720px]">
            <thead>
              <tr>
                <Th>Strategy</Th>
                <Th right>Total return</Th>
                <Th right>Live so far</Th>
                <Th right>Worst drop</Th>
                <Th right>{taxable ? "After tax, if sold today" : "Total after costs"}</Th>
                <Th right>Trades per year</Th>
              </tr>
            </thead>
            <tbody>
              {rows.map((s) => (
                <tr key={s.id} className="border-t border-border text-[12px]">
                  <td className="px-sp3 py-sp2">
                    <div className="font-semibold text-t1">{FRIENDLY[s.id]?.name ?? s.name}</div>
                    <div className="max-w-[46ch] text-[10px] leading-snug text-t3">{FRIENDLY[s.id]?.what}</div>
                  </td>
                  <td className={`mono px-sp3 py-sp2 text-right font-semibold ${tone(s.total_return)}`}>{pct(s.total_return)}</td>
                  <td className={`mono px-sp3 py-sp2 text-right ${tone(s.live_return)}`}>{s.live_days ? pct(s.live_return, 2) : "—"}</td>
                  <td className="mono px-sp3 py-sp2 text-right text-red">{pct(s.max_drawdown)}</td>
                  <td className={`mono px-sp3 py-sp2 text-right font-semibold ${tone(s.after_tax_return)}`}>{taxable ? (s.tax_tracked ? pct(s.after_tax_liquidated_return) : "—") : pct(s.total_return)}</td>
                  <td className="mono px-sp3 py-sp2 text-right text-t3">{s.turnover.toFixed(1)}× capital</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Section>

      <Section title="What this analyses" note="Everything below uses public information anyone can check.">
        <ul className="grid gap-sp3 text-[12px] leading-snug text-t2 md:grid-cols-3">
          <li className="rounded-r2 border border-border bg-bg2/40 p-sp3">
            <b className="text-t1">Prices and trends.</b> Daily closes for 15 stocks and funds, turned into signals, a risk rating and how the stocks move together.
          </li>
          <li className="rounded-r2 border border-border bg-bg2/40 p-sp3">
            <b className="text-t1">Company filings.</b> Fundamentals and material events straight from the SEC&rsquo;s public filings, as of the date they were filed, never later.
          </li>
          <li className="rounded-r2 border border-border bg-bg2/40 p-sp3">
            <b className="text-t1">The economy.</b> US Treasury yields, unemployment and inflation from government sources. All of it is simulated research, not advice.
          </li>
        </ul>
      </Section>

      <Section title="How to read this">
        <ul className="grid gap-sp3 text-[12px] leading-snug text-t2 md:grid-cols-3">
          <li className="rounded-r2 border border-border bg-bg2/40 p-sp3">
            <b className="text-t1">Beat the placebo first.</b> If the engine can&rsquo;t clearly beat the scrambled-signal placebo over the live stretch, its signals aren&rsquo;t adding anything, whatever the backtest says.
          </li>
          <li className="rounded-r2 border border-border bg-bg2/40 p-sp3">
            <b className="text-t1">Then beat the market after tax.</b>
            {engine && market && engine.tax_tracked && market.tax_tracked
              ? ` If everything were sold today, the engine's after-tax total is ${pct(engine.after_tax_liquidated_return)} against ${pct(market.after_tax_liquidated_return)} for simply holding the market.`
              : " Trading more means realising more gains, and tax takes its share of every one."}
          </li>
          <li className="rounded-r2 border border-border bg-bg2/40 p-sp3">
            <b className="text-t1">Slow is a feature.</b> This is built for weeks-to-years decisions on daily closes. It does not day-trade: short-term trading is where fees, taxes and noise erase most edges.
          </li>
        </ul>
      </Section>

      {/* Ledger-scored calls section (S3 T12d) */}
      <section aria-labelledby="ledger-scored-heading">
        <h2 id="ledger-scored-heading" className="text-[16px] font-semibold text-t1 mb-sp3">Ledger-scored calls</h2>
        {ledgerQ.isPending && <p className="py-sp6 text-center text-[13px] text-t3">Loading ledger scores…</p>}
        {ledgerQ.isError && (
          <p className="py-sp6 text-center text-[13px] text-red">
            {ledgerQ.error instanceof ApiError ? ledgerQ.error.message : "Could not load ledger-scored calls"}
          </p>
        )}
        {ledgerQ.data && (
          <>
            <p className="mb-sp4 text-[12px] text-t3 leading-snug">
              {ledgerQ.data.ledger_calls} ledger call{ledgerQ.data.ledger_calls === 1 ? "" : "s"} recorded so far.
              {ledgerQ.data.scored_calls.length === 0 && " None have reached their outcome window yet."}
              <br />
              <span className="text-t3">{ledgerQ.data.pre_ledger_note}</span>
            </p>

            {ledgerQ.data.scored_calls.length > 0 && (
              <>
                <div className="grid gap-sp3 mb-sp4 sm:grid-cols-3">
                  {([1, 5, 20] as const).map((h) => {
                    const m: LedgerMetrics = ledgerQ.data.metrics[String(h)];
                    const count = m.count;
                    const showData = count > 0;
                    return (
                      <div key={h} className="rounded-r2 border border-border bg-bg2/40 p-sp3">
                        <div className="text-[10px] font-bold uppercase tracking-wide text-t3 mb-1">{h}-day horizon</div>
                        <dl className="grid grid-cols-2 gap-x-sp3 gap-y-1 text-[12px]">
                          <dt className="text-t3">Calls</dt>
                          <dd className="font-mono font-semibold text-t1 text-right">{count}</dd>
                          <dt className="text-t3">Hit rate</dt>
                          <dd className="font-mono font-semibold text-t1 text-right">
                            {showData && m.hit_rate !== null ? `${(m.hit_rate * 100).toFixed(1)}%` : "too few calls"}
                          </dd>
                          <dt className="text-t3">Mean excess</dt>
                          <dd className="font-mono font-semibold text-t1 text-right">
                            {showData && m.mean_excess_return !== null ? `${(m.mean_excess_return * 100).toFixed(2)}%` : "too few calls"}
                          </dd>
                          <dt className="text-t3">Rank IC</dt>
                          <dd className="font-mono font-semibold text-t1 text-right">
                            {showData && m.rank_ic !== null ? m.rank_ic.toFixed(3) : "too few calls"}
                          </dd>
                        </dl>
                      </div>
                    );
                  })}
                </div>

                <div className="overflow-x-auto">
                  <table className="w-full min-w-[720px]">
                    <thead>
                      <tr className="border-b border-border">
                        <Th>Date</Th>
                        <Th>Symbol</Th>
                        <Th>Call</Th>
                        <Th>Recorded at</Th>
                        <Th right>Horizon</Th>
                        <Th right>Forward return</Th>
                        <Th right>Excess return</Th>
                        <Th right>Right?</Th>
                      </tr>
                    </thead>
                    <tbody>
                      {ledgerQ.data.scored_calls
                        .slice()
                        .sort((a, b) => (a.recorded_at > b.recorded_at ? -1 : 1))
                        .map((call: LedgerScoredCall) => (
                          <tr key={call.call_id} className="border-t border-border text-[12px]">
                            <td className="mono px-sp3 py-sp2">{call.entry_date ?? "—"}</td>
                            <td className="px-sp3 py-sp2 font-mono font-semibold text-t1">{call.symbol}</td>
                            <td className="px-sp3 py-sp2">
                              <span
                                className={`inline-block px-1.5 py-0.5 rounded text-[10px] font-semibold ${
                                  call.decision === "BUY"
                                    ? "bg-green/20 text-green"
                                    : call.decision === "SELL"
                                    ? "bg-red/20 text-red"
                                    : "bg-amber/20 text-amber"
                                }`}
                              >
                                {call.decision}
                              </span>
                            </td>
                            <td className="mono px-sp3 py-sp2 text-t2">
                              {call.recorded_at}
                              <span className="block text-t3">{call.recorded_label}</span>
                            </td>
                            <td className="mono px-sp3 py-sp2 text-right text-t2">{call.horizon}d</td>
                            <td className="mono px-sp3 py-sp2 text-right font-semibold">
                              {call.forward_return !== null ? `${(call.forward_return * 100).toFixed(2)}%` : "—"}
                            </td>
                            <td className="mono px-sp3 py-sp2 text-right font-semibold">
                              {call.excess_return !== null
                                ? `${(call.excess_return * 100).toFixed(2)}%`
                                : "—"}
                            </td>
                            <td className="mono px-sp3 py-sp2 text-right font-semibold">
                              {call.right ? (
                                <span className="text-green">✓</span>
                              ) : (
                                <span className="text-red">✗</span>
                              )}
                            </td>
                          </tr>
                        ))}
                    </tbody>
                  </table>
                </div>
              </>
            )}

            {ledgerQ.data.scored_calls.length === 0 && (
              <div className="rounded-r2 border border-border bg-bg2/40 p-sp6 text-center text-[13px] text-t3">
                No scored calls yet. Calls appear here once their outcome window has closed.
              </div>
            )}
          </>
        )}
      </section>
    </div>
  );
}
