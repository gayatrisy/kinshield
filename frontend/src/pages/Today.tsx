import { useCallback, useEffect, useState } from "react";
import { Ack, api, fmtDateTime, fmtTime, Notification, Session, TimelineEvent } from "../api";
import type { Nav } from "../App";

type Dash = {
  today: Ack[];
  pending_extractions: { id: number; original_name: string }[];
  pending_findings: number;
  needs_attention: Ack[];
  notifications: Notification[];
  events: TimelineEvent[];
  active_medication_count: number;
  label_mentions: { id: number; med: string; other: string }[];
};

export const ackTag = (s: string) => {
  const map: Record<string, [string, string]> = {
    pending: ["Waiting", ""],
    acknowledged: ["Said they took it", "ok"],
    skipped: ["Chose to skip", "wait"],
    help: ["Asked for help", "alert"],
    no_response: ["No response", "alert"],
  };
  const [t, c] = map[s] ?? [s, ""];
  return <span className={`tag ${c}`}>{t}</span>;
};

export const notifTag = (n: Notification) => (
  <span className={`tag ${n.api_status === "failed" ? "alert" : n.seen_at ? "ok" : "wait"}`}>{n.status_text}</span>
);

export default function Today({ go, session }: { go: Nav; session: Session }) {
  const [d, setD] = useState<Dash | null>(null);
  const [err, setErr] = useState("");
  const [symptom, setSymptom] = useState("");

  const load = useCallback(async () => {
    try {
      setD(await api.get<Dash>("/api/dashboard"));
      setErr("");
    } catch (e) {
      setErr((e as Error).message);
    }
  }, []);
  useEffect(() => {
    load();
    const t = window.setInterval(load, 15000);
    return () => window.clearInterval(t);
  }, [load]);

  const parent = session.people.find((p) => p.role === "parent")?.name.split(" ")[0] ?? "your parent";
  if (!d) return <p className="muted">{err || "Loading…"}</p>;

  const todo: JSX.Element[] = [];
  d.pending_extractions.forEach((x) =>
    todo.push(
      <li key={`e${x.id}`} className="spread">
        <span>Check what was read from {x.original_name}</span>
        <button className="btn small" onClick={() => go(`/review/${x.id}`)}>
          Check it
        </button>
      </li>,
    ),
  );
  if (d.pending_findings)
    todo.push(
      <li key="f" className="spread">
        <span>
          {d.pending_findings} change{d.pending_findings > 1 ? "s" : ""} to the plan waiting for your decision
        </span>
        <button className="btn small" onClick={() => go("/decisions")}>
          Decide
        </button>
      </li>,
    );
  d.label_mentions.forEach((m) =>
    todo.push(
      <li key={`l${m.id}`} className="spread">
        <span>
          The {m.med} label mentions {m.other}. Worth raising with the doctor.
        </span>
        <button className="btn small quiet" onClick={() => go("/medicines")}>
          See passage
        </button>
      </li>,
    ),
  );

  return (
    <div className="stack-l">
      <div className="page-head">
        <h1>Today</h1>
        <p className="muted">
          {parent}'s reminders, what needs you, and what KinShield did. Reminders are answered by {parent} on their own
          screen; answers are self-reported.
        </p>
      </div>

      {d.needs_attention.length > 0 && (
        <div className="panel" style={{ borderColor: "#eec3ba", background: "#fdf6f4" }}>
          <h2>Needs attention</h2>
          <ul className="list" style={{ marginTop: "0.75rem" }}>
            {d.needs_attention.map((a) => (
              <li key={a.id} className="dose-row">
                <span className="dose-time">{fmtTime(a.due_at)}</span>
                <span>
                  {a.name_as_written} {a.dose ? `· ${a.dose}` : ""}
                </span>
                {ackTag(a.status)}
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="grid-2">
        <section className="panel">
          <div className="panel-head">
            <h2>Reminders today</h2>
            <span className="tiny">{d.active_medication_count} active medicines</span>
          </div>
          {d.today.length === 0 ? (
            <p className="muted">No reminders scheduled today.</p>
          ) : (
            <ul className="list">
              {d.today.map((a) => (
                <li key={a.id} className="dose-row">
                  <span className="dose-time">{fmtTime(a.due_at)}</span>
                  <span>
                    {a.name_as_written}
                    {a.dose ? <span className="muted small"> · {a.dose}</span> : null}
                  </span>
                  {ackTag(a.status)}
                </li>
              ))}
            </ul>
          )}
        </section>

        <section className="stack">
          <div className="panel">
            <h2>Waiting for you</h2>
            {todo.length ? (
              <ul className="list" style={{ marginTop: "0.75rem" }}>
                {todo}
              </ul>
            ) : (
              <p className="muted" style={{ marginTop: "0.5rem" }}>
                Nothing to review. Upload a new prescription or discharge summary when you get one.
              </p>
            )}
            <div className="row" style={{ marginTop: "1rem" }}>
              <button className="btn quiet small" onClick={() => go("/documents")}>
                Upload a document
              </button>
            </div>
          </div>

          <div className="panel">
            <h2>Alerts to you</h2>
            {d.notifications.length === 0 ? (
              <p className="muted" style={{ marginTop: "0.5rem" }}>
                No alerts sent yet. KinShield messages you on Telegram when a reminder gets no response within{" "}
                {session.ack_window_minutes} minutes, or when {parent} asks for help.
              </p>
            ) : (
              <ul className="list" style={{ marginTop: "0.75rem" }}>
                {d.notifications.slice(0, 5).map((n) => (
                  <li key={n.id} className="spread">
                    <span>
                      {n.reason}
                      <span className="tiny"> · {fmtDateTime(n.sent_at)}</span>
                    </span>
                    {notifTag(n)}
                  </li>
                ))}
              </ul>
            )}
          </div>
        </section>
      </div>

      <div className="grid-2">
        <section className="panel">
          <h2>Note a symptom or change</h2>
          <p className="muted small" style={{ margin: "0.35rem 0 0.75rem" }}>
            Saved to memory and included in the visit brief.
          </p>
          <form
            className="stack"
            onSubmit={async (e) => {
              e.preventDefault();
              if (!symptom.trim()) return;
              await api.post("/api/symptoms", { text: symptom });
              setSymptom("");
              load();
            }}
          >
            <textarea
              aria-label="Symptom or change"
              placeholder="For example: felt dizzy after the morning tablets"
              value={symptom}
              onChange={(e) => setSymptom(e.target.value)}
            />
            <div>
              <button className="btn quiet">Save note</button>
            </div>
          </form>
        </section>
        <section className="panel">
          <h2>Recent activity</h2>
          <ul className="list" style={{ marginTop: "0.75rem" }}>
            {d.events.slice(0, 7).map((e) => (
              <li key={e.id}>
                <span className="small">{e.summary}</span>
                <div className="tiny">{fmtDateTime(e.at)}</div>
              </li>
            ))}
          </ul>
        </section>
      </div>
    </div>
  );
}
