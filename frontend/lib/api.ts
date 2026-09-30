const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
const WS_URL = process.env.NEXT_PUBLIC_WS_URL ?? "ws://localhost:8000/ws/signals";

import type { ComplianceAction, ComplianceEvent, ComplianceRule, GateHealth, LedgerRow, LedgerVerify, QuarantineItem, QuarantineStatus } from "@/lib/types";

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

// ---- Gate health API (S3 T14) ----

/** Daily claim pass rate and top failing checks/metrics for committee runs dated from..to (YYYY-MM-DD, inclusive). */
export async function fetchGateHealth(token: string | undefined, from: string, to: string): Promise<GateHealth> {
  const params = new URLSearchParams({ from, to });
  return apiFetch<GateHealth>(`/api/admin/gate-health?${params}`, { token });
}

// ---- Quarantine review API (S3 T6) ----

export async function fetchQuarantine(
  token: string | undefined,
  status?: QuarantineStatus | ""
): Promise<QuarantineItem[]> {
  const params = new URLSearchParams({ limit: "200" });
  if (status) params.set("status", status);
  return apiFetch<QuarantineItem[]>(`/api/admin/quarantine?${params}`, { token });
}

/** Thrown by approveQuarantineItem when the gates still fail and no override was given (HTTP 409):
 * carries the failing checks so the caller can show them and offer "approve with override" instead. */
export class QuarantineGateFailureError extends ApiError {
  failingChecks: string[];
  constructor(failingChecks: string[]) {
    super(409, "Quarantine gates still fail");
    this.failingChecks = failingChecks;
  }
}

/** Re-runs A6+A7 for the item. Passing checks -> approved. Still failing with no overrideReason ->
 * throws QuarantineGateFailureError; pass overrideReason (>= 10 characters) to approve anyway. */
export async function approveQuarantineItem(
  token: string | undefined,
  itemId: string,
  overrideReason?: string
): Promise<QuarantineItem> {
  const res = await fetch(apiUrl(`/api/admin/quarantine/${encodeURIComponent(itemId)}/approve`), {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify({ override_reason: overrideReason || undefined }),
  });
  if (res.status === 409) {
    const body = await res.json().catch(() => ({}));
    throw new QuarantineGateFailureError(Array.isArray(body?.detail?.failing_checks) ? body.detail.failing_checks : []);
  }
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
  return res.json() as Promise<QuarantineItem>;
}

export async function rejectQuarantineItem(token: string | undefined, itemId: string, note: string): Promise<QuarantineItem> {
  return apiFetch<QuarantineItem>(`/api/admin/quarantine/${encodeURIComponent(itemId)}/reject`, {
    token,
    method: "POST",
    body: JSON.stringify({ note }),
  });
}
