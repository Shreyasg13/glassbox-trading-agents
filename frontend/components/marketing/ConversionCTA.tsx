import { Reveal } from "@/components/Reveal";

/**
 * The single dedicated conversion moment between the Lens carousel and
 * the rest of the marketing page -- per the Plan-correction.MD addendum
 * ("final conversion section"). Deliberately does NOT claim a specific
 * free-signal quota (e.g. "5 free verified signals"): the addendum's own
 * rule is that a quantified free-tier claim must be backed by real,
 * backend-enforced entitlements before it's shown anywhere, and that
 * enforcement doesn't exist yet. This uses one of the addendum's own
 * listed safe alternative headlines instead, which makes the same
 * "verify, don't trust" pitch without inventing a number.
 */
export function ConversionCTA() {
  return (
    <section id="glassbox-verify" className="scroll-mt-20 border-y border-border bg-panel px-sp6 py-sp10 md:px-sp10">
      <Reveal>
        <div className="mx-auto flex max-w-[720px] flex-col items-center gap-sp5 text-center">
          <div className="inline-flex items-center gap-sp2 rounded-r4 border border-teal/20 bg-teal-dim px-sp3 py-1 text-[11px] font-semibold text-teal">
            <span className="live-dot" />
            Verification, not advice
          </div>
          <h2 className="text-[30px] font-extrabold leading-tight tracking-tight text-t1 md:text-[36px]">
            Stop guessing.
            <br />
            <span className="text-teal">Start verifying with GlassBox.</span>
          </h2>
          <p className="max-w-[520px] text-[15px] leading-relaxed text-t2">
            Every lens gave you an opinion. GlassBox gives you the raw data, the formula, and an
            independent audit behind whatever score you&apos;re looking at, on your own holdings.
          </p>
          <div className="mt-sp2 flex flex-wrap justify-center gap-sp3">
            <a href="/signup" className="btn btn-primary px-sp6 py-sp3 text-[14px]">
              Try GlassBox Free <span className="opacity-70">→</span>
            </a>
            <a href="/login" className="btn btn-ghost px-sp6 py-sp3 text-[14px]">
              Already verifying? Sign in
            </a>
          </div>
          <p className="mono text-[11px] text-t4">Free, non-commercial research preview · not investment advice</p>
        </div>
      </Reveal>
    </section>
  );
}
