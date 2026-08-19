import { useState, type FormEvent } from "react";
import { Navigate, useLocation, useNavigate } from "react-router-dom";
import { ErrorSurface } from "../../components/Feedback";
import { useSession } from "../../state/session";
import type { Deployment } from "../../api/client";

export function Login({deployment="central"}:{deployment?:Deployment}) {
  const session = useSession();
  const navigate = useNavigate();
  const location = useLocation();
  const [username, setUsername] = useState("admin");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>();
  if (session.status === "authenticated") return <Navigate to="/admin" replace />;
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError(undefined);
    try {
      await session.login(username, password);
      const target = (location.state as { from?: string } | null)?.from || "/admin";
      navigate(target, { replace: true });
    } catch (caught) {
      setError(caught);
    } finally {
      setBusy(false);
    }
  };
  return (
    <main className="login-page">
      <section className="login-card" aria-labelledby="login-title">
        <div className="brand brand--login">
          <span className="brand__mark" aria-hidden="true">U</span>
          <span><strong>{deployment==="central"?"UPM Central":"UPM Site"}</strong><small>Administration</small></span>
        </div>
        <h1 id="login-title">Administrator login</h1>
        <p className="muted">Sign in to manage events, programs, Sites, and deployments.</p>
        <form onSubmit={submit}>
          <label className="field">
            Username
            <input className="input" autoComplete="username" required value={username}
              onChange={(event) => setUsername(event.target.value)} />
          </label>
          <label className="field">
            Password
            <input className="input" type="password" autoComplete="current-password" required
              value={password} onChange={(event) => setPassword(event.target.value)} />
          </label>
          <button className="button button--primary" disabled={busy}>
            {busy ? "Signing in…" : "Sign in"}
          </button>
        </form>
        {error != null ? <ErrorSurface error={error} /> : null}
      </section>
    </main>
  );
}
