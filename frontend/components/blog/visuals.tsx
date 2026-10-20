"use client";

import { useState } from "react";
import type { CasebookCase, CasebookLean, CasebookPost } from "@/lib/types";
import { dayHeadline } from "@/lib/casebookText";

// BUY/SELL/HOLD always travel with a glyph and a word, never colour alone (teal/red is a CVD-marginal pair).
export const LEAN_FILL: Record<CasebookLean, string> = { BUY: "var(--c-teal)", SELL: "var(--c-red)", HOLD: "var(--c-t3)" };
export const LEAN_GLYPH: Record<CasebookLean, string> = { BUY: "▲", SELL: "▼", HOLD: "•" };
const LEANS: CasebookLean[] = ["BUY", "SELL", "HOLD"];

const pct = (x: number, dp = 1) => `${x >= 0 ? "+" : ""}${(x * 100).toFixed(dp)}%`;

/** Facts computed from the post itself: nothing here is claimed that the data on the page doesn't show. */
export function postFacts(p: CasebookPost) {
  const cases = p.cases;
  const count = (d: CasebookLean) => cases.filter((c) => c.decision === d).length;
  const analysts = new Set(cases.flatMap((c) => c.analysts.filter((a) => a.type === "llm").map((a) => a.agent))).size;
  const quant = new Set(cases.flatMap((c) => c.analysts.filter((a) => a.type !== "llm").map((a) => a.agent))).size;
  const agreement = cases
    .map((c) => {
      const voiced = c.analysts.filter((a) => a.lean);
      return voiced.length && c.decision ? voiced.filter((a) => a.lean === c.decision).length / voiced.length : null;
    })
    .filter((x): x is number => x !== null);
  const split = cases
    .map((c) => ({ c, n: Object.fromEntries(LEANS.map((l) => [l, c.analysts.filter((a) => a.lean === l).length])) as Record<CasebookLean, number> }))
    .map((x) => ({ ...x, spread: LEANS.filter((l) => x.n[l] > 0).length * 100 - Math.max(...LEANS.map((l) => x.n[l])) }))
    .sort((a, b) => b.spread - a.spread)[0];
  const outcomes = cases.filter((c) => c.outcome);
  const scored = outcomes.filter((c) => c.outcome!.right !== null);
  const excess = outcomes.map((c) => c.outcome!.excess).filter((x): x is number => x !== null);
  return {
    n: cases.length,
    buy: count("BUY"),
    sell: count("SELL"),
    hold: count("HOLD"),
    analysts,
    quant,
    agreement: agreement.length ? agreement.reduce((a, b) => a + b, 0) / agreement.length : null,
    overruled: cases.filter((c) => c.agrees_with_engine === false).map((c) => c.symbol),
    mostSplit: split && LEANS.filter((l) => split.n[l] > 0).length > 1 ? split : null,
    hasOutcomes: outcomes.length > 0,
    scored: scored.length,
    right: scored.filter((c) => c.outcome!.right).length,
    avgExcess: excess.length ? excess.reduce((a, b) => a + b, 0) / excess.length : null,
    beatSpy: excess.filter((x) => x > 0).length,
    withExcess: excess.length,
  };
}

type Facts = ReturnType<typeof postFacts>;


function Stat({ value, label, sub }: { value: string; label: string; sub?: string }) {
  return (
    <div className="glass-panel p-sp4">
      <div className="text-[28px] font-extrabold leading-none tracking-tight text-t1">{value}</div>
      <div className="mt-sp2 text-[12px] font-semibold text-t2">{label}</div>
      {sub && <div className="mt-1 text-[11px] text-t3">{sub}</div>}
    </div>
  );
}

