import { useCallback, useEffect, useState } from "react";
import { api, ApiError, Session } from "./api";
import Start from "./pages/Start";
import Today from "./pages/Today";
import Documents from "./pages/Documents";
import Review from "./pages/Review";
import Decisions from "./pages/Decisions";
import Medicines from "./pages/Medicines";
import Ask from "./pages/Ask";
import Brief from "./pages/Brief";
import Activity from "./pages/Activity";
import Parent from "./pages/Parent";
import Settings from "./pages/Settings";
import More from "./pages/More";
import InstallCard from "./InstallCard";

export type Nav = (to: string) => void;

function useHash(): [string, Nav] {
  const read = () => window.location.hash.replace(/^#/, "") || "/today";
  const [hash, setHash] = useState(read);
  useEffect(() => {
    const on = () => setHash(read());
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  const go = useCallback((to: string) => {
    window.location.hash = to;
    window.scrollTo(0, 0);
  }, []);
  return [hash, go];
}

const Shield = () => (
  <svg width="26" height="26" viewBox="0 0 32 32" aria-hidden="true">
    <path d="M16 3 5 7v8c0 7 5 12 11 14 6-2 11-7 11-14V7z" fill="#fff" />
    <path d="M11 16h10M16 11v10" stroke="#1c2b39" strokeWidth="3" />
  </svg>
);

export default function App() {
  const [session, setSession] = useState<Session | null | undefined>(undefined);
  const [pending, setPending] = useState({ reviews: 0, decisions: 0 });
  const [hash, go] = useHash();

  const load = useCallback(async () => {
    try {
      setSession(await api.get<Session>("/api/session"));
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) setSession(null);
      else setSession(null);
    }
  }, []);
  useEffect(() => {
    load();
  }, [load]);

  const refreshCounts = useCallback(async () => {
    try {
      const d = await api.get<{ pending_extractions: unknown[]; pending_findings: number }>("/api/dashboard");
      setPending({ reviews: d.pending_extractions.length, decisions: d.pending_findings });
    } catch {
      /* ignore */
    }
  }, []);
  useEffect(() => {
    if (session) refreshCounts();
  }, [session, hash, refreshCounts]);

  if (session === undefined) return <main className="muted">Loading…</main>;
  if (session === null) return <Start onReady={load} />;

  const [path, arg] = hash.split("/").filter(Boolean);
  if (path === "parent") return <Parent go={go} />;

  const links: [string, string, number?][] = [
    ["today", "Today"],
    ["documents", "Documents", pending.reviews],
    ["decisions", "Decisions", pending.decisions],
    ["medicines", "Medicines"],
    ["ask", "Ask KinShield"],
    ["brief", "Visit brief"],
    ["activity", "Activity log"],
    ["parent", "Parent's screen"],
    ["settings", "Settings"],
  ];

  const I = (d: string) => (
    <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <path d={d} />
    </svg>
  );
  const tabs: [string, string, JSX.Element, number?][] = [
    ["today", "Today", I("M4 11.5 12 5l8 6.5V20a1 1 0 0 1-1 1h-5v-6h-4v6H5a1 1 0 0 1-1-1z")],
    ["documents", "Upload", I("M12 16V4m0 0-4.5 4.5M12 4l4.5 4.5M4 15v4a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-4"), pending.reviews],
    ["decisions", "Decide", I("M9 12.5l2 2 4-4.5M12 3l7 3v5c0 5-3 8.5-7 10-4-1.5-7-5-7-10V6z"), pending.decisions],
    ["medicines", "Medicines", I("M10.5 3.5a5 5 0 0 1 7 7l-7 7a5 5 0 0 1-7-7zM7 7l7 7")],
    ["more", "More", I("M5 12h.01M12 12h.01M19 12h.01")],
  ];
  const moreActive = ["more", "ask", "brief", "activity", "settings"].includes(path ?? "");

  let page;
  switch (path) {
    case "documents":
      page = <Documents go={go} />;
      break;
    case "review":
      page = <Review id={Number(arg)} go={go} onDone={refreshCounts} />;
      break;
    case "decisions":
      page = <Decisions go={go} onChange={refreshCounts} />;
      break;
    case "medicines":
      page = <Medicines go={go} session={session} />;
      break;
    case "ask":
      page = <Ask />;
      break;
    case "brief":
      page = <Brief />;
      break;
    case "activity":
      page = <Activity />;
      break;
    case "settings":
      page = <Settings session={session} onReset={() => setSession(null)} />;
      break;
    case "more":
      page = <More />;
      break;
    default:
      page = <Today go={go} session={session} />;
  }

  return (
    <>
      {session.is_demo ? (
        <div className="demo-banner">
          <span className="long">
            Demo workspace with a synthetic patient. Everything here is invented and private to this browser; it is
            deleted 7 days after your last visit.
          </span>
          <span className="short">Demo with an invented patient. Private to this browser.</span>
        </div>
      ) : null}
      <div className="shell">
        <aside className="side">
          <div className="brand">
            <Shield /> KinShield
          </div>
          <nav className="nav" aria-label="Main">
            {links.map(([to, label, n]) => (
              <a key={to} href={`#/${to}`} aria-current={path === to || (!path && to === "today") ? "page" : undefined}>
                {label}
                {n ? <span className="count">{n}</span> : null}
              </a>
            ))}
          </nav>
          <div className="side-foot">
            Runs on NVIDIA Nemotron via Nebius Token Factory. Not medical advice.
          </div>
        </aside>
        <main>
          {(!path || path === "today") && <InstallCard compact />}
          {page}
        </main>
      </div>
      <nav className="tabbar" aria-label="Main">
        {tabs.map(([to, label, icon, n]) => {
          const current = to === "more" ? moreActive : path === to || (!path && to === "today") || (to === "documents" && path === "review");
          return (
            <a key={to} href={`#/${to}`} aria-current={current ? "page" : undefined}>
              <span className="tab-icon" aria-hidden="true">
                {icon}
                {n ? <span className="tab-badge">{n}</span> : null}
              </span>
              <span>{label}</span>
            </a>
          );
        })}
      </nav>
    </>
  );
}
