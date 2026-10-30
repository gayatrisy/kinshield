import { api, saveToken, Session, withToken } from "../api";
import InstallCard from "../InstallCard";

export default function Settings({ session, onReset }: { session: Session; onReset: () => void }) {
  const t = session.telegram;
  return (
    <div className="stack-l" style={{ maxWidth: 760 }}>
      <div className="page-head">
        <h1>Settings</h1>
        <p className="muted">Where alerts go, which models are used, and your data.</p>
      </div>

      <section className="stack">
        <h2>On your phone</h2>
        <InstallCard />
      </section>

      <section className="panel stack">
        <h2>Telegram alerts</h2>
        {!t.configured ? (
          <p className="muted">This server has no Telegram bot configured, so alerts are recorded as “not sent”.</p>
        ) : t.linked ? (
          <p>
            <span className="tag ok">Linked</span> Alerts go to your Telegram chat.
          </p>
        ) : (
          <>
            <p className="muted">
              {t.demo_channel
                ? "Alerts currently go to the KinShield demo channel. To get them on your own phone, link your Telegram:"
                : "Link your Telegram to receive alerts:"}
            </p>
            {t.link_url ? (
              <a className="btn" href={t.link_url} target="_blank" rel="noreferrer" style={{ textDecoration: "none", alignSelf: "start" }}>
                Open Telegram and press Start
              </a>
            ) : (
              <p className="tiny">The bot username is not configured on this server.</p>
            )}
          </>
        )}
        <p className="tiny">
          An alert goes out when a reminder gets no response within {session.ack_window_minutes} minutes, or when your
          parent taps “I need help”. If you don't tap Seen, it is sent once more.
        </p>
      </section>

      <section className="panel stack">
        <h2>NVIDIA models on Nebius Token Factory</h2>
        {!session.models.configured && <div className="error">No Nebius API key is configured on this server.</div>}
        <table className="data">
          <tbody>
            <tr>
              <td>Reading documents</td>
              <td>{session.models.fast}</td>
            </tr>
            <tr>
              <td>Plan notes, answers, visit brief</td>
              <td>{session.models.main}</td>
            </tr>
            <tr>
              <td>Label passages</td>
              <td>{session.models.deep}</td>
            </tr>
            <tr>
              <td>Page images (only if you choose)</td>
              <td>{session.vision_fallback ? session.models.vision : "Off on this server"}</td>
            </tr>
          </tbody>
        </table>
      </section>

      <section className="panel stack">
        <h2>Your data</h2>
        <p className="muted small">
          Everything is stored in this workspace's own database on the server: documents, the plan, decisions and logs.
          Names, phone numbers, addresses and ID numbers are removed from text before it is sent to a model. That
          removal is pattern-based and can miss things.
        </p>
        <div className="row">
          <a className="btn quiet" href={withToken("/api/export")} style={{ textDecoration: "none" }}>
            Export my data
          </a>
          <button
            className="btn danger"
            onClick={async () => {
              if (!confirm("Delete this workspace and everything in it? This can't be undone.")) return;
              await api.del("/api/session");
              saveToken(null);
              onReset();
            }}
          >
            Delete everything
          </button>
        </div>
      </section>
    </div>
  );
}
