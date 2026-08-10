import type { ReactNode } from "react";
import { ApiError } from "../api/client";

export function Loading({ label = "Loading" }: { label?: string }) {
  return (
    <div className="feedback" role="status">
      <span className="spinner" aria-hidden="true" />
      {label}…
    </div>
  );
}
export function Empty({
  title = "Nothing here yet",
  children,
}: {
  title?: string;
  children?: ReactNode;
}) {
  return (
    <div className="feedback feedback--empty">
      <strong>{title}</strong>
      {children && <span>{children}</span>}
    </div>
  );
}
export function ErrorSurface({
  error,
  onRetry,
}: {
  error: unknown;
  onRetry?: () => void;
}) {
  const api =
    error instanceof ApiError
      ? error
      : new ApiError("unknown", "An unexpected application error occurred.");
  const title =
    api.kind === "unauthorized"
      ? "Administrator session required"
      : api.kind === "unavailable"
        ? "Service unavailable"
        : api.kind === "validation"
          ? "The request needs attention"
          : "The request failed";
  return (
    <div
      className={`feedback feedback--error feedback--${api.kind}`}
      role="alert"
    >
      <strong>{title}</strong>
      <span>{api.message}</span>
      {api.requestId && <small>Request ID: {api.requestId}</small>}
      {onRetry && (
        <button className="button" onClick={onRetry}>
          Try again
        </button>
      )}
    </div>
  );
}
export function PageState<T>({
  loading,
  error,
  data,
  onRetry,
  children,
  empty,
}: {
  loading: boolean;
  error?: unknown;
  data?: T;
  onRetry?: () => void;
  children: (data: T) => ReactNode;
  empty?: (data: T) => boolean;
}) {
  if (loading && data === undefined) return <Loading />;
  if (error) return <ErrorSurface error={error} onRetry={onRetry} />;
  if (data === undefined || empty?.(data)) return <Empty />;
  return <>{children(data)}</>;
}
