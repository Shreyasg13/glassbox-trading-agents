"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import type { CasebookTeaser } from "@/lib/types";
import { LEAN_FILL, LEAN_GLYPH } from "@/components/blog/visuals";

const PAGE = 12;
const fmt = (d: string) => new Date(`${d}T12:00:00Z`).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric", timeZone: "UTC" });
const KICKER_TONE: Record<CasebookTeaser["kicker"], string> = { Overrule: "text-gold", Debate: "text-teal", Casebook: "text-t3" };

function PostCard({ p, featured = false }: { p: CasebookTeaser; featured?: boolean }) {
  const moves = p.symbols.filter((s) => p.decisions[s] && p.decisions[s] !== "HOLD");
  return (
    <Link
      href={`/blog/${p.date}`}
      className={`glass-panel group flex h-full flex-col overflow-hidden transition-all duration-200 ease-glass hover:-translate-y-0.5 hover:border-border2 ${featured ? "md:flex-row" : ""}`}
    >
      {/* eslint-disable-next-line @next/next/no-img-element -- generated share PNG (/share/<date>), cached an hour */}
      <img
        src={`/share/${p.date}`}
        alt=""
        loading="lazy"
        className={`aspect-[1200/630] w-full border-b border-border object-cover ${featured ? "md:w-[55%] md:border-b-0 md:border-r" : ""}`}
      />
      <div className={`flex flex-1 flex-col p-sp5 ${featured ? "md:justify-center md:p-sp8" : ""}`}>
        <div className="mb-sp2 flex items-center gap-sp2 text-[10.5px] font-bold uppercase tracking-wide">
          <span className={KICKER_TONE[p.kicker]}>{p.kicker}</span>
          {p.editors_pick && <span className="rounded-r4 bg-teal-dim px-1.5 py-0.5 text-teal">Editor&apos;s pick</span>}
        </div>
        <h3 className={`mb-sp2 font-extrabold leading-snug tracking-tight text-t1 group-hover:underline ${featured ? "text-[24px]" : "text-[16px]"}`}>{p.title}</h3>
        <p className={`mb-sp3 text-t2 ${featured ? "text-[14px]" : "line-clamp-2 text-[12.5px]"}`}>{p.dek}</p>
        {featured && p.excerpt && (
          <blockquote className="mb-sp3 line-clamp-3 border-l-2 border-border2 pl-sp3 text-[13px] italic leading-relaxed text-t2">
            {p.excerpt.text}
            <span className="not-italic text-t3"> · {p.excerpt.agent}</span>
          </blockquote>
        )}
        <div className="mt-auto flex flex-wrap items-center gap-x-sp3 gap-y-1 text-[11.5px] text-t3">
          <span>{fmt(p.date)}</span>
          <span>{p.read_minutes} min read</span>
          {(p.tags.length ? p.tags : moves).slice(0, 4).map((t) => (
            <span key={t} className="mono rounded-[4px] border border-border bg-bg2 px-1.5 py-0.5 text-[10.5px] text-t2">
              {p.decisions[t] && <span style={{ color: LEAN_FILL[p.decisions[t]!] }}>{LEAN_GLYPH[p.decisions[t]!]} </span>}
              {t}
            </span>
          ))}
        </div>
      </div>
    </Link>
  );
}

/** The blog's post list, Substack-style: newest post featured, then a card grid with filters and "load more". */
export function PostGrid({ posts }: { posts: CasebookTeaser[] }) {
  const [filter, setFilter] = useState<string>("All");
  const [shown, setShown] = useState(PAGE);
  const symbols = useMemo(() => Array.from(new Set(posts.flatMap((p) => p.symbols))).sort(), [posts]);
  const list = posts.filter((p) =>
    filter === "All" ? true : filter === "Overrules" ? p.kicker === "Overrule" : filter === "Debates" ? p.kicker === "Debate" : p.focus === filter || p.tags.includes(filter),
  );
  const [first, ...rest] = list;
  const pick = (f: string) => () => { setFilter(f); setShown(PAGE); };

  return (
    <div>
      <div className="mb-sp4 flex flex-wrap items-center gap-sp2" role="toolbar" aria-label="Filter posts">
        {["All", "Overrules", "Debates"].map((f) => (
          <button
            key={f}
            type="button"
            aria-pressed={filter === f}
            onClick={pick(f)}
            className={`rounded-r4 border px-sp3 py-1 text-[12px] font-semibold transition-colors ${filter === f ? "border-teal bg-teal-dim text-teal" : "border-border text-t2 hover:border-border2"}`}
          >
            {f}
          </button>
        ))}
        <select
          aria-label="Filter by stock"
          value={symbols.includes(filter) ? filter : ""}
          onChange={(e) => pick(e.target.value || "All")()}
          className="rounded-r4 border border-border bg-bg1 px-sp2 py-1 text-[12px] text-t2"
        >
          <option value="">By stock…</option>
          {symbols.map((s) => <option key={s} value={s}>{s}</option>)}
        </select>
      </div>

      {!first && <p className="text-[13px] text-t3">No posts match that filter yet.</p>}
      {first && (
        <div className="mb-sp4">
          <PostCard p={first} featured />
        </div>
      )}
      <div className="grid grid-cols-1 gap-sp4 sm:grid-cols-2 lg:grid-cols-3">
        {rest.slice(0, shown).map((p) => <PostCard key={p.date} p={p} />)}
      </div>
      {rest.length > shown && (
        <div className="mt-sp6 text-center">
          <button type="button" className="btn btn-ghost text-[12.5px]" onClick={() => setShown((n) => n + PAGE)}>
            Load more posts
          </button>
        </div>
      )}
    </div>
  );
}
