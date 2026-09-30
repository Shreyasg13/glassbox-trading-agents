"use client";

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { GlassPanel } from "@/components/GlassPanel";
import { useAuth } from "@/lib/auth";
import { fetchNarrativeEvidence } from "@/lib/api";
import type { EvidenceClaim } from "@/lib/types";

/**
 * "Show my work" (S3 T7): the verified badge plus an expandable panel listing every
 * claim behind a report narrative's numbers, each checked against the A6 gate.
 *
 * USER-role only (see docs/s3/GLASSBOX_S3_BUILD_PLAN.md 5.1) -- signed-out visitors
 * see the report itself (that route is public) but not this panel, so it renders
 * nothing without a token rather than surfacing the backend's 401.
 */
export function EvidencePanel({ narrativeId }: { narrativeId: string }) {
  const { token } = useAuth();
  const [expanded, setExpanded] = useState(false);
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const { data, isLoading } = useQuery({
    queryKey: ["narrative-evidence", narrativeId],
    queryFn: () => fetchNarrativeEvidence(token ?? undefined, narrativeId),
    enabled: !!token && !!narrativeId,
    retry: false,
  });

  // No checked numbers -> no badge at all: "Verified 0/0" would claim a check that never happened.
  if (!token || isLoading || !data || data.claims.length === 0) return null;

  const selected = data.claims.find((c) => c.claim_id === selectedId) ?? null;

  return (
    <GlassPanel variant="panel" className="flex flex-col gap-sp3">
      <div className="flex items-center justify-between gap-sp3">
        <div className="flex items-center gap-sp2">
          <span
            className={`rounded-r4 px-sp3 py-1 text-[11px] font-bold uppercase tracking-wide ${
              data.verified ? "bg-teal/15 text-teal" : "bg-gold/15 text-gold"
            }`}
          >
            {data.verified ? "Verified" : "Not fully verified"}
          </span>
          <span className="mono text-[11px] text-t3">
            {data.claims.filter((c) => c.passed).length}/{data.claims.length} numbers checked against source
          </span>
        </div>
        {data.claims.length > 0 && (
          <button
            type="button"
            onClick={() => setExpanded((e) => !e)}
            className="text-[12px] font-semibold text-teal hover:underline"
          >
            {expanded ? "Hide my work" : "Show my work"}
          </button>
        )}
      </div>

      {expanded && data.claims.length > 0 && (
        <div className="flex flex-col gap-sp3 border-t border-border pt-sp3 md:flex-row">
          <ul className="flex flex-1 flex-col gap-sp1">
            {data.claims.map((c) => (
              <li key={c.claim_id}>
                <button
                  type="button"
                  onClick={() => setSelectedId(c.claim_id === selectedId ? null : c.claim_id)}
                  className={`flex w-full items-center justify-between rounded-r2 border px-sp3 py-sp2 text-left text-[12.5px] transition-colors ${
                    c.claim_id === selectedId
                      ? "border-teal bg-teal/10 text-t1"
                      : "border-border bg-bg2 text-t2 hover:border-teal/50"
                  }`}
                >
                  <span className="mono">{c.label}</span>
                  <span className="flex items-center gap-sp2">
                    <span className="mono font-semibold text-t1">{formatClaimValue(c)}</span>
                    <span className={c.passed ? "text-teal" : "text-gold"}>{c.passed ? "✓" : "!"}</span>
                  </span>
                </button>
              </li>
            ))}
          </ul>

          <div className="flex-1 rounded-r2 border border-border bg-bg2 p-sp4">
            {selected ? (
              <div className="flex flex-col gap-sp2 text-[12.5px]">
                <div className="text-[13px] font-bold text-t1">{selected.label}</div>
                <Row label="Value" value={formatClaimValue(selected)} />
                <Row label="Source" value={selected.source} />
                {selected.field_path && <Row label="Field path" value={selected.field_path} mono />}
                {selected.as_of && <Row label="As of" value={selected.as_of} />}
                <Row
                  label="Status"
                  value={selected.passed ? "Verified against source" : "Not verified against source"}
                />
              </div>
            ) : (
              <p className="text-[12px] text-t3">Click a number to see where it came from.</p>
            )}
          </div>
        </div>
      )}
    </GlassPanel>
  );
}

function Row({ label, value, mono = false }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="flex items-baseline justify-between gap-sp3">
      <span className="text-[10.5px] uppercase tracking-wide text-t4">{label}</span>
      <span className={`text-right text-t2 ${mono ? "mono break-all" : ""}`}>{value}</span>
    </div>
  );
}

function formatClaimValue(c: EvidenceClaim): string {
  switch (c.unit) {
    case "pct":
      return `${(c.value * 100).toFixed(2)}%`;
    case "USD":
      return `$${c.value.toFixed(2)}`;
    case "ratio":
      return c.value.toFixed(2);
    case "count":
      return String(Math.round(c.value));
    default:
      return String(c.value);
  }
}
