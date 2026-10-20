"use client";

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { apiFetch } from "@/lib/api";
import type { CasebookFeed, CasebookLean, FeedCard, FeedQuote } from "@/lib/types";
import { LEAN_FILL, LEAN_GLYPH } from "@/components/blog/visuals";

const pct = (x: number) => `${x >= 0 ? "+" : ""}${(x * 100).toFixed(1)}%`;

function Lean({ d, big = false }: { d: CasebookLean | null | undefined; big?: boolean }) {
  if (!d) return <span className="text-t3">no call</span>;
  return (
    <b className={`whitespace-nowrap ${big ? "text-[15px]" : ""}`} style={{ color: LEAN_FILL[d] }}>
      {LEAN_GLYPH[d]} {d}
    </b>
  );
}

function Kicker({ children, date }: { children: React.ReactNode; date?: string }) {
  return (
    <div className="mb-sp2 flex items-center justify-between gap-sp2 text-[10.5px] font-bold uppercase tracking-wide text-t3">
      <span>{children}</span>
      {date && <span className="mono font-normal normal-case">{date}</span>}
    </div>
  );
}

function Quote({ q }: { q: FeedQuote }) {
  return (
    <blockquote className="text-[13px] leading-relaxed text-t2">
      <span className="text-[18px] leading-none text-t3">&ldquo;</span>
      {q.text}
      <footer className="mt-1 text-[11.5px] text-t3">
        {q.agent} · <Lean d={q.lean} />
      </footer>
    </blockquote>
  );
}

/** Every AI analyst opinion of the day in one bar: the texture behind a page of HOLDs. */
function Opinions({ o, allHold }: { o: Record<CasebookLean, number>; allHold: boolean }) {
  const leans: CasebookLean[] = ["BUY", "SELL", "HOLD"];
  const total = leans.reduce((a, l) => a + o[l], 0);
  if (!total) return null;
  return (
    <div className="mb-sp4 rounded-r2 bg-bg2 p-sp4">
      <div className="mb-sp2 text-[12px] font-semibold text-t2">All {total} AI analyst opinions today</div>
      <div className="mb-sp2 flex h-[12px] gap-[2px] overflow-hidden rounded-[4px]">
        {leans.filter((l) => o[l]).map((l) => (
          <div key={l} style={{ width: `${(o[l] / total) * 100}%`, background: LEAN_FILL[l], minWidth: 4 }} />
        ))}
      </div>
      <div className="mono flex flex-wrap gap-x-sp4 text-[11px] text-t2">
        {leans.map((l) => (
          <span key={l}>
            {LEAN_GLYPH[l]} {o[l]} {l}
          </span>
        ))}
      </div>
      {allHold && o.BUY + o.SELL > 0 && (
        <p className="mt-sp2 text-[11.5px] text-t3">
          {o.BUY + o.SELL} individual analysts wanted to act; the weighted vote still said wait.
        </p>
      )}
    </div>
  );
}

const SPAN: Record<FeedCard["type"], string> = {
  day: "md:col-span-2 md:row-span-2",
  debate: "md:col-span-2",
  overrule: "md:col-span-1",
  verdict: "md:col-span-1",
  quote: "md:col-span-2",
  scoreboard: "md:col-span-2",
};

