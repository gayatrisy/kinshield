import { useState } from "react";
import { api } from "../api";

export default function Start({ onReady }: { onReady: () => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [own, setOwn] = useState({ caregiver: "", parent: "" });

  async function run(fn: () => Promise<unknown>) {
    setBusy(true);
    setError("");
    try {
      await fn();
      window.location.hash = "/today";
      onReady();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="start">
      <section className="start-left">
        <div className="brand" style={{ padding: 0 }}>
          KinShield
        </div>
        <div className="stack-l">
          <h1>Your parent's care plan, kept straight between hospital visits.</h1>
          <p>
            A private assistant for the family member who manages an older parent's medicines. It reads discharge
            papers, remembers what you confirmed, reminds your parent, and tells you when a reminder goes unanswered.
          </p>
          <ol className="chain">
            <li>Upload a discharge summary or prescription</li>
            <li>Check every medicine against the original page</li>
            <li>Decide what changes in the plan</li>
            <li>Reminders run; you get a Telegram alert if one goes unanswered</li>
            <li>Take a cited brief to the next doctor visit</li>
          </ol>
        </div>
        <p className="small" style={{ color: "#9fb0bf" }}>
          KinShield organises what documents say. It does not give medical advice or judge whether medicines are safe.
        </p>
      </section>
      <section className="start-right">
        <div className="stack">
          <h2>Try it with a sample patient</h2>
          <p className="muted">
            You get a private workspace with Ramesh, 74, an invented patient just discharged after a heart attack.
            Nobody else can see what you do in it.
          </p>
          <button className="btn" disabled={busy} onClick={() => run(() => api.post("/api/session/demo"))}>
            Start the demo
          </button>
        </div>
        <details>
          <summary className="muted" style={{ cursor: "pointer" }}>
            Set up for your own family instead
          </summary>
          <form
            className="stack"
            style={{ marginTop: "1rem" }}
            onSubmit={(e) => {
              e.preventDefault();
              run(() => api.post("/api/session/owner", { ...own, timezone: Intl.DateTimeFormat().resolvedOptions().timeZone }));
            }}
          >
            <label className="field">
              <span>Your name</span>
              <input type="text" required value={own.caregiver} onChange={(e) => setOwn({ ...own, caregiver: e.target.value })} />
            </label>
            <label className="field">
              <span>Your parent's name (used only to remove it before anything is sent to a model)</span>
              <input type="text" required value={own.parent} onChange={(e) => setOwn({ ...own, parent: e.target.value })} />
            </label>
            <button className="btn quiet" disabled={busy}>
              Create my workspace
            </button>
          </form>
        </details>
        {error && <div className="error">{error}</div>}
      </section>
    </div>
  );
}
