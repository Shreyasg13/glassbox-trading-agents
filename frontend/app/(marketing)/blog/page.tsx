"use client";

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { ApiError, apiFetch } from "@/lib/api";
import type { CasebookIndex } from "@/lib/types";
import { LEAN_FILL, LEAN_GLYPH } from "@/components/blog/visuals";
import { Feed } from "@/components/blog/Feed";

/** GlassBox Research blog: the Committee Casebook, one post per day the committee sat. */
export default function BlogIndexPage() {
  const q = useQuery({
    queryKey: ["casebook"],
    queryFn: () => apiFetch<CasebookIndex>("/api/public/casebook?limit=120"),
    staleTime: 5 * 60 * 1000,
    retry: false,
  });

  return (
    <section className="mx-auto max-w-[1100px] px-sp6 py-sp10">
      <div className="mb-sp2 text-[12px] font-bold uppercase tracking-wide text-t3">GlassBox Research</div>
      <h1 className="mb-sp3 text-[32px] font-extrabold leading-tight tracking-tight text-t1">The Committee Casebook</h1>
      <p className="mb-sp8 max-w-[620px] text-[14px] leading-relaxed text-t2">
        Every trading day seven AI analysts and three quant agents argue over the 15 stocks and funds we track. Here is each one&apos;s case,
        the vote, and what the stock did over the next five trading days against SPY. Wrong calls stay up too.
      </p>

      {q.isPending && <p className="text-[13px] text-t3">Loading…</p>}
      {q.isError && <p className="text-[13px] text-red">{q.error instanceof ApiError ? q.error.message : "Could not load the casebook"}</p>}
      {q.data && !q.data.posts.length && <p className="text-[13px] text-t3">No committee reviews yet.</p>}
      <div className="mb-sp10">
        <Feed />
      </div>
      {q.data && q.data.posts.length > 0 && <h2 className="mb-sp4 text-[20px] font-extrabold text-t1">Every day, in full</h2>}

      <ul className="flex flex-col gap-sp3">
        {q.data?.posts.map((p) => (
          <li key={p.date}>
            <Link
              href={`/blog/${p.date}`}
              className="glass-panel block p-sp5 transition-all duration-200 ease-glass hover:-translate-y-0.5 hover:border-border2"
            >
              <div className="mono mb-sp1 text-[11px] text-t3">{p.date}</div>
              <div className="mb-sp2 text-[15px] font-bold text-t1">What the committee argued about {p.symbols.length} stocks</div>
              <div className="flex flex-wrap gap-1">
                {p.symbols.map((s) => {
                  const d = p.decisions[s];
                  return (
                    <span
                      key={s}
                      className="mono flex items-center gap-1 rounded-[4px] border border-border bg-bg2 px-1.5 py-0.5 text-[10.5px] text-t2"
                      title={`${s}: ${d ?? "no call"}`}
                    >
                      <span style={{ color: d ? LEAN_FILL[d] : undefined }}>{d ? LEAN_GLYPH[d] : "–"}</span>
                      {s}
                    </span>
                  );
                })}
              </div>
              {p.disagreements.length > 0 && (
                <div className="mt-sp1 text-[12px] text-gold">Committee overruled the engine on {p.disagreements.join(", ")}</div>
              )}
            </Link>
          </li>
        ))}
      </ul>

      {q.data && <p className="mt-sp8 text-[11px] leading-relaxed text-t3">{q.data.disclaimer}</p>}
    </section>
  );
}
