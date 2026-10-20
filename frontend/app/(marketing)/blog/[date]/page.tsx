"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { ApiError, apiFetch } from "@/lib/api";
import type { CasebookPost } from "@/lib/types";
import { CaseCard } from "@/components/blog/CaseCard";
import { LeanHeatmap, OutcomeBars, PostHero, Takeaways, postFacts } from "@/components/blog/visuals";

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
      {q.isPending && <p className="text-[13px] text-t3">Loading…</p>}
      {q.isError && <p className="text-[13px] text-red">{q.error instanceof ApiError ? q.error.message : "Could not load this post"}</p>}

      {q.data && (
        <>
          <PostHero f={postFacts(q.data)} date={date} cases={q.data.cases} />
          <LeanHeatmap cases={q.data.cases} />
          <OutcomeBars cases={q.data.cases} horizon={q.data.horizon} />
          <Takeaways f={postFacts(q.data)} />
          <h2 className="mb-sp4 text-[20px] font-extrabold text-t1">Every case, in full</h2>
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
