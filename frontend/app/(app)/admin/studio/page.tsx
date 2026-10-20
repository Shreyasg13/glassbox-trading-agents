"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, apiFetch } from "@/lib/api";
import { useAuth } from "@/lib/auth";

type Draft = {
  id: string;
  kind: "linkedin" | "x_thread" | "caption" | "recap";
  date: string;
  status: "ready" | "pending" | "published" | "rejected";
  source: "model" | "template";
  rejected: string[];
  text?: string;
  posts?: string[];
  title?: string;
  paragraphs?: string[];
  week?: string;
  updated_at: string;
};

const LABEL: Record<Draft["kind"], string> = { linkedin: "LinkedIn post", x_thread: "X thread", caption: "Short caption", recap: "Weekly recap" };
const ORDER: Draft["kind"][] = ["linkedin", "x_thread", "caption"];

function CopyButton({ text }: { text: string }) {
  const [done, setDone] = useState(false);
  return (
    <button
      type="button"
      className="btn btn-ghost px-sp3 py-1 text-[11.5px]"
      onClick={() => navigator.clipboard.writeText(text).then(() => { setDone(true); setTimeout(() => setDone(false), 1500); })}
    >
      {done ? "Copied ✓" : "Copy"}
    </button>
  );
}

function Provenance({ d }: { d: Draft }) {
  return (
    <div className="text-[10.5px] text-t3">
      {d.source === "model" ? "Written by the model, passed every check" : "Template draft"}
      {d.rejected.length > 0 && <span title={d.rejected.join("\n")}> · model draft rejected: {d.rejected[0]}</span>}
    </div>
  );
}

function SocialCard({ d }: { d: Draft }) {
  const text = d.posts ? d.posts.join("\n\n") : d.text ?? "";
  return (
    <div className="rounded-r2 border border-border bg-bg2/40 p-sp4">
      <div className="mb-sp2 flex items-center justify-between gap-sp2">
        <span className="text-[12.5px] font-bold text-t1">{LABEL[d.kind]}</span>
        <CopyButton text={text} />
      </div>
      {d.posts ? (
        <ol className="mb-sp2 flex flex-col gap-sp2">
          {d.posts.map((p, i) => (
            <li key={i} className="flex items-start gap-sp2 rounded-r1 bg-bg1 p-sp2 text-[12.5px] leading-relaxed text-t2">
              <span className="mono shrink-0 text-[10.5px] text-t3">{i + 1}/{d.posts!.length}</span>
              <span className="flex-1 whitespace-pre-wrap">{p}</span>
              <span className="mono shrink-0 text-[10px] text-t3">{p.length}</span>
              <CopyButton text={p} />
            </li>
          ))}
        </ol>
      ) : (
        <p className="mb-sp2 whitespace-pre-wrap text-[12.5px] leading-relaxed text-t2">{d.text}</p>
      )}
      <Provenance d={d} />
    </div>
  );
}

