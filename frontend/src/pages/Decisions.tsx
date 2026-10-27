import { useEffect, useState } from "react";
import { api, Finding } from "../api";
import type { Nav } from "../App";

const KIND: Record<Finding["kind"], [string, string]> = {
  new: ["New medicine", "wait"],
  changed: ["Changed", "wait"],
  explicitly_stopped: ["Document says stop", "alert"],
  possible_duplicate: ["Possible duplicate", "alert"],
  not_mentioned: ["Not mentioned", "wait"],
  unchanged: ["No change", "ok"],
};

const CHOICE: Record<string, string> = {
  accept: "Apply",
  reject: "Don't add",
  keep_current: "Keep current",
  stop: "Stop it",
  keep: "Keep taking it",
  acknowledge: "Noted",
  replace: "Replace the old one",
  keep_both: "Keep both",
  reject_new: "Don't add the new one",
  ask_doctor: "Keep, and ask the doctor",
  no_change: "No change",
};

// the first choice is shown as the primary button: what the document asks for, or the least drastic option
const ORDER: Record<string, string[]> = {
  new: ["accept", "reject"],
  changed: ["accept", "keep_current"],
  explicitly_stopped: ["stop", "keep", "acknowledge"],
  possible_duplicate: ["keep_both", "replace", "reject_new"],
  not_mentioned: ["ask_doctor", "keep", "stop"],
};

function Line({ label, a, b }: { label: string; a?: string | null; b?: string | null }) {
  if (!a && !b) return null;
  const differs = (a ?? "").toLowerCase() !== (b ?? "").toLowerCase();
  return (
    <>
      <span className="h">{label}</span>
      <span>{a ?? "—"}</span>
      <span style={{ fontWeight: differs && a ? 700 : 400 }}>{b ?? "—"}</span>
    </>
  );
}

export default function Decisions({ go, onChange }: { go: Nav; onChange: () => void }) {
  const [list, setList] = useState<Finding[] | null>(null);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState<number | null>(null);
  const load = () => api.get<Finding[]>("/api/findings").then(setList).catch((e) => setErr(e.message));
  useEffect(() => {
    load();
  }, []);

  if (!list) return <p className="muted">{err || "Loading…"}</p>;
  const open = list.filter((f) => !f.decision);
  const decided = list.filter((f) => f.decision && f.kind !== "unchanged");

  async function decide(f: Finding, choice: string) {
    setBusy(f.id);
    setErr("");
    try {
      await api.post(`/api/findings/${f.id}/decide`, { decision: choice });
      await load();
      onChange();
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="stack-l">
      <div className="page-head">
        <h1>Decisions</h1>
        <p className="muted">
          The confirmed document compared with the current medicine list. Each change waits for you. A medicine the
          document doesn't mention stays on the list until you decide.
        </p>
      </div>
      {err && <div className="error">{err}</div>}
      {open.length === 0 ? (
        <div className="panel stack">
          <p>No decisions waiting.</p>
          <div className="row">
            <button className="btn quiet small" onClick={() => go("/medicines")}>
              See the medicine list
            </button>
          </div>
        </div>
      ) : (
        <div className="stack">
          {open.map((f) => {
            const p = f.detail.proposed ?? {};
            const c = f.detail.current ?? {};
            const [label, cls] = KIND[f.kind];
            return (
              <article key={f.id} className="panel finding">
                <div className="stack">
                  <span className={`tag ${cls}`}>{label}</span>
                </div>
                <div className="stack">
                  <h3>{f.note}</h3>
                  {(f.detail.proposed || f.detail.current) && (
                    <div className="compare">
                      <span />
                      <span className="h">On the list now</span>
                      <span className="h">In the document</span>
                      <Line label="Medicine" a={c.name_as_written} b={p.name} />
                      <Line label="Strength" a={c.strength} b={p.strength} />
                      <Line label="Dose" a={c.dose} b={p.dose} />
                      <Line label="How often" a={c.frequency} b={p.frequency} />
                      {p.times && p.action !== "stop" && <Line label="Reminders" a={null} b={(p.times as string[]).join(", ") || "none"} />}
                    </div>
                  )}
                  {f.sources.map((s) => (
                    <div key={s.id} className="quote">
                      Page {s.page}: {s.text}
                    </div>
                  ))}
                  <div className="row">
                    {[...f.choices]
                      .sort((a, b) => (ORDER[f.kind] ?? []).indexOf(a) - (ORDER[f.kind] ?? []).indexOf(b))
                      .map((ch, i) => (
                        <button
                          key={ch}
                          className={`btn small ${i === 0 ? "" : "quiet"}`}
                          disabled={busy === f.id}
                          onClick={() => decide(f, ch)}
                        >
                          {CHOICE[ch] ?? ch}
                        </button>
                      ))}
                  </div>
                </div>
              </article>
            );
          })}
        </div>
      )}

      {decided.length > 0 && (
        <section className="panel">
          <h2>Decided</h2>
          <ul className="list" style={{ marginTop: "0.75rem" }}>
            {decided.slice(0, 20).map((f) => (
              <li key={f.id} className="spread">
                <span className="small">{f.note}</span>
                <span className="tag ok">{CHOICE[f.decision!] ?? f.decision}</span>
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}
