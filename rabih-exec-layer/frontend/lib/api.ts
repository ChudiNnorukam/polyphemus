const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
const API_TOKEN = process.env.NEXT_PUBLIC_API_TOKEN ?? "";

const headers = {
  Authorization: `Bearer ${API_TOKEN}`,
  "Content-Type": "application/json",
};

async function apiFetch<T>(path: string, params?: Record<string, string | number>): Promise<T> {
  const url = new URL(`${API_BASE}${path}`);
  if (params) {
    Object.entries(params).forEach(([k, v]) => url.searchParams.set(k, String(v)));
  }
  const res = await fetch(url.toString(), { headers, cache: "no-store" });
  if (!res.ok) throw new Error(`API error ${res.status}: ${path}`);
  return res.json();
}

export interface Stats {
  decisions: number;
  open_actions: number;
  risks: number;
  drifting: number;
}

export interface Decision {
  id: number;
  text: string;
  attributed_to: string | null;
  confidence: number;
  extracted_at: string;
  channel_name: string;
  linked_actions: number;
}

export interface Action {
  id: number;
  action_text: string;
  assignee_id: string | null;
  due_date: string | null;
  status: "open" | "resolved" | "drifted";
  created_at: string;
  confidence: number;
  channel_name: string;
}

export interface Risk {
  id: number;
  text: string;
  raised_by: string | null;
  confidence: number;
  extracted_at: string;
  channel_name: string;
}

export interface DriftEvent {
  id: number;
  action_text: string;
  assignee_id: string | null;
  days_overdue: number;
  action_created_at: string;
  detected_at: string;
  channel_name: string;
}

export const getStats = () => apiFetch<Stats>("/api/stats");

export const getDecisions = (params?: { limit?: number; offset?: number; channel?: string }) =>
  apiFetch<{ decisions: Decision[]; count: number }>("/api/decisions", params as any);

export const getActions = (params?: { limit?: number; status?: string; assignee?: string }) =>
  apiFetch<{ actions: Action[]; count: number }>("/api/actions", params as any);

export const getRisks = (params?: { limit?: number; severity?: string }) =>
  apiFetch<{ risks: Risk[]; count: number }>("/api/risks", params as any);

export const getDrift = (params?: { limit?: number }) =>
  apiFetch<{ drift: DriftEvent[]; count: number }>("/api/drift", params as any);
