"use client";

import { Suspense, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { ApiError, apiFetch } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { FeedbackButtons } from "@/components/feedback/FeedbackButtons";

type Row = {
  symbol: string; price: number | null; engine_signal: string; engine_confidence: number; risk_level: string | null;
  committee_action: string | null; committee_headline: string | null; consensus: string | null; gated: boolean; disagrees: boolean;
  fwd_1d: number | null; fwd_5d: number | null;
};
type Track = { reviews: number; reliable_reviews: number; agrees_with_engine: number | null; by_decision_5d: Record<string, { n: number; mean_return: number | null; hit_rate: number | null }>; note: string };
type Signals = { as_of: string; first_date: string; latest_date: string; committee_started: string | null; note: string | null; rows: Row[]; committee_track: Track; watchlist: string[] | null };
type Msg = { role: "user" | "assistant"; content: string; id?: string; ai?: boolean };
type AskResult = { id: string; answer: string; as_of: string; note: string | null; used_ai: boolean; remaining_today: number; disclaimer: string };
type History = { messages: { id: string; question: string; answer: string; used_llm: boolean }[]; remaining_today: number; daily_limit: number };

const pct = (v: number | null | undefined, d = 1) => (v == null ? "–" : `${v >= 0 ? "+" : ""}${(v * 100).toFixed(d)}%`);
const tone = (v: number | null | undefined) => (v == null ? "text-t3" : v > 0 ? "text-teal" : v < 0 ? "text-red" : "text-t2");
const chip = (s: string | null) => (s === "BUY" ? "text-teal" : s === "SELL" ? "text-red" : "text-t2");

function AssistantInner() {
  const { token } = useAuth();
  const params = useSearchParams();
  const opts = { token: token ?? undefined };
  const [date, setDate] = useState<string>(params.get("date") ?? "");
  const focus = params.get("symbol");
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [remaining, setRemaining] = useState<number | null>(null);
  const endRef = useRef<HTMLDivElement>(null);

  const sig = useQuery({
    queryKey: ["my-signals", date],
    queryFn: () => apiFetch<Signals>(`/api/me/signals${date ? `?date=${date}` : ""}`, opts),
    enabled: !!token,
    retry: false,
  });
  const hist = useQuery({ queryKey: ["ask-history"], queryFn: () => apiFetch<History>("/api/me/ask/history", opts), enabled: !!token, retry: false });

  useEffect(() => {
    if (!hist.data) return;
    setRemaining(hist.data.remaining_today);
    setMsgs(hist.data.messages.flatMap((m) => [{ role: "user" as const, content: m.question }, { role: "assistant" as const, content: m.answer, id: m.id, ai: m.used_llm }]));
  }, [hist.data]);
  useEffect(() => { endRef.current?.scrollIntoView({ block: "nearest" }); }, [msgs.length, busy]);

  const asOf = sig.data?.as_of ?? date;
  const suggestions = [
    focus ? `Why is ${focus} flagged on ${asOf || "this day"}?` : "What needs my attention on this day?",
    "Where did the committee disagree with the engine, and why?",
    "Which of my stocks looks riskiest?",
    "How has the committee done on my stocks so far?",
  ];

  async function send(q: string) {
    const question = q.trim();
    if (!question || busy) return;
    setBusy(true);
    setError(null);
    const history = msgs.slice(-6).map((m) => ({ role: m.role, content: m.content.slice(0, 600) }));
    setMsgs((m) => [...m, { role: "user", content: question }]);
    setText("");
    try {
      const r = await apiFetch<AskResult>("/api/me/ask", { method: "POST", ...opts, body: JSON.stringify({ question, date: date || sig.data?.as_of || null, history }) });
      setMsgs((m) => [...m, { role: "assistant", content: r.answer, id: r.id, ai: r.used_ai }]);
      setRemaining(r.remaining_today);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Could not get an answer");
    } finally {
      setBusy(false);
    }
  }

  const s = sig.data;
  return (
    <div className="flex flex-col gap-sp5">
      <header>
        <h1 className="text-[22px] font-extrabold tracking-tight text-t1">Portfolio assistant</h1>
        <p className="mt-1 max-w-[75ch] text-[12px] text-t3">
          Pick any trading day to see what the engine and the committee said about your stocks and how it turned out, then ask follow-up questions. Answers only use GlassBox&rsquo;s own data. Simulated research, not investment advice.
        </p>
      </header>

      <section className="glass-panel p-sp4" aria-label="Signals for a day">
        <div className="flex flex-wrap items-end gap-sp3">
          <label className="flex flex-col gap-1 text-[11px] text-t3">
            Trading day
            <input type="date" value={date || s?.as_of || ""} min={s?.first_date} max={s?.latest_date} onChange={(e) => setDate(e.target.value)} className="rounded-r1 border border-border bg-bg2 px-sp3 py-sp2 text-[13px] text-t1" />
          </label>
          {s && <button type="button" onClick={() => setDate("")} className="btn btn-ghost text-[12px]">Latest ({s.latest_date})</button>}
          {s && <span className="text-[11px] text-t3">showing {s.as_of}{s.watchlist ? ` · ${s.watchlist.join(", ")}` : " · all tracked stocks"}</span>}
        </div>
        {s?.note && <p className="mt-sp2 text-[11.5px] text-gold">{s.note}</p>}
        {sig.isPending && <p className="mt-sp3 text-[12px] text-t3">Loading…</p>}
        {sig.isError && <p className="mt-sp3 text-[12px] text-red">{sig.error instanceof ApiError ? sig.error.message : "Could not load signals."}</p>}
        {s && (
          <div className="mt-sp3 overflow-x-auto">
            <table className="w-full min-w-[760px] text-left text-[12px]">
              <thead className="text-[10px] uppercase tracking-wide text-t3">
                <tr><th className="py-1 pr-sp3">Stock</th><th className="pr-sp3">Engine</th><th className="pr-sp3">Committee</th><th className="pr-sp3">Risk</th><th className="pr-sp3">Next day</th><th className="pr-sp3">Next 5 days</th><th>Why · feedback</th></tr>
              </thead>
              <tbody>
                {s.rows.map((r) => (
                  <tr key={r.symbol} className={`border-t border-white/[0.04] align-top ${focus === r.symbol ? "bg-teal-dim/40" : ""}`}>
                    <td className="py-1.5 pr-sp3"><b className="text-t1">{r.symbol}</b></td>
                    <td className={`pr-sp3 font-bold ${chip(r.engine_signal)}`}>{r.engine_signal}<span className="ml-1 font-normal text-t3">{r.engine_confidence.toFixed(0)}%</span></td>
                    <td className="pr-sp3">
                      {r.committee_action ? <span className={`font-bold ${chip(r.committee_action)}`}>{r.committee_action}</span> : <span className="text-t3">no review</span>}
                      {r.disagrees && <span className="ml-1 text-[10px] text-gold">overruled engine</span>}
                      {r.gated && <span className="ml-1 text-[10px] text-gold">risk-gated</span>}
                    </td>
                    <td className="pr-sp3 text-t2">{r.risk_level ?? "–"}</td>
                    <td className={`mono pr-sp3 ${tone(r.fwd_1d)}`}>{pct(r.fwd_1d)}</td>
                    <td className={`mono pr-sp3 ${tone(r.fwd_5d)}`}>{pct(r.fwd_5d)}</td>
                    <td className="text-[11px] leading-snug text-t2">
                      {r.committee_headline ?? (r.committee_action ? r.consensus : "The committee did not review this stock that day.")}
                      <div className="mt-1"><FeedbackButtons type="signal" refId={`${r.symbol}|${s.as_of}`} symbol={r.symbol} label="Useful?" /></div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="mt-sp2 text-[10.5px] text-t3">&ldquo;Next day&rdquo; and &ldquo;Next 5 days&rdquo; show how the stock moved afterwards; blank means too recent to know. It is a record, not a promise.</p>
          </div>
        )}
      </section>

      {s && (
        <section className="glass-panel p-sp4" aria-label="Committee performance on your stocks">
          <div className="flex flex-wrap items-baseline justify-between gap-sp2">
            <h2 className="text-[15px] font-bold text-t1">How the committee has done on your stocks</h2>
            <Link href="/dashboard" className="text-[12px] text-teal underline">See your portfolio →</Link>
          </div>
          <div className="mt-sp3 grid grid-cols-3 gap-sp3">
            {(["BUY", "SELL", "HOLD"] as const).map((d) => {
              const t = s.committee_track.by_decision_5d[d];
              return (
                <div key={d} className="rounded-r2 border border-border bg-bg2/40 px-sp3 py-sp3">
                  <div className={`text-[11px] font-bold ${chip(d)}`}>{d} calls</div>
                  <div className="mono mt-1 text-[16px] font-extrabold text-t1">{t?.n ?? 0}</div>
                  <div className="text-[10.5px] text-t3">{t && t.n > 0 ? `${t.hit_rate === null ? "not scored for direction" : `${(t.hit_rate * 100).toFixed(0)}% right`} · avg ${pct(t.mean_return)} over 5 days` : "not enough aged calls yet"}</div>
                </div>
              );
            })}
          </div>
          <p className="mt-sp2 text-[11px] leading-snug text-t3">
            {s.committee_track.reviews} review{s.committee_track.reviews === 1 ? "" : "s"} of your stocks so far
            {s.committee_track.agrees_with_engine != null ? `, agreeing with the engine ${(s.committee_track.agrees_with_engine * 100).toFixed(0)}% of the time` : ""}. {s.committee_track.note}
          </p>
          <div className="mt-sp3"><FeedbackButtons type="committee" label="Is this view of the committee useful?" /></div>
        </section>
      )}

      <section className="glass-panel flex flex-col gap-sp3 p-sp4" aria-label="Ask a follow-up">
        <div className="flex flex-wrap items-baseline justify-between gap-sp2">
          <h2 className="text-[15px] font-bold text-t1">Ask a question{asOf ? ` about ${asOf}` : ""}</h2>
          {remaining != null && <span className="mono text-[10.5px] text-t3">{remaining} question{remaining === 1 ? "" : "s"} left today</span>}
        </div>
        <div className="flex flex-wrap gap-sp2">
          {suggestions.map((q) => (
            <button key={q} type="button" onClick={() => send(q)} disabled={busy} className="rounded-r4 border border-border px-sp3 py-1 text-[11.5px] text-t2 hover:bg-bg3 disabled:opacity-50">{q}</button>
          ))}
        </div>
        <div className="flex max-h-[420px] flex-col gap-sp3 overflow-y-auto" aria-live="polite">
          {msgs.length === 0 && <p className="text-[12px] text-t3">No questions yet. Try one of the suggestions above.</p>}
          {msgs.map((m, i) => (
            <div key={i} className={m.role === "user" ? "self-end rounded-r2 bg-teal-dim px-sp3 py-sp2 text-[12.5px] text-t1" : "max-w-[85ch] rounded-r2 border border-border bg-bg2/40 px-sp3 py-sp2 text-[12.5px] leading-relaxed text-t1"}>
              <span className="whitespace-pre-line">{m.content}</span>
              {m.role === "assistant" && (
                <div className="mt-sp2 flex flex-col gap-1">
                  {m.ai === false && <span className="text-[10.5px] text-gold">Basic summary: the AI wasn&rsquo;t available for this answer.</span>}
                  {m.id && <FeedbackButtons type="answer" refId={m.id} label="Helpful?" />}
                </div>
              )}
            </div>
          ))}
          {busy && <div className="text-[12px] text-t3">Thinking…</div>}
          <div ref={endRef} />
        </div>
        {error && <p role="alert" className="text-[12px] text-red">{error}</p>}
        <form onSubmit={(e) => { e.preventDefault(); send(text); }} className="flex gap-sp2">
          <input value={text} onChange={(e) => setText(e.target.value)} maxLength={500} placeholder="e.g. Why did the committee disagree with the engine on this stock?" className="min-w-0 flex-1 rounded-r1 border border-border bg-bg2 px-sp3 py-sp2 text-[13px] text-t1" aria-label="Your question" />
          <button type="submit" disabled={busy || !text.trim()} className="rounded-r1 bg-teal px-sp4 py-sp2 text-[12px] font-bold text-bg disabled:opacity-50">Ask</button>
        </form>
        <p className="text-[10.5px] text-t3">Answers are drawn only from the data above and can still be wrong. Simulated research, not investment advice.</p>
      </section>

      <section className="glass-panel p-sp4" aria-label="Help shape the committee">
        <h2 className="text-[15px] font-bold text-t1">Help shape the committee</h2>
        <p className="mt-1 max-w-[75ch] text-[12px] text-t3">
          Tell us what you&rsquo;d want from it: a different mix of analysts, a kind of stock it should cover, clearer explanations. The maintainer reads this and uses it to decide what to test. It never changes the committee automatically.
        </p>
        <div className="mt-sp3"><FeedbackButtons type="committee" label="Your idea:" /></div>
      </section>
    </div>
  );
}

export default function AssistantPage() {
  return (
    <Suspense fallback={<p className="text-[12px] text-t3">Loading…</p>}>
      <AssistantInner />
    </Suspense>
  );
}
