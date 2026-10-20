import type { CasebookCase } from "@/lib/types";

// Rendered by next/og (Satori) into the 1200x630 share image: inline styles and flexbox only.
const FILL: Record<string, string> = { BUY: "#0dccaa", SELL: "#e8445a", HOLD: "#5c7090" };

export const SHARE_SIZE = { width: 1200, height: 630 };

/** A day's casebook as one picture: the headline, and the whole room (every analyst x every stock) as coloured squares. */
export function ShareCard({ kicker, headline, cases }: { kicker: string; headline: string; cases: CasebookCase[] }) {
  const agents = Array.from(new Set(cases.flatMap((c) => c.analysts.filter((a) => a.type === "llm").map((a) => a.agent))));
  const cell = Math.max(14, Math.min(30, Math.floor(440 / Math.max(cases.length, 1)) - 4));
  return (
    <div style={{ width: "100%", height: "100%", display: "flex", flexDirection: "column", background: "#06090f", color: "#edf2ff", padding: "56px 64px", fontFamily: "sans-serif" }}>
      <div style={{ display: "flex", justifyContent: "space-between", fontSize: 22, color: "#9aaccc" }}>
        <span style={{ color: "#0dccaa", fontWeight: 700 }}>GlassBox Research</span>
        <span>{kicker}</span>
      </div>
      <div style={{ display: "flex", flex: 1, marginTop: 36, gap: 48 }}>
        <div style={{ display: "flex", flexDirection: "column", flex: 1 }}>
          <div style={{ fontSize: headline.length > 90 ? 46 : 54, fontWeight: 800, lineHeight: 1.12, letterSpacing: -1 }}>{headline}</div>
          <div style={{ marginTop: "auto", display: "flex", gap: 22, fontSize: 22, color: "#9aaccc" }}>
            {(["BUY", "SELL", "HOLD"] as const).map((l) => (
              <span key={l} style={{ display: "flex", alignItems: "center", gap: 8 }}>
                <span style={{ width: 18, height: 18, borderRadius: 4, background: FILL[l] }} /> {l}
              </span>
            ))}
          </div>
        </div>
        {cases.length > 0 && agents.length > 0 && (
          <div style={{ display: "flex", flexDirection: "column", gap: 4, justifyContent: "center" }}>
            {agents.map((agent) => (
              <div key={agent} style={{ display: "flex", gap: 4 }}>
                {cases.map((c) => {
                  const a = c.analysts.find((x) => x.agent === agent);
                  return (
                    <div
                      key={c.symbol}
                      style={{ width: cell, height: cell, borderRadius: 4, background: a?.lean ? FILL[a.lean] : "transparent", border: a?.lean ? "none" : "1px dashed #5c7090" }}
                    />
                  );
                })}
              </div>
            ))}
            <div style={{ display: "flex", fontSize: 16, color: "#5c7090", marginTop: 8 }}>each square: one AI analyst&apos;s call on one stock</div>
          </div>
        )}
      </div>
      <div style={{ display: "flex", marginTop: 28, fontSize: 19, color: "#5c7090" }}>
        Every call public · scored against SPY after 5 trading days · simulated research, not investment advice
      </div>
    </div>
  );
}