/** The Daily Studio: share-ready drafts written after each committee run. Copy, attach the image, post by hand. */
export default function StudioPage() {
  const { token } = useAuth();
  const qc = useQueryClient();
  const q = useQuery({
    queryKey: ["studio-drafts"],
    queryFn: () => apiFetch<{ drafts: Draft[] }>("/api/admin/studio/drafts?limit=120", { token: token ?? undefined }),
    enabled: !!token,
  });
  const run = useMutation({
    mutationFn: (recap: boolean) => apiFetch("/api/admin/studio/run", { method: "POST", token: token ?? undefined, body: JSON.stringify({ recap }) }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["studio-drafts"] }),
  });
  const review = useMutation({
    mutationFn: ({ id, status }: { id: string; status: string }) =>
      apiFetch(`/api/admin/studio/drafts/${encodeURIComponent(id)}/status`, { method: "POST", token: token ?? undefined, body: JSON.stringify({ status }) }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["studio-drafts"] }),
  });

  const drafts = q.data?.drafts ?? [];
  const recaps = drafts.filter((d) => d.kind === "recap");
  const days = Array.from(new Set(drafts.filter((d) => d.kind !== "recap").map((d) => d.date))).sort().reverse();
  const err = (e: unknown) => (e instanceof ApiError ? e.message : "failed");

  return (
    <div className="flex flex-col gap-sp6">
      <div className="flex flex-wrap items-end justify-between gap-sp3">
        <div>
          <h1 className="mono text-[18px] font-bold text-t1">Daily Studio</h1>
          <p className="max-w-[640px] text-[12.5px] text-t3">
            Written automatically after each committee run from that day&apos;s Casebook. Every number is checked against the day&apos;s data and every draft
            passes the compliance filter. Nothing is posted for you: copy a draft, download the image, and post it yourself. Links carry UTM tags,
            so Traction shows which post brought visitors.
          </p>
        </div>
        <div className="flex gap-sp2">
          <button type="button" className="btn btn-ghost text-[12px]" disabled={run.isPending} onClick={() => run.mutate(false)}>
            {run.isPending ? "Writing…" : "Write today's drafts now"}
          </button>
          <button type="button" className="btn btn-ghost text-[12px]" disabled={run.isPending} onClick={() => run.mutate(true)}>
            Write this week&apos;s recap
          </button>
        </div>
      </div>
      {run.isError && <p className="text-[12px] text-red">{err(run.error)}</p>}
      {q.isPending && <p className="text-[12px] text-t3">Loading…</p>}
      {q.isError && <p className="text-[12px] text-red">{err(q.error)}</p>}
      {q.data && !drafts.length && <p className="text-[12px] text-t3">No drafts yet. They appear after the next committee run, or press &ldquo;Write today&apos;s drafts now&rdquo;.</p>}

      {recaps.length > 0 && (
        <section className="flex flex-col gap-sp3">
          <h2 className="text-[14px] font-bold text-t1">Weekly recaps (need your approval for /blog)</h2>
          {recaps.map((r) => (
            <div key={r.id} className="rounded-r2 border border-border bg-bg2/40 p-sp4">
              <div className="mb-sp2 flex flex-wrap items-center justify-between gap-sp2">
                <span className="text-[13px] font-bold text-t1">{r.title}</span>
                <span className={`rounded-r4 px-sp2 py-0.5 text-[10.5px] font-bold uppercase ${r.status === "published" ? "bg-teal-dim text-teal" : r.status === "rejected" ? "bg-red-dim text-red" : "bg-gold-dim text-gold"}`}>
                  {r.status}
                </span>
              </div>
              <div className="mb-sp3 flex flex-col gap-sp2 text-[12.5px] leading-relaxed text-t2">
                {r.paragraphs?.map((p, i) => <p key={i}>{p}</p>)}
              </div>
              <div className="flex flex-wrap items-center gap-sp2">
                {r.status !== "published" && (
                  <button type="button" className="btn btn-primary px-sp3 py-1 text-[11.5px]" onClick={() => review.mutate({ id: r.id, status: "published" })}>
                    Publish to /blog
                  </button>
                )}
                {r.status !== "rejected" && (
                  <button type="button" className="btn btn-ghost px-sp3 py-1 text-[11.5px]" onClick={() => review.mutate({ id: r.id, status: "rejected" })}>
                    {r.status === "published" ? "Unpublish" : "Reject"}
                  </button>
                )}
                <Provenance d={r} />
              </div>
            </div>
          ))}
        </section>
      )}

      {days.map((d) => (
        <section key={d} className="flex flex-col gap-sp3">
          <div className="flex flex-wrap items-baseline justify-between gap-sp2">
            <h2 className="text-[14px] font-bold text-t1">Casebook {d}</h2>
            <span className="flex gap-sp3 text-[12px]">
              <a href={`/blog/${d}`} target="_blank" rel="noreferrer" className="text-teal hover:underline">Open post ↗</a>
              <a href={`/share/${d}`} download={`glassbox-casebook-${d}.png`} className="text-teal hover:underline">Download image ↓</a>
            </span>
          </div>
          <div className="grid grid-cols-1 gap-sp4 lg:grid-cols-[minmax(0,1fr)_360px]">
            <div className="flex flex-col gap-sp3">
              {ORDER.map((k) => drafts.find((x) => x.date === d && x.kind === k)).filter((x): x is Draft => !!x).map((x) => (
                <SocialCard key={x.id} d={x} />
              ))}
            </div>
            {/* eslint-disable-next-line @next/next/no-img-element -- generated PNG, no optimisation needed */}
            <img src={`/share/${d}`} alt={`Share image for ${d}`} className="w-full rounded-r2 border border-border" loading="lazy" />
          </div>
        </section>
      ))}
    </div>
  );
}
