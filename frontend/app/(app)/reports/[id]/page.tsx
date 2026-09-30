"use client";

import { useParams } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { apiFetch } from "@/lib/api";
import { EvidencePanel } from "@/components/EvidencePanel";
import { GlassPanel } from "@/components/GlassPanel";
import { GuideBubble } from "@/components/onboarding/GuideBubble";
import { PortfolioValueChart } from "@/components/reports/PortfolioValueChart";
import { SignalBreakdownChart } from "@/components/reports/SignalBreakdownChart";
import type { DailyReportNarrative } from "@/lib/types";

export default function ReportDetailPage() {
  const { id } = useParams<{ id: string }>();

  // No auth required per contract — /api/reports/narratives/{id} is public.
  const { data, isLoading, error } = useQuery({
    queryKey: ["report-narrative", id],
    queryFn: () => apiFetch<DailyReportNarrative>(`/api/reports/narratives/${id}`),
    enabled: !!id,
  });

  if (isLoading) return <p className="text-[13px] text-t3">Loading…</p>;
  if (error || !data) return <p className="text-[13px] font-semibold text-red">Report not found.</p>;

  return (
    <div className="flex flex-col gap-sp5">
      <div className="flex items-center justify-between">
        <h1 className="mono text-[18px] font-bold text-t1">
          {data.date}
          {data.title && <span className="ml-sp3 font-sans text-[14px] font-semibold text-t2">{data.title}</span>}
        </h1>
        <span className="text-[11px] font-semibold uppercase text-t3">
          {data.provider} · {data.model}
        </span>
      </div>
      <GuideBubble
        compact
        message="Instead of asking you to trust this narrative, GlassBox exposes the data behind it below and independently checked the result before you saw it."
      />
      <GlassPanel variant="raised">
        <p className="whitespace-pre-wrap text-[14px] leading-relaxed text-t1">{data.narrative}</p>
      </GlassPanel>

      <EvidencePanel narrativeId={data.id} />

      <div className="grid grid-cols-1 gap-sp5 lg:grid-cols-2">
        <PortfolioValueChart />
        <SignalBreakdownChart />
      </div>
      <p className="text-[10.5px] text-t3">
        Charts reflect the latest available data, not necessarily a frozen snapshot from this
        report&apos;s exact generation date.
      </p>
    </div>
  );
}
