"use client";

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ApiError, apiFetch } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import type { LeaderRow, StrategyAccuracy } from "@/lib/types";
import { LeanChip, RiskChip, Section, Th, pct, plainPct, tone } from "./shared";

const x = (n: number | null | undefined) => (n === null || n === undefined ? "—" : `×${n.toFixed(2)}`);

function Card({ title, badge, children }: { title: string; badge: React.ReactNode; children: React.ReactNode }) {
  return (
    <div className="rounded-r3 border border-border bg-bg2/40 p-sp4">
      <div className="mb-sp2 flex items-start justify-between gap-sp2">
        <h3 className="text-[12px] font-bold text-t1">{title}</h3>
        {badge}
      </div>
      {children}
    </div>
  );
}

const Tag = ({ children, warn }: { children: React.ReactNode; warn?: boolean }) => (
  <span className={`rounded-r1 px-2 py-[1px] text-[9px] font-bold uppercase tracking-wide ${warn ? "bg-gold-dim text-gold" : "bg-teal-dim text-teal"}`}>{children}</span>
);

function Leaderboard({ lb }: { lb: StrategyAccuracy["leaderboard"] }) {
  if (!lb.agents.length) return <p className="text-[12px] text-t3">No agent answers on record yet — the leaderboard fills in as the daily committee runs.</p>;
  const row = (a: LeaderRow) => (
    <tr key={a.agent} className="border-t border-border text-[12px]">
      <td className="px-sp3 py-sp2">
        <div className="font-semibold text-t1">{a.agent}</div>
        <div className="text-[10px] text-t3">{a.type === "deterministic" ? "engine agent" : "AI analyst"}</div>
        {a.type === "llm" && (
          <div className="text-[10px] text-t3" title={Object.entries(a.models).map(([m, n]) => `${m}: ${n}`).join("\n")}>
            {Object.keys(a.models)[0] ?? "—"} · answered {plainPct(a.answer_rate)} · failover {plainPct(a.failover_rate)}
          </div>
        )}
      </td>
      <td className="mono px-sp3 py-sp2 text-right text-t2">{a.answers}</td>
      <td className="mono px-sp3 py-sp2 text-right text-t2">{plainPct(a.agrees_with_committee)}</td>
      <td className="mono px-sp3 py-sp2 text-right text-t2">{plainPct(a.agrees_with_engine)}</td>
      <td className="mono px-sp3 py-sp2 text-right text-t3">{a.avg_confidence === null ? "—" : `${Math.round(a.avg_confidence)}%`}</td>
      <td className="mono px-sp3 py-sp2 text-right text-t3">{a.directional_calls}</td>
      <td className="mono px-sp3 py-sp2 text-right text-t2">{a.hit_rate === null ? "—" : plainPct(a.hit_rate)}</td>
      <td className={`mono px-sp3 py-sp2 text-right font-semibold ${tone(a.mean_edge)}`}>{pct(a.mean_edge, 2)}</td>
      <td className="mono px-sp3 py-sp2 text-right text-t2">
        {a.pivotal}
        {a.pivotal_edge !== null && <span className={`ml-1 ${tone(a.pivotal_edge)}`}>{pct(a.pivotal_edge, 2)}</span>}
      </td>
      <td
        className="mono px-sp3 py-sp2 text-right text-t2"
        title={a.calibration_n ? `said ${plainPct(a.mean_p_beat_spy)} on average, beat SPY ${plainPct(a.beat_rate)} of the time` : undefined}
      >
        {a.brier === null ? "—" : `${a.brier.toFixed(3)} (n=${a.calibration_n})`}
      </td>
      <td className="px-sp3 py-sp2">{a.ranked ? <Tag>ranked</Tag> : <Tag warn>need {lb.min_ranked - a.directional_calls} more calls</Tag>}</td>
    </tr>
  );
  return (
    <>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[1000px]">
          <thead>
            <tr>
              <Th>Agent</Th>
              <Th right>Answers</Th>
              <Th right>With committee</Th>
              <Th right>With engine</Th>
              <Th right>Says it is</Th>
              <Th right>BUY/SELL calls</Th>
              <Th right>Hit rate</Th>
              <Th right>Edge per call</Th>
              <Th right>Pivotal</Th>
              <Th right>Calibration</Th>
              <Th>Status</Th>
            </tr>
          </thead>
          <tbody>{lb.agents.map(row)}</tbody>
        </table>
      </div>
      <p className="mt-sp2 text-[11px] text-t3">{lb.note}</p>
    </>
  );
}

/** "Is it actually working?" -- the engine signal, the risk signal and the committee, each with
 * its sample size, and the per-agent leaderboard that stays unranked until it means something. */
