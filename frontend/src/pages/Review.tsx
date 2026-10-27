import { useEffect, useMemo, useState } from "react";
import { api, ExtractionView, Group, Item } from "../api";
import type { Nav } from "../App";

const LABELS: Record<string, string> = {
  name: "Medicine as written",
  strength: "Strength",
  form: "Form",
  dose: "Dose each time",
  frequency: "How often",
  timing: "When",
  duration: "For how long",
  instructions: "Other instructions",
  action: "What the document says to do",
  substance: "Allergic to",
  reaction: "Reaction",
  what: "Follow-up",
  when: "When",
  text: "Instruction",
};
const MED_ORDER = ["name", "strength", "dose", "frequency", "timing", "form", "duration", "instructions"];
const KIND_TITLE: Record<string, string> = {
  allergy: "Allergy",
  follow_up: "Follow-up",
  instruction: "Other instruction",
};

type GroupState = { include: boolean; times: string; ingredient: string; rxcui: string | null; source: string | null };

export default function Review({ id, go, onDone }: { id: number; go: Nav; onDone: () => void }) {
  const [v, setV] = useState<ExtractionView | null>(null);
  const [vals, setVals] = useState<Record<number, string>>({});
  const [ok, setOk] = useState<Record<number, boolean>>({});
  const [groups, setGroups] = useState<Record<string, GroupState>>({});
  const [focus, setFocus] = useState<number[]>([]);
  const [page, setPage] = useState(1);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  const load = () =>
    api
      .get<ExtractionView>(`/api/extractions/${id}`)
      .then((x) => {
        setV(x);
        const vs: Record<number, string> = {};
        const gs: Record<string, GroupState> = {};
        x.groups.forEach((g) => {
          g.items.forEach((it) => (vs[it.id] = it.caregiver_value ?? ""));
          const n = g.normalization;
          gs[`${g.kind}:${g.index}`] = {
            include: true,
            times: (g.suggested_times ?? []).join(", "),
            ingredient: n && n.status === "exact" ? n.ingredient ?? "" : "",
            rxcui: n && n.status === "exact" ? n.rxcui : null,
            source: n && n.status === "exact" ? "rxnorm" : null,
          };
        });
        setVals(vs);
        setGroups(gs);
      })
      .catch((e) => setErr(e.message));
  useEffect(() => {
    load();
  }, [id]);

  const lineById = useMemo(() => new Map((v?.lines ?? []).map((l) => [l.id, l])), [v]);
  const meds = v?.groups.filter((g) => g.kind === "medication") ?? [];
  const others = v?.groups.filter((g) => g.kind !== "medication") ?? [];

  const needs = (it: Item) => !!(it.requires_confirm || it.uncertain);
  const outstanding = useMemo(() => {
    if (!v) return 0;
    let n = 0;
    v.groups.forEach((g) => {
      if (!groups[`${g.kind}:${g.index}`]?.include) return;
      g.items.forEach((it) => {
        if (needs(it) && !ok[it.id]) n++;
      });
    });
    return n;
  }, [v, ok, groups]);

  if (!v) return <p className="muted">{err || "Loading…"}</p>;
  const done = v.extraction.status !== "pending";

  const focusItem = (it: Item) => {
    setFocus(it.source_line_ids);
    const first = lineById.get(it.source_line_ids[0]);
    if (first) setPage(first.page);
  };

  const field = (g: Group, it: Item, wide = false) => {
    const must = needs(it);
    return (
      <div key={it.id} className={`fieldbox ${must ? "must" : ""} ${ok[it.id] ? "done" : ""} ${wide ? "wide" : ""}`}>
        <label className="field">
          <span>{LABELS[it.field] ?? it.field}</span>
          {it.field === "action" ? (
            <select
              value={vals[it.id] ?? ""}
              disabled={done}
              onFocus={() => focusItem(it)}
              onChange={(e) => setVals({ ...vals, [it.id]: e.target.value })}
            >
              <option value="">Choose…</option>
              <option value="start">Start this medicine</option>
              <option value="continue">Continue as before</option>
              <option value="change">Change dose or timing</option>
              <option value="stop">Stop this medicine</option>
            </select>
          ) : (
            <input
              type="text"
              value={vals[it.id] ?? ""}
              disabled={done}
              placeholder="Not stated"
              onFocus={() => focusItem(it)}
              onChange={(e) => {
                setVals({ ...vals, [it.id]: e.target.value });
                setOk({ ...ok, [it.id]: false });
              }}
            />
          )}
        </label>
        {it.uncertain ? <div className="why">Check this: {it.uncertain_reason}</div> : null}
        {must && !done && (
          <label className="chk">
            <input type="checkbox" checked={!!ok[it.id]} onChange={(e) => setOk({ ...ok, [it.id]: e.target.checked })} />
            Matches the document
          </label>
        )}
      </div>
    );
  };

  async function submit() {
    setBusy(true);
    setErr("");
    const items = v!.groups.flatMap((g) =>
      g.items.map((it) => ({ id: it.id, value: vals[it.id] ?? "", confirmed: !needs(it) || !!ok[it.id] })),
    );
    const gs = v!.groups.map((g) => {
      const s = groups[`${g.kind}:${g.index}`];
      return {
        kind: g.kind,
        index: g.index,
        include: s.include,
        times: g.kind === "medication" ? s.times.split(/[,\s]+/).filter(Boolean) : undefined,
        ingredient: s.ingredient || undefined,
        rxcui: s.rxcui || undefined,
        ingredient_source: s.source || (s.ingredient ? "caregiver" : undefined),
      };
    });
    try {
      await api.post(`/api/extractions/${id}/confirm`, { items, groups: gs });
      onDone();
      go("/decisions");
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const pageLines = v.lines.filter((l) => l.page === page);
  const groupLines = (g: Group) => Array.from(new Set(g.items.flatMap((i) => i.source_line_ids)));

  return (
    <div className="stack-l">
      <div className="page-head">
        <h1>Check what was read</h1>
        <p className="muted">
          {v.document.original_name} · read with {v.document.has_text_layer ? "the PDF's text" : "OCR on this server"}
          {v.model_run ? ` · listed by ${v.model_run.model_id.split("/").pop()} in ${(v.model_run.latency_ms / 1000).toFixed(1)} s` : ""}.
          Click a field to see the line it came from. Nothing changes in the care plan until you confirm.
        </p>
      </div>
      {done && <div className="note">This document has already been {v.extraction.status}.</div>}

      <div className="review">
        <div className="sheet-wrap">
          {v.document.page_count > 1 && (
            <div className="page-tabs">
              {Array.from({ length: v.document.page_count }, (_, i) => (
                <button key={i} className={`btn small ${page === i + 1 ? "" : "quiet"}`} onClick={() => setPage(i + 1)}>
                  Page {i + 1}
                </button>
              ))}
            </div>
          )}
          <div className="sheet">
            <img src={`/api/documents/${v.document.id}/pages/${page}`} alt={`Page ${page} of ${v.document.original_name}`} />
            {pageLines
              .filter((l) => focus.includes(l.id) && l.source !== "vision")
              .map((l) => (
                <div
                  key={l.id}
                  className="hl"
                  style={{
                    left: `${l.bbox[0] * 100 - 0.5}%`,
                    top: `${l.bbox[1] * 100 - 0.3}%`,
                    width: `${(l.bbox[2] - l.bbox[0]) * 100 + 1}%`,
                    height: `${(l.bbox[3] - l.bbox[1]) * 100 + 0.6}%`,
                  }}
                />
              ))}
          </div>
          {focus.length > 0 && (
            <div className="quote" style={{ marginTop: "0.5rem" }}>
              {focus.map((f) => lineById.get(f)?.text).filter(Boolean).join(" / ")}
            </div>
          )}
          {!v.document.has_text_layer && !done && (
            <p className="tiny" style={{ marginTop: "0.5rem" }}>
              OCR misread the page?{" "}
              <button
                className="btn small quiet"
                onClick={async () => {
                  if (!confirm("Send this page image to the vision model? Lines that look like names, numbers or addresses are blacked out first, but masking can miss things.")) return;
                  try {
                    const r = await api.post<{ extraction_id: number }>(`/api/documents/${v.document.id}/vision?page=${page}`);
                    go(`/review/${r.extraction_id}`);
                  } catch (e) {
                    setErr((e as Error).message);
                  }
                }}
              >
                Read it with the vision model
              </button>
            </p>
          )}
        </div>

        <div className="stack">
          {meds.map((g) => {
            const key = `${g.kind}:${g.index}`;
            const s = groups[key];
            const byField = Object.fromEntries(g.items.map((i) => [i.field, i]));
            const n = g.normalization;
            const name = vals[byField.name?.id] || "Medicine";
            const action = byField.action ? vals[byField.action.id] : "";
            return (
              <article key={key} className={`medcard ${s?.include ? "" : "off"}`} onMouseEnter={() => setFocus(groupLines(g))}>
                <header className="medcard-head">
                  <h3>{name}</h3>
                  {!done && (
                    <label className="chk small row" style={{ gap: "0.35rem" }}>
                      <input
                        type="checkbox"
                        checked={s?.include}
                        onChange={(e) => setGroups({ ...groups, [key]: { ...s, include: e.target.checked } })}
                      />
                      Include
                    </label>
                  )}
                </header>
                {s?.include && (
                  <div className="medcard-body">
                    {byField.action && field(g, byField.action, true)}
                    {MED_ORDER.filter((f) => byField[f]).map((f) => field(g, byField[f], f === "name" || f === "instructions"))}
                    {action !== "stop" && (
                      <label className="field wide">
                        <span>Reminder times (24-hour, comma separated)</span>
                        <input
                          type="text"
                          value={s.times}
                          disabled={done}
                          onChange={(e) => setGroups({ ...groups, [key]: { ...s, times: e.target.value } })}
                        />
                      </label>
                    )}
                    <div className="wide small muted">
                      {n?.status === "exact" && <>Matched in RxNorm as {n.ingredient}.</>}
                      {n?.status === "approximate" && s.source !== "rxnorm" && (
                        <span className="row">
                          RxNorm's closest match is “{n.candidate_name}”.
                          <button
                            className="btn small quiet"
                            onClick={() =>
                              setGroups({ ...groups, [key]: { ...s, ingredient: n.ingredient ?? "", rxcui: n.rxcui, source: "rxnorm" } })
                            }
                          >
                            Use it
                          </button>
                        </span>
                      )}
                      {n?.status === "approximate" && s.source === "rxnorm" && <>Using RxNorm match {s.ingredient}.</>}
                      {(!n || n.status === "not_found" || n.status === "lookup_failed") && (
                        <label className="field">
                          <span>
                            {n?.status === "lookup_failed" ? "RxNorm could not be reached." : "Not found in RxNorm."} Generic
                            name, if you know it (used to spot duplicates)
                          </span>
                          <input
                            type="text"
                            value={s.ingredient}
                            disabled={done}
                            onChange={(e) => setGroups({ ...groups, [key]: { ...s, ingredient: e.target.value, source: "caregiver", rxcui: null } })}
                          />
                        </label>
                      )}
                    </div>
                  </div>
                )}
              </article>
            );
          })}

          {others.length > 0 && (
            <div className="panel tight stack">
              <h3>Also on this document</h3>
              {others.map((g) => {
                const key = `${g.kind}:${g.index}`;
                const s = groups[key];
                return (
                  <div key={key} className={s?.include ? "" : "medcard off"} onMouseEnter={() => setFocus(groupLines(g))}>
                    <div className="spread">
                      <strong className="small">{KIND_TITLE[g.kind]}</strong>
                      {!done && (
                        <label className="chk small row" style={{ gap: "0.35rem" }}>
                          <input
                            type="checkbox"
                            checked={s?.include}
                            onChange={(e) => setGroups({ ...groups, [key]: { ...s, include: e.target.checked } })}
                          />
                          Include
                        </label>
                      )}
                    </div>
                    {s?.include && <div className="medcard-body" style={{ padding: "0.25rem 0 0" }}>{g.items.map((it) => field(g, it))}</div>}
                  </div>
                );
              })}
            </div>
          )}

          {!done && (
            <div className="sticky-foot stack">
              {err && <div className="error">{err}</div>}
              <div className="spread">
                <span className="small muted">
                  {outstanding ? `${outstanding} field${outstanding > 1 ? "s" : ""} still to check against the page` : "Everything checked."}
                </span>
                <div className="row">
                  <button
                    className="btn quiet"
                    onClick={async () => {
                      await api.post(`/api/extractions/${id}/reject`);
                      onDone();
                      go("/documents");
                    }}
                  >
                    Discard this reading
                  </button>
                  <button className="btn sage" disabled={busy || outstanding > 0} onClick={submit}>
                    Confirm and compare with the plan
                  </button>
                </div>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
