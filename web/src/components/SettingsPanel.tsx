import { useState } from "react";
import { createPortal } from "react-dom";
import { usePreferences, type Motion } from "../state/preferences";
import { useSession } from "../state/session";

export function SettingsPanel({ central }: { central: boolean }) {
  const { theme, motion, effectiveMotion, setTheme, setMotion } =
    usePreferences();
  const session = useSession();
  const [open, setOpen] = useState(false);
  const [token, setToken] = useState("");
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
                <form
                  onSubmit={(event) => {
                    event.preventDefault();
                    session.setAdminToken(token);
                    setToken("");
                  }}
                >
                  <fieldset>
                    <legend>Temporary administrator session</legend>
                    <p className="muted">
                      Until full authentication is implemented, enter the
                      existing Central admin token. It is kept only for this
                      browser tab.
                    </p>
                    <label className="field">
                      Admin token
                      <input
                        className="input"
                        type="password"
                        autoComplete="off"
                        value={token}
                        onChange={(event) => setToken(event.target.value)}
                      />
                    </label>
                    <div className="button-row">
                      <button className="button button--primary" type="submit">
                        Start session
                      </button>
                      {session.adminToken && (
                        <button
                          className="button"
                          type="button"
                          onClick={() => session.setAdminToken(null)}
                        >
                          End session
                        </button>
                      )}
                    </div>
                  </fieldset>
                </form>
              )}
              <footer>
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