export function AccuracyPanel() {
  const { token } = useAuth();
  const [h, setH] = useState<"5" | "20">("20");
  const q = useQuery({
    queryKey: ["strategy-accuracy"],
    queryFn: () => apiFetch<StrategyAccuracy>("/api/admin/strategy/accuracy", { token: token ?? undefined }),
    enabled: !!token,
    staleTime: 10 * 60 * 1000,
    retry: false,
  });
  if (q.isPending) return <p className="py-sp6 text-center text-[13px] text-t3">Crunching ten years of history… (first load takes a few seconds)</p>;
  if (q.isError) return <p className="py-sp6 text-center text-[13px] text-red">{q.error instanceof ApiError ? q.error.message : "Could not load accuracy"}</p>;
  const a = q.data;
  const sig = a.signal;
  const risk = a.risk;
  const com = a.committee;

  return (
    <div className="flex flex-col gap-sp5">
      <div className="grid gap-sp4 lg:grid-cols-3">
        <Card title="Engine BUY / SELL signal" badge={<Tag warn>in-sample</Tag>}>
          <p className="mb-sp2 text-[11px] text-t3">Parameters were fitted on this same history, so treat these numbers as optimistic. Live results start on the paper page.</p>
          <table className="w-full text-[12px]">
            <thead>
              <tr className="text-left text-[10px] font-bold uppercase tracking-wide text-t3">
                <th className="py-1">5-day</th>
                <th className="py-1 text-right">n</th>
                <th className="py-1 text-right">Avg move</th>
                <th className="py-1 text-right">Right</th>
              </tr>
            </thead>
            <tbody>
              {(["BUY", "SELL", "HOLD"] as const).map((s) => {
                const c = sig.signals[s]["5"];
                return (
                  <tr key={s} className="border-t border-border">
                    <td className="py-1"><LeanChip lean={s} small /></td>
                    <td className="mono py-1 text-right text-t3">{c.n.toLocaleString()}</td>
                    <td className={`mono py-1 text-right ${tone(c.mean_return)}`}>{pct(c.mean_return, 2)}</td>
                    <td className="mono py-1 text-right text-t2">{c.hit_rate === null ? "—" : plainPct(c.hit_rate)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          <p className="mt-sp2 text-[11px] text-t2">
            Edge vs the average day: BUY {pct(sig.edge_vs_average.BUY["5"], 2)} · SELL {pct(sig.edge_vs_average.SELL["5"], 2)}
          </p>
        </Card>

        <Card title="Risk signal" badge={<Tag>out-of-sample</Tag>}>
          <div className="mb-sp2 flex gap-sp2 text-[10px]">
            {(["5", "20"] as const).map((k) => (
              <button key={k} type="button" onClick={() => setH(k)} className={`rounded-r1 px-2 py-[2px] font-bold ${h === k ? "bg-teal-dim text-teal" : "bg-bg3 text-t3"}`}>
                next {k} days
              </button>
            ))}
          </div>
          <table className="w-full text-[12px]">
            <thead>
              <tr className="text-left text-[10px] font-bold uppercase tracking-wide text-t3">
                <th className="py-1">Rated</th>
                <th className="py-1 text-right">n</th>
                <th className="py-1 text-right">Volatility</th>
                <th className="py-1 text-right">Worst dip</th>
                <th className="py-1 text-right">P(drop &gt; {plainPct(risk.drop_threshold[h])})</th>
              </tr>
            </thead>
            <tbody>
              {(["LOW", "MEDIUM", "HIGH"] as const).map((lv) => {
                const c = risk.levels[lv][h];
                return (
                  <tr key={lv} className="border-t border-border">
                    <td className="py-1"><RiskChip level={lv} /></td>
                    <td className="mono py-1 text-right text-t3">{c.n.toLocaleString()}</td>
                    <td className="mono py-1 text-right text-t2">{c.fwd_vol === null ? "—" : plainPct(c.fwd_vol)}</td>
                    <td className="mono py-1 text-right text-red">{pct(c.fwd_worst_dip)}</td>
                    <td className="mono py-1 text-right text-t2">{c.p_drop === null ? "—" : plainPct(c.p_drop)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          <p className="mt-sp2 text-[11px] text-t2">
            HIGH vs LOW: volatility {x(risk.lift[h].vol)} · worst dip {x(risk.lift[h].dip)} · drop odds {x(risk.lift[h].p_drop)}
          </p>
          <p className="mt-1 text-[10px] text-t3">{risk.note}</p>
        </Card>

        <Card title="Investment Committee" badge={<Tag warn>{com.reliable_runs} decisions</Tag>}>
          {com.reliable_runs === 0 ? (
            <p className="text-[12px] text-t3">No reliable decisions on record yet.</p>
          ) : (
            <>
              <p className="text-[12px] text-t2">
                Agrees with the plain engine <b className="text-t1">{plainPct(com.agrees_with_engine)}</b> of the time
                {com.agrees_with_engine !== null && com.agrees_with_engine > 0.9 ? " — so far it mostly echoes the engine." : "."}
              </p>
              <table className="mt-sp2 w-full text-[12px]">
                <tbody>
                  {(["BUY", "SELL", "HOLD"] as const).map((d) => {
                    const c = com.by_decision[d]["5"];
                    return (
                      <tr key={d} className="border-t border-border">
                        <td className="py-1"><LeanChip lean={d} small /></td>
                        <td className="mono py-1 text-right text-t3">n={c.n}</td>
                        <td className={`mono py-1 text-right ${tone(c.mean_return)}`}>{c.n ? pct(c.mean_return, 2) : "—"}</td>
                        <td className="mono py-1 text-right text-t2">{c.hit_rate === null ? "—" : `${plainPct(c.hit_rate)} right`}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
              <p className="mt-sp2 text-[10px] text-t3">5-day forward return from the next close. Tiny samples: read n, not the percentages.</p>
            </>
          )}
        </Card>
      </div>

      <Section title="Who is worth trusting? — agent leaderboard" note="Per committee member: how often it sided with the final call and the engine, and — once enough decisions have aged — whether its BUY/SELL calls made money.">
        <Leaderboard lb={a.leaderboard} />
      </Section>
    </div>
  );
}