/** The top of a post: a plain-English headline and the handful of numbers that matter. */
export function PostHero({ f, date, cases }: { f: Facts; date: string; cases: CasebookCase[] }) {
  return (
    <div className="mb-sp8">
      <div className="mono mb-sp2 text-[11px] text-t3">{date} · GlassBox Research · Committee Casebook</div>
      <h1 className="mb-sp3 text-[30px] font-extrabold leading-tight tracking-tight text-t1 md:text-[34px]">{dayHeadline(cases)}</h1>
      <p className="mb-sp6 max-w-[640px] text-[14.5px] leading-relaxed text-t2">
        Here&apos;s who argued what, where they disagreed, and{f.hasOutcomes ? " how it actually turned out five trading days later" : " (in five trading days) how it turns out"}. Wrong calls stay up too.
      </p>
      <div className="grid grid-cols-2 gap-sp3 md:grid-cols-4">
        <Stat value={String(f.n)} label="stocks reviewed" sub={`${f.buy} BUY · ${f.sell} SELL · ${f.hold} HOLD`} />
        <Stat value={f.agreement === null ? "—" : `${Math.round(f.agreement * 100)}%`} label="of analysts agreed with the final call" sub="averaged over every stock" />
        <Stat value={String(f.overruled.length)} label="times the committee overruled the plain signal engine" sub={f.overruled.join(", ") || "it went along every time"} />
        {f.hasOutcomes ? (
          f.scored ? (
            <Stat value={`${f.right}/${f.scored}`} label="BUY/SELL calls were right" sub="judged against SPY over 5 trading days" />
          ) : (
            <Stat value={`${f.beatSpy}/${f.withExcess}`} label="stocks beat SPY over the next 5 days" sub="all calls were HOLD, so none scored right or wrong" />
          )
        ) : (
          <Stat value="5 days" label="until this post is scored" sub="the outcome is added automatically" />
        )}
      </div>
    </div>
  );
}

/** Tip-style callouts: what the numbers above mean for an ordinary investor's week. Educational, never advice. */
export function Takeaways({ f }: { f: Facts }) {
  const tips: { title: string; body: string }[] = [];
  if (f.hold >= Math.ceil(f.n * 0.7))
    tips.push({
      title: "Doing nothing is a decision too",
      body: `The analysts said wait on ${f.hold} of ${f.n} stocks. When they see no clear edge, they hold. Every trade has a cost (the spread, and tax in a taxable account), so a quiet day isn't a wasted day.`,
    });
  if (f.mostSplit) {
    const n = f.mostSplit.n;
    tips.push({
      title: `The room split on ${f.mostSplit.c.symbol}`,
      body: `${n.BUY} said BUY, ${n.SELL} said SELL, ${n.HOLD} said HOLD. When informed opinions split this evenly, uncertainty is high. That's a reason for caution, not conviction.`,
    });
  }
  if (f.overruled.length)
    tips.push({
      title: "The AI overruled the simple rule",
      body: `On ${f.overruled.join(", ")} the committee went against the plain moving-average engine. Whether that's skill or noise only shows up over many days, which is why every call here gets scored.`,
    });
  if (f.hasOutcomes && f.avgExcess !== null)
    tips.push({
      title: "How it played out",
      body: f.scored
        ? `${f.right} of ${f.scored} BUY/SELL calls were right against SPY. Across all ${f.withExcess} stocks, the average move versus SPY was ${pct(f.avgExcess)}.`
        : `Across all ${f.withExcess} stocks, the average move versus SPY was ${pct(f.avgExcess)}, and ${f.beatSpy} of them beat it. HOLD calls aren't scored right or wrong.`,
    });
  return (
    <div className="mb-sp8 grid grid-cols-1 gap-sp3 md:grid-cols-2">
      {tips.map((t) => (
        <div key={t.title} className="rounded-r2 border border-border border-l-[3px] border-l-teal bg-bg2 p-sp4">
          <div className="mb-1 text-[11px] font-bold uppercase tracking-wide text-teal">💡 What this means</div>
          <div className="mb-1 text-[14px] font-bold text-t1">{t.title}</div>
          <p className="text-[13px] leading-relaxed text-t2">{t.body}</p>
        </div>
      ))}
      <div className="rounded-r2 border border-border bg-bg2 p-sp4 md:col-span-2">
        <div className="mb-1 text-[14px] font-bold text-t1">What this doesn&apos;t tell you</div>
        <p className="text-[12.5px] leading-relaxed text-t2">
          This is simulated research on a fixed list of 15 stocks and funds, not advice about what you should buy or sell. One day of calls says
          almost nothing on its own; the committee only counts as adding value once it beats simple rules and SPY over at least 60 live trading days.
        </p>
      </div>
    </div>
  );
}

function Legend({ items }: { items: { label: string; fill: string; glyph?: string; dashed?: boolean }[] }) {
  return (
    <div className="flex flex-wrap gap-x-sp4 gap-y-1 text-[11.5px] text-t2">
      {items.map((i) => (
        <span key={i.label} className="flex items-center gap-1.5">
          <span className={`grid h-[14px] w-[14px] place-items-center rounded-[3px] text-[9px] text-bg ${i.dashed ? "border border-dashed border-t3" : ""}`} style={{ background: i.fill }}>
            {i.glyph}
          </span>
          {i.label}
        </span>
      ))}
    </div>
  );
}