function Body({ c }: { c: FeedCard }) {
  switch (c.type) {
    case "day":
      return (
        <div className="flex h-full flex-col">
          <Kicker date={c.date}>Today&apos;s casebook</Kicker>
          <h3 className="mb-sp4 text-[24px] font-extrabold leading-tight tracking-tight text-t1 md:text-[28px]">{c.headline}</h3>
          <div className="mb-sp4 flex flex-wrap gap-1">
            {Object.entries(c.decisions).map(([s, d]) => (
              <span key={s} className="mono flex items-center gap-1 rounded-[4px] border border-border bg-bg2 px-1.5 py-0.5 text-[10.5px] text-t2">
                <span style={{ color: d ? LEAN_FILL[d] : undefined }}>{d ? LEAN_GLYPH[d] : "–"}</span>
                {s}
              </span>
            ))}
          </div>
          {c.overruled.length > 0 && <p className="mb-sp3 text-[12.5px] text-gold">The AI overruled the plain engine on {c.overruled.join(", ")}.</p>}
          <Opinions o={c.opinions} allHold={Object.values(c.decisions).every((d) => d === "HOLD")} />
          <span className="mt-auto text-[13px] font-semibold text-teal">Read every analyst&apos;s case →</span>
        </div>
      );
    case "debate": {
      const total = c.split.BUY + c.split.SELL + c.split.HOLD || 1;
      return (
        <>
          <Kicker date={c.date}>Most debated</Kicker>
          <h3 className="mb-sp3 text-[17px] font-bold text-t1">
            The room split on {c.symbol}: {c.split.BUY} BUY, {c.split.SELL} SELL, {c.split.HOLD} HOLD
          </h3>
          <div className="mb-sp3 flex h-[8px] gap-[2px] overflow-hidden rounded-[4px]">
            {(["BUY", "SELL", "HOLD"] as CasebookLean[]).filter((l) => c.split[l]).map((l) => (
              <div key={l} style={{ width: `${(c.split[l] / total) * 100}%`, background: LEAN_FILL[l] }} />
            ))}
          </div>
          <div className="grid grid-cols-1 gap-sp3 sm:grid-cols-2">
            {c.quotes.map((q) => (
              <Quote key={q.agent} q={q} />
            ))}
          </div>
          <p className="mt-sp3 text-[12px] text-t3">
            Final call: <Lean d={c.decision} />
          </p>
        </>
      );
    }
    case "overrule":
      return (
        <>
          <Kicker date={c.date}>AI vs the simple rule</Kicker>
          <h3 className="mb-sp3 text-[16px] font-bold text-t1">{c.symbol}: the committee overruled the engine</h3>
          <div className="mb-sp3 flex flex-wrap items-center gap-x-sp2 gap-y-1 text-[13px]">
            <span className="text-t3">engine</span> <Lean d={c.engine} big /> <span className="text-t3">→ committee</span> <Lean d={c.decision} big />
          </div>
          <p className="text-[12.5px] text-t2">
            {c.outcome
              ? `${c.outcome.horizon} days later: ${c.symbol} ${pct(c.outcome.return)}${c.outcome.spy_return !== null ? ` vs SPY ${pct(c.outcome.spy_return)}` : ""}.${
                  c.outcome.right === true ? " The AI was right." : c.outcome.right === false ? " The AI was wrong." : ""
                }`
              : "Outcome pending: scored 5 trading days later."}
          </p>
        </>
      );
    case "verdict":
      return (
        <>
          <Kicker date={c.date}>{c.horizon} days later</Kicker>
          <h3 className="mb-sp3 text-[16px] font-bold text-t1">How that day&apos;s calls played out</h3>
          <div className="flex flex-col gap-sp2 text-[12.5px] text-t2">
            <span>
              Best: <b className="text-t1">{c.best.symbol}</b> {pct(c.best.excess)} vs SPY (<Lean d={c.best.decision} />)
            </span>
            <span>
              Worst: <b className="text-t1">{c.worst.symbol}</b> {pct(c.worst.excess)} vs SPY (<Lean d={c.worst.decision} />)
            </span>
            <span className="text-t3">{c.judged ? `${c.right} of ${c.judged} BUY/SELL calls were right.` : "All HOLD, so none scored right or wrong."}</span>
          </div>
        </>
      );
    case "quote":
      return (
        <>
          <Kicker date={c.date}>Analyst spotlight · {c.symbol}</Kicker>
          <div className="text-[15px]">
            <Quote q={c.quote} />
          </div>
        </>
      );
    case "scoreboard":
      return (
        <>
          <Kicker>The running scoreboard</Kicker>
          <div className="grid grid-cols-2 gap-sp3 sm:grid-cols-4">
            {[
              { v: String(c.days), l: "trading days" },
              { v: String(c.calls), l: "public calls" },
              { v: c.hold_share === null ? "—" : `${Math.round(c.hold_share * 100)}%`, l: "were HOLD" },
              { v: c.scored ? `${c.right}/${c.scored}` : "—", l: "BUY/SELL calls right" },
            ].map((t) => (
              <div key={t.l}>
                <div className="text-[22px] font-extrabold leading-none text-t1">{t.v}</div>
                <div className="mt-1 text-[11.5px] text-t3">{t.l}</div>
              </div>
            ))}
          </div>
          <p className="mt-sp3 text-[11.5px] text-t3">Too early to call it skill: the bar is 60 live trading days against simple rules and SPY.</p>
        </>
      );
  }
}

/** The daily research feed: a bento grid of cards rebuilt from each trading day's committee runs. */
export function Feed({ limit }: { limit?: number }) {
  const q = useQuery({
    queryKey: ["casebook-feed"],
    queryFn: () => apiFetch<CasebookFeed>("/api/public/casebook/feed"),
    staleTime: 5 * 60 * 1000,
    retry: false,
  });
  const cards = (q.data?.cards ?? []).slice(0, limit);
  if (!cards.length) return null;
  return (
    <div>
      {q.data?.latest && (
        <div className="mb-sp3 flex items-center gap-sp2 text-[11.5px] text-t3">
          <span className="live-dot" /> Updated every trading day after the US market closes · latest {q.data.latest}
        </div>
      )}
      <div className="grid grid-flow-dense grid-cols-1 gap-sp4 md:grid-cols-4">
        {cards.map((c, i) => (
          <Link
            key={`${c.type}-${i}`}
            href={c.href}
            className={`glass-panel block p-sp5 transition-all duration-200 ease-glass hover:-translate-y-0.5 hover:border-border2 ${SPAN[c.type]}`}
          >
            <Body c={c} />
          </Link>
        ))}
      </div>
    </div>
  );
}
