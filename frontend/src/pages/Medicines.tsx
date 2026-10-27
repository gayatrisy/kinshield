import { useEffect, useState } from "react";
import { api, fmtDateTime, Medication, Session } from "../api";
import type { Nav } from "../App";

function EvidenceBlock({ m }: { m: Medication }) {
  if (!m.evidence.length) return null;
  const found = m.evidence.filter((e) => e.outcome === "mention_found");
  const rest = m.evidence.filter((e) => e.outcome !== "mention_found");
  return (
    <div className="evidence stack">
      {found.map((e) => (
        <div key={e.id} className="stack">
          <span className="tag alert">{e.outcome_text}</span>
          <div className="quote">“{e.passage}”</div>
          {e.explanation && <p className="small">{e.explanation}</p>}
          {e.clinician_question && (
            <p className="small">
              <strong>Ask:</strong> {e.clinician_question}
            </p>
          )}
          <p className="tiny">
            openFDA label, section “{(e.section ?? "").replace(/_/g, " ")}”, retrieved {fmtDateTime(e.retrieved_at)}
            {e.url ? (
              <>
                {" · "}
                <a href={e.url} target="_blank" rel="noreferrer">
                  full label
                </a>
              </>
            ) : null}
          </p>
        </div>
      ))}
      {rest.map((e) => (
        <p key={e.id} className="small">
          <span className="tag">{e.outcome_text}</span>
        </p>
      ))}
      <p className="tiny">A label check is not a safety check. US labels may not cover medicines sold elsewhere.</p>
    </div>
  );
}

export default function Medicines({ go, session }: { go: Nav; session: Session }) {
  const [meds, setMeds] = useState<Medication[] | null>(null);
  const [msg, setMsg] = useState("");
  const [err, setErr] = useState("");
  const load = () => api.get<Medication[]>("/api/medications").then(setMeds).catch((e) => setErr(e.message));
  useEffect(() => {
    load();
  }, []);
  if (!meds) return <p className="muted">{err || "Loading…"}</p>;
  const active = meds.filter((m) => m.status === "active");
  const stopped = meds.filter((m) => m.status === "stopped");

  return (
    <div className="stack-l">
      <div className="page-head">
        <h1>Medicines</h1>
        <p className="muted">
          The confirmed list. New medicines have their US drug label (openFDA) searched for the names of the other
          medicines on this list.
        </p>
      </div>
      {msg && <div className="note">{msg}</div>}
      {err && <div className="error">{err}</div>}
      <section className="panel">
        <ul className="list">
          {active.map((m) => (
            <li key={m.id} className="medline">
              <div>
                <h3>
                  {m.name_as_written}
                  {m.ask_doctor ? <span className="tag wait" style={{ marginLeft: "0.5rem" }}>Ask the doctor</span> : null}
                </h3>
                <p className="small muted">
                  {[m.strength, m.dose, m.frequency, m.instructions].filter(Boolean).join(" · ")}
                  {m.ingredient ? ` · generic: ${m.ingredient}` : ""}
                </p>
                <p className="tiny">
                  Reminders {m.times.length ? m.times.join(", ") : "none (as needed)"}
                  {m.confirmed_by ? ` · confirmed by ${m.confirmed_by.name}, ${fmtDateTime(m.confirmed_by.at)}` : ""}
                </p>
                <EvidenceBlock m={m} />
              </div>
              <div className="row" style={{ alignItems: "start" }}>
                <button
                  className="btn small quiet"
                  onClick={async () => {
                    setMsg("Checking the label…");
                    try {
                      await api.post(`/api/medications/${m.id}/review`);
                      setMsg("");
                      load();
                    } catch (e) {
                      setMsg("");
                      setErr((e as Error).message);
                    }
                  }}
                >
                  Check label
                </button>
                {session.demo_mode && m.times.length > 0 && (
                  <button
                    className="btn small quiet"
                    title={`Creates a reminder due now. With no answer in ${session.ack_window_minutes} minutes, you get an alert.`}
                    onClick={async () => {
                      await api.post("/api/demo/due-now", { medication_id: m.id });
                      setMsg(
                        `A reminder for ${m.name_as_written} is due now on the parent's screen. Leave it unanswered for ${session.ack_window_minutes} minutes to see the alert.`,
                      );
                    }}
                  >
                    Make a reminder due now
                  </button>
                )}
              </div>
            </li>
          ))}
        </ul>
        {active.length === 0 && (
          <p className="muted">
            No medicines yet.{" "}
            <button className="btn small quiet" onClick={() => go("/documents")}>
              Upload a prescription
            </button>
          </p>
        )}
      </section>
      {stopped.length > 0 && (
        <section className="panel">
          <h2>Stopped</h2>
          <ul className="list" style={{ marginTop: "0.75rem" }}>
            {stopped.map((m) => (
              <li key={m.id} className="small muted">
                {m.name_as_written}
                {m.confirmed_by ? ` · stopped by ${m.confirmed_by.name}` : ""}
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}
