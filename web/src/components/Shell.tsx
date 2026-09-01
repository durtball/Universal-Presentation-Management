import { useEffect, useState, type ReactNode } from "react";
import { NavLink, useLocation } from "react-router-dom";
import type { Deployment } from "../api/client";
import { SettingsPanel } from "./SettingsPanel";
import { StatusBadge } from "./StatusBadge";
import { useSession } from "../state/session";

const centralNav = [
  ["/admin", "Dashboard"],
  ["/admin/sites", "Sites"],
  ["/admin/users", "Users"],
  ["/admin/events", "Events"],
  ["/admin/people", "People"],
  ["/admin/sessions", "Sessions"],
  ["/admin/presenters", "Presenters"],
  ["/admin/presentations", "Presentations"],
  ["/admin/media", "Presentation Media"],
  ["/admin/imports", "Imports"],
  ["/admin/room-mappings", "Room Mapping"],
  ["/admin/storage", "Storage"],
  ["/admin/logs", "Logs"],
];
const siteNav = [
  ["/admin", "Overview"],
  ["/admin/central", "Central Connection"],
  ["/admin/program", "Program"],
  ["/admin/rooms", "Rooms"],
  ["/admin/devices", "Devices"],
  ["/admin/users", "Users"],
  ["/admin/storage", "Storage"],
  ["/admin/media", "Presentation Media"],
  ["/admin/presentations", "Presentations"],
  ["/admin/logs", "Logs"],
];

export function Shell({
  deployment,
  context,
  children,
}: {
  deployment: Deployment;
  context?: {
    name?: string;
    id?: string;
    connectivity?: unknown;
    health?: unknown;
    synchronization?: unknown;
  };
  children: ReactNode;
}) {
  const session=useSession();
  const nav = (deployment === "central" ? centralNav : siteNav).filter(([path]) =>
    deployment==="central" || path!=="/admin/users" || session.can("users.read"));
  const location = useLocation();
  const [menu, setMenu] = useState(false);
  const [online, setOnline] = useState(navigator.onLine);
  useEffect(() => {
    const update = () => setOnline(navigator.onLine);
    window.addEventListener("online", update);
    window.addEventListener("offline", update);
    return () => {
      window.removeEventListener("online", update);
      window.removeEventListener("offline", update);
    };
  }, []);
  const current =
    nav.find(
      ([path]) =>
        path === location.pathname ||
        (path !== "/admin" && location.pathname.startsWith(`${path}/`)),
    )?.[1] ?? "Administration";
  return (
    <div className="app-shell">
      <a className="skip-link" href="#main-content">
        Skip to main content
      </a>
      <aside className={menu ? "sidebar sidebar--open" : "sidebar"}>
        <div className="brand">
          <span className="brand__mark" aria-hidden="true">
            U
          </span>
          <span>
            <strong>UPM</strong>
            <small>Universal Presentation Management</small>
          </span>
        </div>
        <nav aria-label="Primary navigation">
          {nav.map(([path, label]) => (
            <NavLink
              key={path}
              to={path}
              end={path === "/admin"}
              onClick={() => setMenu(false)}
              className={({ isActive }) =>
                isActive ? "nav-link nav-link--active" : "nav-link"
              }
            >
              {label}
            </NavLink>
          ))}
        </nav>
        <div className={`scope-card scope-card--${deployment}`}>
          <small>Current context</small>
          <strong>
            {deployment === "central"
              ? "UPM Central"
              : context?.name || "UPM Site"}
          </strong>
          {context?.id ? (
            <code title={context.id}>{context.id.slice(0, 8)}…</code>
          ) : null}
          <div className="scope-card__states">
            <StatusBadge
              value={
                context?.health ??
                (deployment === "central" ? "healthy" : "pending")
              }
            />
            {context?.connectivity != null ? (
              <StatusBadge value={context.connectivity} />
            ) : null}
            {context?.synchronization != null ? (
              <StatusBadge value={context.synchronization} />
            ) : null}
          </div>
        </div>
      </aside>
      <div className="workspace">
        <header className="topbar">
          <button
            className="menu-button"
            aria-label="Toggle navigation"
            aria-expanded={menu}
            onClick={() => setMenu(!menu)}
          >
            ☰
          </button>
          <div>
            <small>
              {deployment === "central"
                ? "Global control plane"
                : "Site-local operations"}
            </small>
            <h1>{current}</h1>
          </div>
          <div className="topbar__actions">
            {session.user?<span>{session.user.display_name}</span>:null}
            <StatusBadge value={online ? "online" : "offline"} />
            <button className="button button--small" onClick={()=>void session.logout()}>Logout</button>
            <SettingsPanel central={deployment === "central"} />
          </div>
        </header>
        <main id="main-content" className="content" tabIndex={-1}>
          {children}
        </main>
        <div className="toast-region" role="status" aria-live="polite">
          {!online && (
            <div className="toast">
              Browser network offline. Site-local data already loaded remains
              visible.
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
