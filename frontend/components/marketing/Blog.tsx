"use client";

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { Reveal } from "@/components/Reveal";
import { apiFetch } from "@/lib/api";
import type { CasebookIndex } from "@/lib/types";

const posts = [
  {
    icon: "◔",
    title: "Why we publish our own error rate",
    excerpt:
      "Every AI research tool makes mistakes. Most hide them. Here's why showing ours, every week, is the point.",
  },
  {
    icon: "⟲",
    title: "How Agent A6 audits Agent A5",
    excerpt:
      "A walkthrough of the verification step that runs before any narrative reaches your screen, and what happens when it disagrees.",
  },
  {
    icon: "ƒ",
    title: "Reading a Debt/Equity ratio without the jargon",
    excerpt: "The raw metrics behind a risk score, explained the way we'd explain them to a client.",
  },
];

export function Blog() {
  // The newest published Committee Casebook posts lead; the editorial notes stay as "coming soon".
  const q = useQuery({
    queryKey: ["casebook", "latest"],
    queryFn: () => apiFetch<CasebookIndex>("/api/public/casebook?limit=3"),
    staleTime: 5 * 60 * 1000,
    retry: false,
  });
  const latest = q.data?.posts ?? [];

  return (
    <section id="blog" className="scroll-mt-20 border-t border-border px-sp6 py-sp10 md:px-sp10">
      <Reveal>
        <div className="mb-sp2 text-[12px] font-bold uppercase tracking-wide text-t3">From the desk</div>
        <h2 className="mb-sp8 max-w-[560px] text-[32px] font-extrabold leading-tight tracking-tight text-t1">
          Notes on transparency.
        </h2>
      </Reveal>
      {latest.length > 0 && (
        <>
          <div className="mb-sp4 flex items-baseline justify-between gap-sp3">
            <h3 className="text-[15px] font-bold text-t1">The Committee Casebook</h3>
            <Link href="/blog" className="text-[12.5px] font-semibold text-teal hover:underline">
              All posts →
            </Link>
          </div>
          <div className="mb-sp8 grid grid-cols-1 gap-sp5 md:grid-cols-3">
            {latest.map((p, i) => (
              <Reveal key={p.date} delayMs={i * 100}>
                <Link
                  href={`/blog/${p.date}`}
                  className="glass-panel block h-full p-sp5 transition-all duration-200 ease-glass hover:-translate-y-1 hover:border-border2 hover:shadow-lg2"
                >
                  <div className="mb-sp3 flex items-center justify-between">
                    <span className="mono text-[11px] text-t3">{p.date}</span>
                    <span className="rounded-r4 bg-teal-dim px-sp2 py-[3px] text-[10px] font-bold uppercase tracking-wide text-teal">Published</span>
                  </div>
                  <h4 className="mb-sp2 text-[14.5px] font-bold text-t1">What the committee argued: {p.symbols.join(", ")}</h4>
                  <p className="text-[13px] leading-relaxed text-t2">
                    Every analyst&apos;s case, the vote and how it played out.
                    {p.disagreements.length > 0 && ` The committee overruled the engine on ${p.disagreements.join(", ")}.`}
                  </p>
                </Link>
              </Reveal>
            ))}
          </div>
        </>
      )}
      <div className="grid grid-cols-1 gap-sp5 md:grid-cols-3">
        {posts.map((p, i) => (
          <Reveal key={p.title} delayMs={i * 100}>
            <div className="glass-panel relative h-full overflow-hidden p-sp5 transition-all duration-200 ease-glass hover:-translate-y-1 hover:border-border2 hover:shadow-lg2">
              <div className="mb-sp4 flex items-center justify-between">
                <div className="grid h-[38px] w-[38px] place-items-center rounded-r2 bg-teal-dim text-[16px] text-teal">
                  {p.icon}
                </div>
                <span className="rounded-r4 border border-border2 bg-bg2 px-sp2 py-[3px] text-[10px] font-bold uppercase tracking-wide text-t3">
                  Coming soon
                </span>
              </div>
              <h3 className="mb-sp2 text-[14.5px] font-bold text-t1">{p.title}</h3>
              <p className="text-[13px] leading-relaxed text-t2">{p.excerpt}</p>
            </div>
          </Reveal>
        ))}
      </div>
    </section>
  );
}
