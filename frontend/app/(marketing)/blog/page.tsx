"use client";

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { ApiError, apiFetch } from "@/lib/api";
import type { CasebookIndex, Recap } from "@/lib/types";
import { Feed } from "@/components/blog/Feed";
import { PostGrid } from "@/components/blog/PostGrid";

/** GlassBox Research blog: the Committee Casebook, one post per day the committee sat. */
export default function BlogIndexPage() {
  const q = useQuery({
    queryKey: ["casebook"],
    queryFn: () => apiFetch<CasebookIndex>("/api/public/casebook?limit=120"),
    staleTime: 5 * 60 * 1000,
    retry: false,
  });
  const recaps = useQuery({
    queryKey: ["recaps"],
    queryFn: () => apiFetch<{ recaps: Recap[] }>("/api/public/recaps"),
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
      {(recaps.data?.recaps.length ?? 0) > 0 && (
        <div className="mb-sp10">
          <h2 className="mb-sp4 text-[20px] font-extrabold text-t1">Weekly recaps</h2>
          <div className="grid grid-cols-1 gap-sp3 md:grid-cols-2">
            {recaps.data!.recaps.map((r) => (
              <Link
                key={r.id}
                href={`/blog/week/${r.id.replace(/^recap:/, "")}`}
                className="glass-panel block p-sp5 transition-all duration-200 ease-glass hover:-translate-y-0.5 hover:border-border2"
              >
                <div className="mono mb-sp1 text-[11px] text-t3">{r.id.replace(/^recap:/, "")}</div>
                <div className="mb-sp2 text-[15px] font-bold text-t1">{r.title}</div>
                <p className="line-clamp-2 text-[12.5px] text-t2">{r.paragraphs[0]}</p>
              </Link>
            ))}
          </div>
        </div>
      )}
      {q.data && q.data.posts.length > 0 && (
        <>
          <h2 className="mb-sp4 text-[20px] font-extrabold text-t1">All posts</h2>
          <PostGrid posts={q.data.posts} />
        </>
      )}

      {q.data && <p className="mt-sp8 text-[11px] leading-relaxed text-t3">{q.data.disclaimer}</p>}
    </section>
  );
}
