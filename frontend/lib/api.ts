const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
const WS_URL = process.env.NEXT_PUBLIC_WS_URL ?? "ws://localhost:8000/ws/signals";

import type { ComplianceAction, ComplianceEvent, ComplianceRule, GateHealth, LedgerRow, LedgerVerify, NarrativeEvidence } from "@/lib/types";

export function apiUrl(path: string): string {
  return `${API_URL}${path}`;
}

/** Origin (scheme+host) of the backend's WS listener, derived from NEXT_PUBLIC_WS_URL. */
export function wsOrigin(): string {
  try {
    const u = new URL(WS_URL);
    return `${u.protocol}//${u.host}`;
  } catch {
    return "ws://localhost:8000";
  }
}

export function jobWsUrl(jobId: string): string {
  return `${wsOrigin()}/ws/jobs/${jobId}`;
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

export async function apiFetch<T>(
  path: string,
  opts: RequestInit & { token?: string } = {}
): Promise<T> {
  const { token, headers, ...rest } = opts;
  const res = await fetch(apiUrl(path), {
    ...rest,
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...headers,
    },
  });

  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      const body = await res.json();
      if (typeof body?.detail === "string") detail = body.detail;
    } catch {
      // no JSON body
    }
    throw new ApiError(res.status, detail);
  }

  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

// ---- Ledger API ----

export async function fetchLedger(
  token: string | undefined,
  fromSeq: number = 1,
  limit: number = 100
): Promise<LedgerRow[]> {
  const params = new URLSearchParams({ from_seq: String(fromSeq), limit: String(limit) });
  return apiFetch<LedgerRow[]>(`/api/admin/ledger?${params}`, { token });
}

export async function verifyLedger(token: string | undefined): Promise<LedgerVerify> {
  return apiFetch<LedgerVerify>(`/api/admin/ledger/verify`, { token, method: "POST" });
}

// ---- Compliance API ----

export async function fetchComplianceEvents(
  token: string | undefined,
  opts: { action?: ComplianceAction | ""; from?: string; to?: string; limit?: number } = {}
): Promise<ComplianceEvent[]> {
  const params = new URLSearchParams({ limit: String(opts.limit ?? 200) });
  if (opts.action) params.set("action", opts.action);
  if (opts.from) params.set("from", opts.from);
  if (opts.to) params.set("to", opts.to);
  return apiFetch<ComplianceEvent[]>(`/api/admin/compliance/events?${params}`, { token });
}

export async function fetchComplianceRules(token: string | undefined): Promise<ComplianceRule[]> {
  return apiFetch<ComplianceRule[]>(`/api/admin/compliance/rules`, { token });
}

// ---- Evidence ("Show my work") API (S3 T7) ----

/** The claims behind a report narrative's numbers, each checked against the A6 gate. USER-role only (401 if signed out). */
export async function fetchNarrativeEvidence(token: string | undefined, narrativeId: string): Promise<NarrativeEvidence> {
  return apiFetch<NarrativeEvidence>(`/api/reports/narratives/${narrativeId}/evidence`, { token });
}

// ---- Gate health API (S3 T14) ----

/** Daily claim pass rate and top failing checks/metrics for committee runs dated from..to (YYYY-MM-DD, inclusive). */
export async function fetchGateHealth(token: string | undefined, from: string, to: string): Promise<GateHealth> {
  const params = new URLSearchParams({ from, to });
  return apiFetch<GateHealth>(`/api/admin/gate-health?${params}`, { token });
}
