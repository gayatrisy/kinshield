import { useEffect, useState } from "react";
import { api, fmtDateTime } from "../api";
import type { Nav } from "../App";

type Doc = {
  id: number;
  original_name: string;
  kind: string;
  uploaded_at: string;
  page_count: number;
  has_text_layer: number;
  status: string;
  extraction: { id: number; status: string } | null;
};

const STEPS = ["Reading the page on this server", "Removing names and ID numbers", "Asking Nemotron to list the medicines"];

export default function Documents({ go }: { go: Nav }) {
  const [docs, setDocs] = useState<Doc[]>([]);
  const [busy, setBusy] = useState(false);
  const [step, setStep] = useState(0);
  const [err, setErr] = useState("");

  const load = () => api.get<Doc[]>("/api/documents").then(setDocs).catch((e) => setErr(e.message));
  useEffect(() => {
    load();
  }, []);

  async function process(fn: () => Promise<{ extraction_id: number }>) {
    setBusy(true);
    setErr("");
    setStep(0);
    const t = window.setInterval(() => setStep((s) => Math.min(s + 1, STEPS.length - 1)), 1800);
    try {
      const r = await fn();
      go(`/review/${r.extraction_id}`);
    } catch (e) {
      setErr((e as Error).message);
      load();
    } finally {
      window.clearInterval(t);
      setBusy(false);
    }
  }

  return (
    <div className="stack-l">
      <div className="page-head">
        <h1>Documents</h1>
        <p className="muted">
          Upload a discharge summary or prescription as a PDF or a phone photo. The page is read on this server and
          names, phone numbers, addresses and ID numbers are removed before any text goes to the model.
        </p>
      </div>

      <div className="grid-2">
        <section className="panel stack">
          <h2>Upload</h2>
          <label className="field">
            <span>PDF, JPG, PNG or WEBP, up to 15 MB</span>
            <input
              type="file"
              accept=".pdf,.png,.jpg,.jpeg,.webp"
              disabled={busy}
              onChange={(e) => {
                const f = e.target.files?.[0];
                if (f) process(() => api.upload("/api/documents", f));
                e.target.value = "";
              }}
            />
          </label>
          <p className="tiny">Please use synthetic or already-redacted documents in the public demo.</p>
        </section>
        <section className="panel stack">
          <h2>Use the sample discharge summary</h2>
          <p className="muted small">Ramesh's invented cardiology discharge, with seven medicine instructions.</p>
          <div className="row">
            <button className="btn" disabled={busy} onClick={() => process(() => api.post("/api/documents/demo", { variant: "pdf" }))}>
              Read the PDF
            </button>
            <button
              className="btn quiet"
              disabled={busy}
              onClick={() => process(() => api.post("/api/documents/demo", { variant: "photo" }))}
            >
              Read a phone photo of it
            </button>
          </div>
        </section>
      </div>

      {busy && (
        <div className="panel" role="status" aria-live="polite">
          <ol className="list">
            {STEPS.map((s, i) => (
              <li key={s} className={i <= step ? "" : "muted"}>
                {i < step ? "Done: " : i === step ? "Now: " : ""}
                {s}
              </li>
            ))}
          </ol>
        </div>
      )}
      {err && <div className="error">{err}</div>}

      <section className="panel">
        <h2>Uploaded</h2>
        {docs.length === 0 ? (
          <p className="muted" style={{ marginTop: "0.5rem" }}>
            Nothing uploaded yet.
          </p>
        ) : (
          <div className="scroll-x" style={{ marginTop: "0.75rem" }}>
            <table className="data">
              <thead>
                <tr>
                  <th>Document</th>
                  <th>Uploaded</th>
                  <th>Read with</th>
                  <th>Status</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {docs.map((d) => (
                  <tr key={d.id}>
                    <td>{d.original_name}</td>
                    <td>{fmtDateTime(d.uploaded_at)}</td>
                    <td>{d.has_text_layer ? "PDF text" : "Local OCR"}</td>
                    <td>
                      {d.extraction?.status === "pending" ? (
                        <span className="tag wait">Waiting for your check</span>
                      ) : d.extraction?.status === "confirmed" ? (
                        <span className="tag ok">Checked</span>
                      ) : d.status === "failed" ? (
                        <span className="tag alert">Extraction failed</span>
                      ) : (
                        <span className="tag">{d.extraction?.status ?? d.status}</span>
                      )}
                    </td>
                    <td>
                      {d.extraction?.status === "pending" && (
                        <button className="btn small" onClick={() => go(`/review/${d.extraction!.id}`)}>
                          Check it
                        </button>
                      )}
                      {d.status === "failed" && (
                        <button
                          className="btn small quiet"
                          onClick={() => process(() => api.post(`/api/documents/${d.id}/extract`))}
                        >
                          Try again
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
