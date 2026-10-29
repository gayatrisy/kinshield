export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const init: RequestInit = { method, credentials: "same-origin", headers: {} };
  if (body instanceof FormData) {
    init.body = body;
  } else if (body !== undefined) {
    init.body = JSON.stringify(body);
    (init.headers as Record<string, string>)["Content-Type"] = "application/json";
  }
  const res = await fetch(path, init);
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`;
    try {
      const j = await res.json();
      detail = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail);
    } catch {
      /* keep status text */
    }
    throw new ApiError(res.status, detail);
  }
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

export const api = {
  get: <T,>(p: string) => request<T>("GET", p),
  post: <T,>(p: string, b?: unknown) => request<T>("POST", p, b ?? {}),
  put: <T,>(p: string, b?: unknown) => request<T>("PUT", p, b ?? {}),
  del: <T,>(p: string) => request<T>("DELETE", p),
  upload: <T,>(p: string, f: File) => {
    const fd = new FormData();
    fd.append("file", f);
    return request<T>("POST", p, fd);
  },
};

export type Session = {
  workspace: string;
  is_demo: number;
  expires_at: string;
  people: { id: number; role: string; name: string; timezone: string; linked: number }[];
  telegram: { configured: boolean; linked: boolean; demo_channel: boolean; link_url: string | null };
  models: { fast: string; main: string; deep: string; vision: string; configured: boolean };
  demo_mode: boolean;
  ack_window_minutes: number;
  vision_fallback: boolean;
};

export type Ack = {
  id: number;
  due_at: string;
  status: string;
  window_minutes: number;
  medication_id: number;
  name_as_written: string;
  strength: string | null;
  dose: string | null;
  instructions: string | null;
};

export type Notification = {
  id: number;
  reason: string;
  body: string;
  api_status: string;
  error: string | null;
  sent_at: string;
  seen_at: string | null;
  status_text: string;
  resend_of: number | null;
};

export type TimelineEvent = { id: number; at: string; kind: string; summary: string };

export type Evidence = {
  id: number;
  outcome: "mention_found" | "no_mention" | "no_label" | "lookup_failed";
  outcome_text: string;
  section: string | null;
  passage: string | null;
  explanation: string | null;
  clinician_question: string | null;
  other_name: string | null;
  url: string | null;
  retrieved_at: string;
};

export type Medication = {
  id: number;
  name_as_written: string;
  ingredient: string | null;
  ingredient_source: string | null;
  strength: string | null;
  dose: string | null;
  frequency: string | null;
  instructions: string | null;
  status: "active" | "stopped";
  ask_doctor: number;
  times: string[];
  evidence: Evidence[];
  confirmed_by: { at: string; action: string; name: string } | null;
};

export type Item = {
  id: number;
  field: string;
  value: string | null;
  caregiver_value: string | null;
  uncertain: number;
  uncertain_reason: string | null;
  requires_confirm: number;
  source_line_ids: number[];
  suggestion: Normalization | null;
};

export type Normalization = {
  query: string;
  status: "exact" | "approximate" | "not_found" | "lookup_failed";
  rxcui: string | null;
  ingredient: string | null;
  candidate_name: string | null;
};

export type Group = {
  kind: "medication" | "allergy" | "follow_up" | "instruction" | "possible_missed";
  index: number;
  items: Item[];
  suggested_times?: string[];
  normalization?: Normalization | null;
};

export type Line = { id: number; page: number; line_no: number; text: string; bbox: number[]; source: string };

export type ExtractionView = {
  extraction: { id: number; document_id: number; status: string; created_at: string };
  document: { id: number; original_name: string; page_count: number; has_text_layer: number };
  lines: Line[];
  groups: Group[];
  model_run: { id: number; model_id: string; prompt_tokens: number; completion_tokens: number; latency_ms: number; cost_usd: number } | null;
};

export type Finding = {
  id: number;
  kind: "new" | "changed" | "explicitly_stopped" | "possible_duplicate" | "not_mentioned" | "unchanged";
  note: string | null;
  decision: string | null;
  choices: string[];
  detail: { proposed?: Record<string, any>; current?: Record<string, any>; fields?: string[] };
  sources: { id: number; page: number; text: string }[];
  created_at: string;
};

export type Cited = { text: string; cites: string[] };

export function fmtTime(iso: string) {
  return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

export function fmtDateTime(iso: string) {
  return new Date(iso).toLocaleString([], { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
}
