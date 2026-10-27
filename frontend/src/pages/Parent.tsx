import { useCallback, useEffect, useState } from "react";
import { Ack, api, fmtTime } from "../api";
import type { Nav } from "../App";

export default function Parent({ go }: { go: Nav }) {
  const [data, setData] = useState<{ name: string; items: Ack[] } | null>(null);
  const [said, setSaid] = useState("");
  const load = useCallback(() => api.get<{ name: string; items: Ack[] }>("/api/parent/today").then(setData), []);
  useEffect(() => {
    load();
    const t = window.setInterval(load, 10000);
    return () => window.clearInterval(t);
  }, [load]);

  if (!data) return <div className="parent big">Loading…</div>;
  const now = Date.now();
  const due = data.items.filter((a) => a.status === "pending" && new Date(a.due_at).getTime() <= now);
  const later = data.items.filter((a) => a.status === "pending" && new Date(a.due_at).getTime() > now);
  const doneToday = data.items.filter((a) => a.status !== "pending");
  const first = data.name.split(" ")[0];

  async function answer(a: Ack, status: string, words: string) {
    await api.post(`/api/acks/${a.id}`, { status });
    setSaid(words);
    load();
  }

  return (
    <div className="parent">
      <div className="spread">
        <h1>Good {new Date().getHours() < 12 ? "morning" : new Date().getHours() < 17 ? "afternoon" : "evening"}, {first}</h1>
        <button className="btn quiet small" onClick={() => go("/today")}>
          Back to caregiver view
        </button>
      </div>
      {said && (
        <p className="big" role="status" style={{ marginTop: "1rem" }}>
          {said}
        </p>
      )}

      {due.length === 0 ? (
        <p className="big" style={{ marginTop: "1.5rem" }}>
          Nothing to take right now.
          {later[0] ? ` Next: ${later[0].name_as_written} at ${fmtTime(later[0].due_at)}.` : ""}
        </p>
      ) : (
        due.map((a) => (
          <section key={a.id} className="pill-card now" aria-label={`Medicine due at ${fmtTime(a.due_at)}`}>
            <div className="when">{fmtTime(a.due_at)}</div>
            <div className="what">{a.name_as_written}</div>
            <div className="big muted">{[a.dose, a.instructions].filter(Boolean).join(", ")}</div>
            <div className="parent-actions">
              <button className="pbtn took" onClick={() => answer(a, "acknowledged", "Thank you. Noted that you took it.")}>
                I took it
              </button>
              <button className="pbtn" onClick={() => answer(a, "skipped", "Noted that you skipped it.")}>
                Skip
              </button>
              <button className="pbtn help" onClick={() => answer(a, "help", `Your family has been told you need help.`)}>
                I need help
              </button>
            </div>
          </section>
        ))
      )}

      {later.length > 0 && (
        <section style={{ marginTop: "2rem" }}>
          <h2>Later today</h2>
          {later.map((a) => (
            <div key={a.id} className="pill-card big">
              <strong>{fmtTime(a.due_at)}</strong> · {a.name_as_written}
            </div>
          ))}
        </section>
      )}
      {doneToday.length > 0 && (
        <p className="muted big" style={{ marginTop: "2rem" }}>
          Answered today: {doneToday.length}
        </p>
      )}
    </div>
  );
}
