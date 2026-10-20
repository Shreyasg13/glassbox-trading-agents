"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { ApiError, apiFetch } from "@/lib/api";
import type { CasebookPost } from "@/lib/types";
import { CaseCard } from "@/components/blog/CaseCard";

/** One day's casebook post: every reviewed stock with the analysts' labelled reasoning. */
export default function BlogPostPage() {
  const { date } = useParams<{ date: string }>();
  const q = useQuery({
    queryKey: ["casebook", date],
    queryFn: () => apiFetch<CasebookPost>(`/api/public/casebook/${date}`),
    staleTime: 5 * 60 * 1000,
    retry: false,
  });

  return (
    <section className="mx-auto max-w-[860px] px-sp6 py-sp10">
      <Link href="/blog" className="mb-sp4 inline-block text-[12px] text-t3 hover:text-t1">← All casebook posts</Link>
      <div className="mb-sp2 text-[12px] font-bold uppercase tracking-wide text-t3">GlassBox Research · Committee Casebook</div>
      <h1 className="mb-sp3 text-[30px] font-extrabold leading-tight tracking-tight text-t1">What the committee argued on {date}</h1>

      {q.isPending && <p className="text-[13px] text-t3">Loading…</p>}
      {q.isError && <p className="text-[13px] text-red">{q.error instanceof ApiError ? q.error.message : "Could not load this post"}</p>}

      {q.data && (
        <>
          <p className="mb-sp8 max-w-[640px] text-[14px] leading-relaxed text-t2">
            {q.data.cases.length} stock{q.data.cases.length === 1 ? "" : "s"} reviewed. Each analyst saw the same real numbers and was told to use only
            those. Their reasoning was recorded before the outcome was known; the result line is filled in {q.data.horizon} trading days later.
          </p>
          <div className="flex flex-col gap-sp5">
            {q.data.cases.map((c) => (
              <CaseCard key={c.symbol} c={c} />
            ))}
          </div>
          <p className="mt-sp8 text-[11px] leading-relaxed text-t3">{q.data.disclaimer}</p>
        </>
      )}
    </section>
  );
}
