import { useState } from "react";
import { api, Cited } from "../api";

type Answer = { answer: Cited[]; dropped_uncited: number; records: Record<string, any> };
type Turn = { q: string; a?: Answer; err?: string };

const SUGGESTIONS = [
  "What changed in the plan this week?",
  "Which medicines should I ask the doctor about?",
  "Has my parent asked for help or not answered any reminders?",
  "What allergies are on record?",
];

export function recordLabel(r: any): string {
  if (!r) return "";
  return r.summary ?? r.name ?? r.medicine ?? r.type;
}

export function CitedText({ items, records }: { items: Cited[]; records: Record<string, any> }) {
  return (
    <div className="answer">
      {items.map((s, i) => (
        <p key={i}>
          {s.text}
          {s.cites.map((c) => (
            <span key={c} className="cite" title={recordLabel(records[c])}>
              {c}
            </span>
          ))}
        </p>
      ))}
    </div>
  );
}

export default function Ask() {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState(false);

  async function send(question: string) {
    if (!question.trim()) return;
    setBusy(true);
    setQ("");
    setTurns((t) => [...t, { q: question }]);
    try {
      const a = await api.post<Answer>("/api/ask", { question });
      setTurns((t) => t.map((x, i) => (i === t.length - 1 ? { ...x, a } : x)));
    } catch (e) {
      setTurns((t) => t.map((x, i) => (i === t.length - 1 ? { ...x, err: (e as Error).message } : x)));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="stack-l" style={{ maxWidth: 760 }}>
      <div className="page-head">
        <h1>Ask KinShield</h1>
        <p className="muted">
          Answers come only from what you've confirmed: medicines, decisions, notes, alerts and label passages. Every
          sentence shows the record it rests on; sentences without a valid record are removed before you see them.
        </p>
      </div>
      {turns.length === 0 && (
        <div className="row">
          {SUGGESTIONS.map((s) => (
            <button key={s} className="btn small quiet" onClick={() => send(s)} disabled={busy}>
              {s}
            </button>
          ))}
        </div>
      )}
      {turns.map((t, i) => (
        <div key={i} className="stack">
          <p>
            <strong>You:</strong> {t.q}
          </p>
          <div className="panel">
            {t.err ? (
              <div className="error">{t.err}</div>
            ) : t.a ? (
              <>
                <CitedText items={t.a.answer} records={t.a.records} />
                {t.a.dropped_uncited > 0 && (
                  <p className="tiny" style={{ marginTop: "0.5rem" }}>
                    {t.a.dropped_uncited} sentence{t.a.dropped_uncited > 1 ? "s" : ""} removed because {t.a.dropped_uncited > 1 ? "they" : "it"} cited no valid record.
                  </p>
                )}
              </>
            ) : (
              <p className="muted">Looking through the records…</p>
            )}
          </div>
        </div>
      ))}
      <form
        className="row"
        onSubmit={(e) => {
          e.preventDefault();
          send(q);
        }}
      >
        <input type="text" aria-label="Your question" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Ask about the care plan" style={{ flex: 1, minWidth: 220 }} />
        <button className="btn" disabled={busy || !q.trim()}>
          Ask
        </button>
      </form>
      <p className="tiny">KinShield doesn't give medical advice. For decisions about medicines, ask the doctor or pharmacist.</p>
    </div>
  );
}
