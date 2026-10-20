"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { ApiError, apiFetch } from "@/lib/api";
import type { Recap } from "@/lib/types";

/** A weekly recap article: written by the Daily Studio from the week's casebook, published only after the owner approves it. */
export default function RecapPage() {
  const { week } = useParams<{ week: string }>();
  const q = useQuery({
    queryKey: ["recap", week],
    queryFn: () => apiFetch<Recap & { disclaimer: string }>(`/api/public/recaps/${encodeURIComponent(`recap:${week}`)}`),
    retry: false,
  });
  return (
    <article className="mx-auto max-w-[720px] px-sp6 py-sp10">
      <Link href="/blog" className="mb-sp4 inline-block text-[12px] text-t3 hover:text-t1">← All casebook posts</Link>
      <div className="mb-sp2 text-[12px] font-bold uppercase tracking-wide text-t3">GlassBox Research · Weekly recap · {week}</div>
      {q.isPending && <p className="text-[13px] text-t3">Loading…</p>}
      {q.isError && <p className="text-[13px] text-red">{q.error instanceof ApiError ? q.error.message : "Could not load this recap"}</p>}
      {q.data && (
        <>
          <h1 className="mb-sp6 text-[32px] font-extrabold leading-tight tracking-tight text-t1">{q.data.title}</h1>
          <div className="flex flex-col gap-sp4 text-[15px] leading-relaxed text-t2">
            {q.data.paragraphs.map((p, i) => (
              <p key={i}>{p}</p>
            ))}
          </div>
          <p className="mt-sp8 text-[11px] leading-relaxed text-t3">
            Drafted by GlassBox&apos;s writing agent from the week&apos;s published calls; every number was checked against that data before an editor approved it.
            {" "}{q.data.disclaimer}
          </p>
        </>
      )}
    </article>
  );
}
