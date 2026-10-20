import type { CasebookCase } from "@/lib/types";
import { VoteBar } from "@/components/blog/visuals";

export const leanTone = (d: string | null | undefined) =>
  d === "BUY" ? "text-teal" : d === "SELL" ? "text-red" : d === "HOLD" ? "text-t2" : "text-t3";

const pct = (x: number) => `${x >= 0 ? "+" : ""}${(x * 100).toFixed(1)}%`;

/** One reviewed stock: why it came up, every analyst's labelled lean and reasoning, the vote and the outcome. */
export function CaseCard({ c }: { c: CasebookCase }) {
  const v = c.votes;
  return (
    <article id={c.symbol} className="glass-panel scroll-mt-24 p-sp5">
      <header className="mb-sp3 flex flex-wrap items-baseline justify-between gap-sp2">
        <h2 className="text-[20px] font-extrabold text-t1">
          {c.symbol} <span className={leanTone(c.decision)}>{c.decision ?? "no call"}</span>
        </h2>
        <span className="text-[12px] text-t3">
          engine said <b className={leanTone(c.engine_signal)}>{c.engine_signal}</b>
          {c.agrees_with_engine === false && <span className="ml-1 font-semibold text-gold">· committee overruled it</span>}
        </span>
      </header>

      <p className="mb-sp3 text-[12.5px] text-t2">
        <b className="text-t1">Why it came up:</b> {c.why}
      </p>
      {v && (
        <div className="mb-sp3 max-w-[420px]">
          <div className="mb-1 text-[11px] font-semibold text-t3">Confidence-weighted vote</div>
          <VoteBar votes={v} />
        </div>
      )}
      {!c.quorum_ok && (
        <p className="mb-sp3 text-[12px] text-red">
          Only {c.answered} of {c.total} agents answered, below quorum, so this call was not acted on. Treat it as unreliable.
        </p>
      )}

      <ul className="mb-sp4 flex flex-col divide-y divide-border">
        {c.analysts.map((a) => (
          <li key={a.agent} className="flex flex-col gap-1 py-sp2 sm:flex-row sm:gap-sp3">
            <span className="w-48 shrink-0 text-[12.5px] font-semibold text-t1">
              {a.agent}
              {a.type === "deterministic" && <span className="ml-1 text-[10px] font-normal text-t3">quant</span>}
            </span>
            <span className={`w-12 shrink-0 text-[12.5px] font-bold ${leanTone(a.lean)}`}>{a.lean ?? "—"}</span>
            <span className="text-[12.5px] leading-relaxed text-t2">
              {a.summary ?? <i className="text-t3">Reasoning withheld by our compliance filter.</i>}
            </span>
          </li>
        ))}
      </ul>
      {c.did_not_answer > 0 && <p className="mb-sp3 text-[11px] text-t3">{c.did_not_answer} agent(s) did not answer this time.</p>}

      <footer className="rounded-r2 bg-bg2 px-sp4 py-sp3 text-[12.5px]">
        {c.outcome ? (
          <span className="text-t2">
            <b className="text-t1">{c.outcome.horizon} trading days later:</b> {c.symbol} {pct(c.outcome.return)}
            {c.outcome.spy_return !== null && <> vs SPY {pct(c.outcome.spy_return)}</>}
            {c.outcome.right === true && <b className="ml-2 text-teal">The call was right.</b>}
            {c.outcome.right === false && <b className="ml-2 text-red">The call was wrong.</b>}
            {c.outcome.right === null && <span className="ml-2 text-t3">(HOLD calls aren&apos;t scored right or wrong.)</span>}
          </span>
        ) : (
          <span className="text-t3">Outcome pending: scored 5 trading days after the review.</span>
        )}
      </footer>
    </article>
  );
}
