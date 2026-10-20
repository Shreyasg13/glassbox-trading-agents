import type { CasebookCase } from "@/lib/types";

/** The one-line story of a day, shared by the post page and its share image (and mirrored in casebook._day_headline). */
export function dayHeadline(cases: CasebookCase[]): string {
  const ai = new Set(cases.flatMap((c) => c.analysts.filter((a) => a.type === "llm").map((a) => a.agent))).size;
  const quant = new Set(cases.flatMap((c) => c.analysts.filter((a) => a.type !== "llm").map((a) => a.agent))).size;
  const who = `${ai} AI analysts${quant ? ` and ${quant} quant models` : ""}`;
  const count = (d: string) => cases.filter((c) => c.decision === d).length;
  const [buy, sell, hold] = [count("BUY"), count("SELL"), count("HOLD")];
  if (hold === cases.length) return `${who} looked at ${cases.length} stocks. On every single one, their verdict was: wait.`;
  const moves = [buy && `${buy} BUY`, sell && `${sell} SELL`].filter(Boolean).join(" and ");
  return `${who} looked at ${cases.length} stocks. They made ${moves}, and said wait on the other ${hold}.`;
}
