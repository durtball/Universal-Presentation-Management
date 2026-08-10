import { Component, type ErrorInfo, type ReactNode } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import type { Deployment } from "./api/client";
import { centralApi } from "./api/central";
import { siteApi } from "./api/site";
import { ErrorSurface } from "./components/Feedback";
import { Shell } from "./components/Shell";
import { useApi } from "./hooks/useApi";
import { Dashboard } from "./pages/central/Dashboard";
import { EventDetailRoute } from "./pages/central/EventDetailRoute";
import { Events } from "./pages/central/Events";
import { EventScoped } from "./pages/central/EventScoped";
import { Imports } from "./pages/central/Imports";
import { People } from "./pages/central/People";
import { Sites } from "./pages/central/Sites";
import { SiteOverview, SiteProgram, SiteStorage } from "./pages/site/SitePages";

export function App({ deployment }: { deployment: Deployment }) {
  return (
    <AppErrorBoundary>
      {deployment === "central" ? <CentralApp /> : <SiteApp />}
    </AppErrorBoundary>
  );
}
function CentralApp() {
  const health = useApi(async (signal) => {
    try {
      await centralApi(null).health(signal);
      return { health: "healthy" };
    } catch {
      return { health: "offline" };
    }
  }, []);
  return (
    <Shell deployment="central" context={health.data}>
      <Routes>
        <Route path="/" element={<Navigate to="/admin" replace />} />
        <Route path="/admin" element={<Dashboard />} />
        <Route path="/admin/sites" element={<Sites />} />
        <Route path="/admin/events" element={<Events />} />
        <Route path="/admin/events/:eventId" element={<EventDetailRoute />} />
        <Route path="/admin/people" element={<People />} />
        <Route
          path="/admin/sessions"
          element={<EventScoped type="sessions" />}
        />
        <Route
          path="/admin/presenters"
          element={<EventScoped type="presenters" />}
        />
        <Route
          path="/admin/presentations"
          element={<EventScoped type="presentations" />}
        />
        <Route path="/admin/imports" element={<Imports />} />
        <Route path="*" element={<Navigate to="/admin" replace />} />
      </Routes>
    </Shell>
  );
}
function SiteApp() {
  const status = useApi(async (signal) => {
    try {
      const registration = await siteApi.registration(signal);
      return {
        name: registration.display_name,
        id: registration.site_id,
        connectivity: registration.connection_status,
        health: "healthy",
        synchronization: registration.failed_sync
          ? "failed"
          : registration.pending_outbound
            ? "synchronizing"
            : "synchronized",
      };
    } catch {
      return { health: "degraded" };
    }
  }, []);
  return (
    <Shell deployment="site" context={status.data}>
      <Routes>
        <Route path="/" element={<Navigate to="/admin" replace />} />
        <Route path="/admin" element={<SiteOverview />} />
        <Route path="/admin/program" element={<SiteProgram />} />
        <Route path="/admin/storage" element={<SiteStorage />} />
        <Route path="*" element={<Navigate to="/admin" replace />} />
      </Routes>
    </Shell>
  );
}

class AppErrorBoundary extends Component<
  { children: ReactNode },
  { error?: Error }
> {
  state: { error?: Error } = {};
  static getDerivedStateFromError(error: Error) {
    return { error };
  }
  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("UPM application error", error, info.componentStack);
  }
  render() {
    return this.state.error ? (
      <main className="fatal">
        <ErrorSurface
          error={this.state.error}
          onRetry={() => window.location.reload()}
        />
      </main>
    ) : (
      this.props.children
    );
  }
}
