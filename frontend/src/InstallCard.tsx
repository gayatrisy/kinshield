import { useEffect, useState } from "react";
import { installState, onInstallChange, promptInstall } from "./install";

export default function InstallCard({ compact = false }: { compact?: boolean }) {
  const [s, setS] = useState(installState());
  useEffect(() => {
    const off = onInstallChange(() => setS(installState()));
    return () => {
      off();
    };
  }, []);
  if (s.installed) return compact ? null : <p className="small muted">KinShield is installed on this device.</p>;
  if (s.canPrompt)
    return (
      <div className={compact ? "install-strip" : "panel stack"}>
        <span>{compact ? "Use KinShield like an app on this phone." : "Install KinShield on this device so it opens like an app, full screen."}</span>
        <button className="btn small" onClick={() => promptInstall().then(() => setS(installState()))}>
          Install
        </button>
      </div>
    );
  if (s.ios)
    return (
      <div className={compact ? "install-strip" : "panel stack"}>
        <span>To install on iPhone: tap Share, then “Add to Home Screen”.</span>
      </div>
    );
  return compact ? null : (
    <p className="small muted">
      To install, open KinShield in Chrome on your phone and choose “Add to Home screen” or “Install app” from the menu.
    </p>
  );
}
