import { useState } from "react";
import { createPortal } from "react-dom";
import { usePreferences, type Motion } from "../state/preferences";
import { useSession } from "../state/session";
import { Link } from "react-router-dom";

export function SettingsPanel({ central }: { central: boolean }) {
  const { theme, motion, effectiveMotion, setTheme, setMotion } =
    usePreferences();
  const session = useSession();
  const [open, setOpen] = useState(false);
  return (
    <>
      <button
        className="icon-button"
        aria-haspopup="dialog"
        onClick={() => setOpen(true)}
      >
        Settings
      </button>
      {open &&
        createPortal(
          <div
            className="dialog-backdrop"
            role="presentation"
            onMouseDown={(event) =>
              event.target === event.currentTarget && setOpen(false)
            }
          >
            <section
              className="dialog"
              role="dialog"
              aria-modal="true"
              aria-labelledby="settings-title"
            >
              <header>
                <h2 id="settings-title">Interface settings</h2>
                <button
                  className="icon-button"
                  aria-label="Close settings"
                  onClick={() => setOpen(false)}
                >
                  ×
                </button>
              </header>
              <fieldset>
                <legend>Theme</legend>
                <label>
                  <input
                    type="radio"
                    name="theme"
                    checked={theme === "glass"}
                    onChange={() => setTheme("glass")}
                  />{" "}
                  UPM Glass
                </label>
                <label>
                  <input
                    type="radio"
                    name="theme"
                    checked={theme === "classic"}
                    onChange={() => setTheme("classic")}
                  />{" "}
                  UPM Classic
                </label>
              </fieldset>
              <fieldset>
                <legend>Motion</legend>
                {(["full", "reduced", "off"] as Motion[]).map((value) => (
                  <label key={value}>
                    <input
                      type="radio"
                      name="motion"
                      checked={motion === value}
                      onChange={() => setMotion(value)}
                    />{" "}
                    {value[0].toUpperCase() + value.slice(1)}
                  </label>
                ))}
                <small>
                  Effective motion: {effectiveMotion}. Browser reduced-motion is
                  honored automatically.
                </small>
              </fieldset>
              {central && (
                <fieldset>
                  <legend>Administrator session</legend>
                  <p className="muted">
                    {session.user
                      ? `Signed in as ${session.user.display_name} (${session.user.username}).`
                      : "Not signed in."}
                  </p>
                  {session.user && (
                    <button
                      className="button"
                      type="button"
                      onClick={async () => {
                        await session.logout();
                        setOpen(false);
                      }}
                    >
                      Log out
                    </button>
                  )}
                </fieldset>
              )}
              <footer>
                <Link className="button" to="/admin/logs" onClick={() => setOpen(false)}>Logs</Link>
                <button
                  className="button button--primary"
                  onClick={() => setOpen(false)}
                >
                  Done
                </button>
              </footer>
            </section>
          </div>,
          document.body,
        )}
    </>
  );
}
