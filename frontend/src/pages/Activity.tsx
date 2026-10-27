import { Fragment, useEffect, useState } from "react";
import { api, fmtDateTime, Notification } from "../api";
import { notifTag } from "./Today";

type Run = {
  id: number;
  task: string;
  model_id: string;
  redacted_prompt: string;
  response_text: string | null;
  prompt_tokens: number;
  completion_tokens: number;
  latency_ms: number;
  cost_usd: number;
  ok: number;
  error: string | null;
  created_at: string;
};
type Act = {
  model_runs: Run[];
  totals: { calls: number; prompt_tokens: number; completion_tokens: number; cost_usd: number };
  by_model: { model_id: string; calls: number; cost_usd: number; avg_ms: number }[];
  confirmations: { id: number; target_table: string; target_id: number; action: string; at: string; actor: string }[];
  notifications: Notification[];
};

const TASK: Record<string, string> = {
  extract_document: "List medicines from a document",
  "extract_document:retry": "List medicines (retry)",
  reconciliation_notes: "Explain plan differences",
  label_evidence: "Explain a label passage",
  ask: "Answer a question",
  visit_brief: "Write the visit brief",
  vision_transcribe: "Read a page image (opt-in)",
};

const TABLE: Record<string, string> = {
  extracted_items: "a field read from a document",
  reconciliation_findings: "a change to the plan",
  medications: "a medicine",
  schedules: "reminder times",
  extractions: "a document reading",
};

export default function Activity() {
  const [a, setA] = useState<Act | null>(null);
  const [open, setOpen] = useState<number | null>(null);
  const [err, setErr] = useState("");
  useEffect(() => {
    api.get<Act>("/api/activity").then(setA).catch((e) => setErr(e.message));
  }, []);
  if (!a) return <p className="muted">{err || "Loading…"}</p>;

  return (
    <div className="stack-l">
      <div className="page-head">
        <h1>Activity log</h1>
        <p className="muted">
          Every model call, with the exact text that left this server, and every decision with who made it.
        </p>
      </div>

      <div className="grid-2">
        <section className="panel">
          <h2>Model calls on Nebius Token Factory</h2>
          <p className="small muted" style={{ margin: "0.35rem 0 0.75rem" }}>
            {a.totals.calls} calls · {(a.totals.prompt_tokens + a.totals.completion_tokens).toLocaleString()} tokens · $
            {a.totals.cost_usd.toFixed(4)} at list price
          </p>
          <table className="data">
            <thead>
              <tr>
                <th>NVIDIA model</th>
                <th className="num">Calls</th>
                <th className="num">Avg time</th>
                <th className="num">Cost</th>
              </tr>
            </thead>
            <tbody>
              {a.by_model.map((m) => (
                <tr key={m.model_id}>
                  <td>{m.model_id.replace("nvidia/", "")}</td>
                  <td className="num">{m.calls}</td>
                  <td className="num">{(m.avg_ms / 1000).toFixed(1)} s</td>
                  <td className="num">${m.cost_usd.toFixed(4)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
        <section className="panel">
          <h2>Alerts</h2>
          {a.notifications.length === 0 ? (
            <p className="muted" style={{ marginTop: "0.5rem" }}>
              None yet.
            </p>
          ) : (
            <ul className="list" style={{ marginTop: "0.75rem" }}>
              {a.notifications.map((n) => (
                <li key={n.id} className="stack">
                  <div className="spread">
                    <span className="small">
                      {n.reason} · {fmtDateTime(n.sent_at)}
                    </span>
                    {notifTag(n)}
                  </div>
                  {n.error && <span className="tiny">{n.error}</span>}
                </li>
              ))}
            </ul>
          )}
          <p className="tiny" style={{ marginTop: "0.75rem" }}>
            “Sent to Telegram” means Telegram accepted the message. It counts as seen only when the caregiver taps Seen.
          </p>
        </section>
      </div>

      <section className="panel">
        <h2>Calls</h2>
        <div className="scroll-x" style={{ marginTop: "0.75rem" }}>
          <table className="data">
            <thead>
              <tr>
                <th>When</th>
                <th>Task</th>
                <th>Model</th>
                <th className="num">Tokens in / out</th>
                <th className="num">Time</th>
                <th className="num">Cost</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {a.model_runs.map((r) => (
                <Fragment key={r.id}>
                  <tr>
                    <td>{fmtDateTime(r.created_at)}</td>
                    <td>
                      {TASK[r.task] ?? r.task}
                      {!r.ok && <span className="tag alert" style={{ marginLeft: "0.4rem" }}>failed</span>}
                    </td>
                    <td>{r.model_id.replace("nvidia/", "")}</td>
                    <td className="num">
                      {r.prompt_tokens} / {r.completion_tokens}
                    </td>
                    <td className="num">{(r.latency_ms / 1000).toFixed(1)} s</td>
                    <td className="num">${r.cost_usd.toFixed(5)}</td>
                    <td>
                      <button className="btn small quiet" aria-expanded={open === r.id} onClick={() => setOpen(open === r.id ? null : r.id)}>
                        {open === r.id ? "Hide" : "What was sent"}
                      </button>
                    </td>
                  </tr>
                  {open === r.id && (
                    <tr>
                      <td colSpan={7}>
                        <strong className="small">Sent (after redaction)</strong>
                        <pre className="prompt">{r.redacted_prompt}</pre>
                        {r.error ? (
                          <div className="error" style={{ marginTop: "0.5rem" }}>{r.error}</div>
                        ) : (
                          <>
                            <strong className="small">Returned</strong>
                            <pre className="prompt">{r.response_text}</pre>
                          </>
                        )}
                      </td>
                    </tr>
                  )}
                </Fragment>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section className="panel">
        <h2>Decisions and confirmations</h2>
        <div className="scroll-x" style={{ marginTop: "0.75rem" }}>
          <table className="data">
            <thead>
              <tr>
                <th>When</th>
                <th>Who</th>
                <th>Did</th>
                <th>On</th>
              </tr>
            </thead>
            <tbody>
              {a.confirmations.map((c) => (
                <tr key={c.id}>
                  <td>{fmtDateTime(c.at)}</td>
                  <td>{c.actor}</td>
                  <td>{c.action.replace(/_/g, " ")}</td>
                  <td>
                    {TABLE[c.target_table] ?? c.target_table} #{c.target_id}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}
