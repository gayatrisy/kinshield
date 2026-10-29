import InstallCard from "../InstallCard";

const LINKS: [string, string, string][] = [
  ["ask", "Ask KinShield", "Questions answered from your confirmed records"],
  ["brief", "Visit brief", "A cited one-page summary for the doctor"],
  ["parent", "Parent's screen", "Big buttons for your parent to answer reminders"],
  ["activity", "Activity log", "Every model call and every decision"],
  ["settings", "Settings", "Telegram alerts, models, export and delete"],
];

export default function More() {
  return (
    <div className="stack-l">
      <div className="page-head">
        <h1>More</h1>
      </div>
      <ul className="panel list more-list">
        {LINKS.map(([to, label, hint]) => (
          <li key={to}>
            <a href={`#/${to}`}>
              <strong>{label}</strong>
              <span className="small muted">{hint}</span>
            </a>
          </li>
        ))}
      </ul>
      <InstallCard />
    </div>
  );
}