const LEAN_LEGEND = [
  ...LEANS.map((l) => ({ label: l, fill: LEAN_FILL[l], glyph: LEAN_GLYPH[l] })),
  { label: "didn't answer", fill: "transparent", dashed: true },
];

/** The room at a glance: every analyst (row) x every stock (column). Tap or hover a cell to read that analyst's reasoning. */
export function LeanHeatmap({ cases }: { cases: CasebookCase[] }) {
  const agents = Array.from(new Set(cases.flatMap((c) => c.analysts.map((a) => a.agent))));
  const [sel, setSel] = useState<{ agent: string; symbol: string } | null>(null);
  const cell = (agent: string, c: CasebookCase) => c.analysts.find((a) => a.agent === agent);
  const picked = sel && cases.find((c) => c.symbol === sel.symbol);
  const pickedA = picked && cell(sel!.agent, picked);

  return (
    <figure className="glass-panel mb-sp8 p-sp5">
      <figcaption className="mb-sp1 text-[16px] font-bold text-t1">The room at a glance</figcaption>
      <p className="mb-sp4 text-[12.5px] text-t3">Each square is one analyst&apos;s call on one stock. Tap any square to read their reasoning.</p>
      <div className="overflow-x-auto pb-sp2">
        <table className="border-separate" style={{ borderSpacing: 2 }}>
          <thead>
            <tr>
              <th className="sticky left-0 z-10 bg-bg1" />
              {cases.map((c) => (
                <th key={c.symbol} className="mono px-0.5 pb-1 text-center text-[10px] font-semibold text-t2">
                  {c.symbol}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {agents.map((agent) => (
              <tr key={agent}>
                <th className="sticky left-0 z-10 whitespace-nowrap bg-bg1 pr-sp3 text-right text-[11.5px] font-medium text-t2">{agent}</th>
                {cases.map((c) => {
                  const a = cell(agent, c);
                  const on = sel?.agent === agent && sel?.symbol === c.symbol;
                  return (
                    <td key={c.symbol} className="p-0">
                      <button
                        type="button"
                        aria-label={`${agent} on ${c.symbol}: ${a?.lean ?? "did not answer"}`}
                        onClick={() => setSel(on ? null : { agent, symbol: c.symbol })}
                        onMouseEnter={() => setSel({ agent, symbol: c.symbol })}
                        className={`grid h-[28px] w-[34px] place-items-center rounded-[4px] text-[11px] text-bg transition-transform duration-150 hover:scale-110 ${on ? "ring-2 ring-t1" : ""} ${a?.lean ? "" : "border border-dashed border-t3"}`}
                        style={{ background: a?.lean ? LEAN_FILL[a.lean] : "transparent" }}
                      >
                        {a?.lean ? LEAN_GLYPH[a.lean] : ""}
                      </button>
                    </td>
                  );
                })}
              </tr>
            ))}
            <tr>
              <th className="sticky left-0 z-10 whitespace-nowrap bg-bg1 pr-sp3 pt-sp2 text-right text-[11.5px] font-bold text-t1">Final call</th>
              {cases.map((c) => (
                <td key={c.symbol} className="pt-sp2 text-center">
                  <span className="mono text-[10px] font-bold" style={{ color: c.decision ? LEAN_FILL[c.decision] : undefined }}>
                    {c.decision ? `${LEAN_GLYPH[c.decision]}${c.decision}` : "—"}
                  </span>
                </td>
              ))}
            </tr>
          </tbody>
        </table>
      </div>
      <div className="mt-sp3">
        <Legend items={LEAN_LEGEND} />
      </div>
      <div className="mt-sp4 min-h-[64px] rounded-r2 bg-bg2 p-sp3 text-[12.5px] leading-relaxed">
        {picked ? (
          <>
            <b className="text-t1">{sel!.agent}</b> on <b className="text-t1">{picked.symbol}</b>:{" "}
            {pickedA?.lean ? (
              <>
                <b style={{ color: LEAN_FILL[pickedA.lean] }}>
                  {LEAN_GLYPH[pickedA.lean]} {pickedA.lean}
                </b>{" "}
                <span className="text-t2">{pickedA.summary ?? "(reasoning withheld by our compliance filter)"}</span>
              </>
            ) : (
              <span className="text-t3">did not answer this time.</span>
            )}
          </>
        ) : (
          <span className="text-t3">Tap a square to see what that analyst said.</span>
        )}
      </div>
    </figure>
  );
}

/** Five trading days later: how each stock did against SPY, coloured by whether the call was right. */
export function OutcomeBars({ cases, horizon }: { cases: CasebookCase[]; horizon: number }) {
  const rows = cases.filter((c) => c.outcome && c.outcome.excess !== null).sort((a, b) => b.outcome!.excess! - a.outcome!.excess!);
  const [hover, setHover] = useState<string | null>(null);
  if (!rows.length) return null;
  const max = Math.max(...rows.map((c) => Math.abs(c.outcome!.excess!)), 0.005);
  const fill = (c: CasebookCase) => (c.outcome!.right === true ? "var(--c-teal)" : c.outcome!.right === false ? "var(--c-red)" : "var(--c-t3)");
  const scored = rows.some((c) => c.outcome!.right !== null);

  return (
    <figure className="glass-panel mb-sp8 p-sp5">
      <figcaption className="mb-sp1 text-[16px] font-bold text-t1">{horizon} trading days later: each stock versus SPY</figcaption>
      <p className="mb-sp4 text-[12.5px] text-t3">Bars to the right beat the market; bars to the left lagged it.</p>
      <div className="flex flex-col gap-[3px]">
        {rows.map((c) => {
          const x = c.outcome!.excess!;
          const w = (Math.abs(x) / max) * 50;
          return (
            <div
              key={c.symbol}
              className="grid grid-cols-[52px_1fr_64px] items-center gap-sp2 rounded-[4px] py-0.5 hover:bg-bg2"
              onMouseEnter={() => setHover(c.symbol)}
              onMouseLeave={() => setHover(null)}
            >
              <span className="mono text-[11px] font-semibold text-t2">{c.symbol}</span>
              <div className="relative h-[16px]">
                <div className="absolute inset-y-[-2px] left-1/2 w-px bg-border2" />
                <div
                  className="absolute top-0 h-full"
                  style={{
                    background: fill(c),
                    width: `${w}%`,
                    left: x >= 0 ? "50%" : `${50 - w}%`,
                    borderRadius: x >= 0 ? "0 4px 4px 0" : "4px 0 0 4px",
                  }}
                />
              </div>
              <span className="mono text-right text-[11px] text-t2">{pct(x)}</span>
              {hover === c.symbol && (
                <div className="col-span-3 px-[60px] pb-1 text-[11px] text-t3">
                  {c.symbol} {pct(c.outcome!.return)} vs SPY {c.outcome!.spy_return === null ? "—" : pct(c.outcome!.spy_return)} · call: {c.decision}
                  {c.outcome!.right === true ? " · right" : c.outcome!.right === false ? " · wrong" : " · not scored"}
                </div>
              )}
            </div>
          );
        })}
      </div>
      <div className="mt-sp4">
        <Legend
          items={[
            ...(scored ? [{ label: "call was right", fill: "var(--c-teal)", glyph: "✓" }, { label: "call was wrong", fill: "var(--c-red)", glyph: "✗" }] : []),
            { label: "HOLD (not scored)", fill: "var(--c-t3)" },
          ]}
        />
      </div>
    </figure>
  );
}

/** The confidence-weighted vote as one stacked bar, each segment labelled. */
export function VoteBar({ votes }: { votes: Record<CasebookLean, number> }) {
  const total = LEANS.reduce((a, l) => a + (votes[l] || 0), 0) || 1;
  return (
    <div>
      <div className="flex h-[10px] gap-[2px] overflow-hidden rounded-[4px]">
        {LEANS.filter((l) => votes[l] > 0).map((l) => (
          <div key={l} style={{ width: `${(votes[l] / total) * 100}%`, background: LEAN_FILL[l] }} title={`${l} ${votes[l].toFixed(1)}`} />
        ))}
      </div>
      <div className="mono mt-1 flex gap-sp3 text-[10.5px] text-t2">
        {LEANS.map((l) => (
          <span key={l}>
            {LEAN_GLYPH[l]} {l} {Math.round(((votes[l] || 0) / total) * 100)}%
          </span>
        ))}
      </div>
    </div>
  );
}
