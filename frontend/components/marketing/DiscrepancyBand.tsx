"use client";

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { Reveal } from "@/components/Reveal";
import { apiFetch } from "@/lib/api";
import type { CasebookIndex } from "@/lib/types";
import { CountUpStat } from "./CountUpStat";

// Every number here is counted from the public Committee Casebook at page load. The band used to show
// hardcoded figures (an accuracy %, errors caught, a trust score) that no data backed; a research site
// must not print a number it cannot point to, so if the casebook can't be read the band shows no stats.
export function DiscrepancyBand() {
  const q = useQuery({
    queryKey: ["casebook"],
    queryFn: () => apiFetch<CasebookIndex>("/api/public/casebook?limit=120"),
    staleTime: 5 * 60 * 1000,
    retry: false,
  });
  const posts = q.data?.posts ?? [];
  const decisions = posts.flatMap((p) => Object.values(p.decisions));
  const calls = decisions.length;
  const holds = decisions.filter((d) => d === "HOLD").length;
  const stats = posts.length
    ? [
        { target: posts.length, decimals: 0, suffix: "", label: "trading days of committee calls, all public", color: "text-t1" },
        { target: calls, decimals: 0, suffix: "", label: "calls published before the outcome was known", color: "text-t1" },
        { target: posts.filter((p) => p.disagreements.length).length, decimals: 0, suffix: "", label: "days the AI overruled the plain engine", color: "text-gold" },
        { target: Math.round((holds / Math.max(calls, 1)) * 100), decimals: 0, suffix: "%", label: "of calls were HOLD: most days the AI sees no edge", color: "text-t3" },
      ]
    : [];

  return (
    <Reveal>
      <div className="border-y border-border bg-panel px-sp6 py-sp6 md:px-sp10">
        <div className="mx-auto flex max-w-[1100px] flex-wrap items-center justify-between gap-sp6">
          {stats.map((s) => (
            <CountUpStat key={s.label} {...s} />
          ))}
          <Link href="/blog" className="text-[12.5px] font-semibold text-teal hover:underline">
            Read every call in the Casebook →
          </Link>
        </div>
      </div>
    </Reveal>
  );
}
