import { useEffect, useState } from "react";
import { api, Cited, fmtDateTime, withToken } from "../api";
import { CitedText } from "./Ask";

type BriefT = {
  id: number;
  created_at: string;
  summary: Cited[];
  questions: Cited[];
  dropped_uncited: number;
  records: Record<string, any>;
  pdf: string;
};

export default function Brief() {
  const [b, setB] = useState<BriefT | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const load = () => api.get<BriefT | null>("/api/brief/latest").then(setB).catch((e) => setErr(e.message));
  useEffect(() => {
    load();
  }, []);

  return (
    <div className="stack-l" style={{ maxWidth: 820 }}>
      <div className="page-head spread">
        <div>
          <h1>Visit brief</h1>
          <p className="muted">
            A one-page summary for the next doctor visit. The medicine table and acknowledgement counts come straight
            from the records; Nemotron writes the summary and questions, each citing its records.
          </p>
        </div>
        <button
          className="btn"
          disabled={busy}
          onClick={async () => {
            setBusy(true);
            setErr("");
            try {
              await api.post("/api/brief");
              await load();
            } catch (e) {
              setErr((e as Error).message);
            } finally {
              setBusy(false);
            }
          }}
        >
          {busy ? "Preparing…" : b ? "Prepare a new brief" : "Prepare the brief"}
        </button>
      </div>
      {err && <div className="error">{err}</div>}
      {!b ? (
        <p className="muted">No brief yet.</p>
      ) : (
        <article className="panel stack-l">
          <div className="spread">
            <span className="tiny">Prepared {fmtDateTime(b.created_at)}</span>
            <a className="btn small quiet" href={withToken(b.pdf)} target="_blank" rel="noreferrer" style={{ textDecoration: "none" }}>
              Open the PDF
            </a>
          </div>
          <section className="stack">
            <h2>Summary</h2>
            <CitedText items={b.summary} records={b.records} />
          </section>
          <section className="stack">
            <h2>Questions for the clinician</h2>
            <ol style={{ margin: 0, paddingLeft: "1.25rem" }}>
              {b.questions.map((q, i) => (
                <li key={i} style={{ marginBottom: "0.4rem" }}>
                  <CitedText items={[q]} records={b.records} />
                </li>
              ))}
            </ol>
          </section>
          {b.dropped_uncited > 0 && <p className="tiny">{b.dropped_uncited} uncited statements were removed.</p>}
        </article>
      )}
    </div>
  );
}
