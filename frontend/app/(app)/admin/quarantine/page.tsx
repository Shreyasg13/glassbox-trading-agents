"use client";

import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, approveQuarantineItem, fetchQuarantine, QuarantineGateFailureError, rejectQuarantineItem } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import type { QuarantineItem, QuarantineStatus } from "@/lib/types";

const statusColor: Record<QuarantineStatus, string> = {
  pending: "text-gold",
  approved: "text-teal",
  rejected: "text-red",
  shadow: "text-t3",
};

const FILTERS: (QuarantineStatus | "")[] = ["pending", "approved", "rejected", "shadow", ""];

function StatusBadge({ status }: { status: QuarantineStatus }) {
  return <span className={`font-semibold uppercase ${statusColor[status]}`}>{status}</span>;
}

function QuarantineRow({
  item,
  onApprove,
  onOverride,
  onReject,
  isBusy,
  failingChecks,
}: {
  item: QuarantineItem;
  onApprove: (item: QuarantineItem) => void;
  onOverride: (item: QuarantineItem) => void;
  onReject: (item: QuarantineItem) => void;
  isBusy: boolean;
  failingChecks?: string[];
}) {
  return (
    <tr className="border-t border-border align-top text-[12px]">
      <td className="mono py-sp2 pr-sp3 text-t2">{item.created_at}</td>
      <td className="mono py-sp2 pr-sp3 text-t2">{item.stage}</td>
      <td className="py-sp2 pr-sp3 text-t1">{item.channel}</td>
      <td className="py-sp2 pr-sp3 text-t2">
        <StatusBadge status={item.status} />
      </td>
      <td className="max-w-[36ch] py-sp2 pr-sp3 text-t2">
        {item.reason ?? "—"}
        {failingChecks && failingChecks.length > 0 && (
          <div className="mt-1 text-[11px] font-semibold text-red">Still failing: {failingChecks.join("; ")}</div>
        )}
      </td>
      <td className="py-sp2 pr-sp3 text-t3">
        {item.decided_by ? (
          <>
            {item.decided_by}
            <br />
            <span className="mono text-[11px]">{item.decided_at}</span>
          </>
        ) : (
          "—"
        )}
      </td>
      <td className="py-sp2">
        {item.status === "pending" && (
          <div className="flex flex-wrap gap-sp2">
            <button className="btn btn-primary" disabled={isBusy} onClick={() => onApprove(item)}>
              Approve
            </button>
            <button className="btn btn-ghost" disabled={isBusy} onClick={() => onOverride(item)}>
              Approve with override
            </button>
            <button className="btn btn-ghost text-red" disabled={isBusy} onClick={() => onReject(item)}>
              Reject
            </button>
          </div>
        )}
      </td>
    </tr>
  );
}

export default function QuarantinePage() {
  const { token } = useAuth();
  const qc = useQueryClient();
  const [filter, setFilter] = useState<QuarantineStatus | "">("pending");
  const [failingById, setFailingById] = useState<Record<string, string[]>>({});

  const { data, isLoading, error } = useQuery({
    queryKey: ["quarantine-items", filter],
    queryFn: () => fetchQuarantine(token ?? undefined, filter),
    enabled: !!token,
  });

  const items = useMemo(() => {
    const rows = data ?? [];
    // Pending first, newest first within each group (the API already orders by created_at desc).
    return [...rows].sort((a, b) => (a.status === "pending") === (b.status === "pending") ? 0 : a.status === "pending" ? -1 : 1);
  }, [data]);

  const invalidate = () => qc.invalidateQueries({ queryKey: ["quarantine-items"] });

  const approve = useMutation({
    mutationFn: ({ id, overrideReason }: { id: string; overrideReason?: string }) =>
      approveQuarantineItem(token ?? undefined, id, overrideReason),
    onMutate: ({ id }) => setFailingById((prev) => ({ ...prev, [id]: [] })),
    onSuccess: (_data, { id }) => {
      setFailingById((prev) => {
        const next = { ...prev };
        delete next[id];
        return next;
      });
      invalidate();
    },
    onError: (err, { id }) => {
      if (err instanceof QuarantineGateFailureError) {
        setFailingById((prev) => ({ ...prev, [id]: err.failingChecks }));
      } else {
        window.alert(err instanceof ApiError ? err.message : "Approve failed.");
      }
    },
  });

  const reject = useMutation({
    mutationFn: ({ id, note }: { id: string; note: string }) => rejectQuarantineItem(token ?? undefined, id, note),
    onSuccess: invalidate,
    onError: (err) => window.alert(err instanceof ApiError ? err.message : "Reject failed."),
  });

  const handleApprove = (item: QuarantineItem) => approve.mutate({ id: item.id });

  const handleOverride = (item: QuarantineItem) => {
    const reason = window.prompt(
      "Reason for approving despite the failing checks (at least 10 characters):",
      failingById[item.id]?.length ? `Still failing: ${failingById[item.id].join("; ")} — reviewed and ` : ""
    );
    if (reason === null) return;
    if (reason.trim().length < 10) {
      window.alert("The override reason must be at least 10 characters.");
      return;
    }
    approve.mutate({ id: item.id, overrideReason: reason.trim() });
  };

  const handleReject = (item: QuarantineItem) => {
    const note = window.prompt("Note for rejecting this item:");
    if (note === null) return;
    if (note.trim().length === 0) {
      window.alert("A note is required to reject an item.");
      return;
    }
    reject.mutate({ id: item.id, note: note.trim() });
  };

  const isBusy = approve.isPending || reject.isPending;

  return (
    <div className="flex flex-col gap-sp5">
      <div>
        <h1 className="text-[18px] font-bold text-t1">Quarantine</h1>
        <p className="mt-1 max-w-[70ch] text-[12px] text-t3">
          Output the A6 (verification) or A7 (compliance) gate held. Approve re-runs both gates for the item's run and
          content; if they still fail you need an override reason to publish anyway. Reject needs a note.
        </p>
      </div>

      <div className="flex gap-sp2">
        {FILTERS.map((f) => (
          <button
            key={f || "all"}
            className={`btn ${filter === f ? "btn-primary" : "btn-ghost"}`}
            onClick={() => setFilter(f)}
          >
            {f || "All"}
          </button>
        ))}
      </div>

      <div className="glass-panel p-sp5">
        {isLoading && <p className="text-[13px] text-t3">Loading…</p>}
        {error && <p className="text-[13px] font-semibold text-red">Failed to load quarantine items.</p>}
        {data && items.length === 0 && <p className="text-[13px] text-t3">Nothing here.</p>}
        {items.length > 0 && (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-[12px]">
              <thead>
                <tr className="text-t3">
                  <th className="pb-sp2 pr-sp3 font-semibold">Created</th>
                  <th className="pb-sp2 pr-sp3 font-semibold">Stage</th>
                  <th className="pb-sp2 pr-sp3 font-semibold">Channel</th>
                  <th className="pb-sp2 pr-sp3 font-semibold">Status</th>
                  <th className="pb-sp2 pr-sp3 font-semibold">Reason</th>
                  <th className="pb-sp2 pr-sp3 font-semibold">Decided by</th>
                  <th className="pb-sp2 font-semibold">Actions</th>
                </tr>
              </thead>
              <tbody>
                {items.map((item) => (
                  <QuarantineRow
                    key={item.id}
                    item={item}
                    onApprove={handleApprove}
                    onOverride={handleOverride}
                    onReject={handleReject}
                    isBusy={isBusy}
                    failingChecks={failingById[item.id]}
                  />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
