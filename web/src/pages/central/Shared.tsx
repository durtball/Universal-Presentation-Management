import type { ReactNode } from "react";
import { useSession } from "../../state/session";
import { ErrorSurface } from "../../components/Feedback";
import { ApiError } from "../../api/client";

export function AdminBoundary({ children }: { children: ReactNode }) {
  const session = useSession();
  if (!session.adminToken)
    return (
      <ErrorSurface
        error={
          new ApiError(
            "unauthorized",
            "Open Settings and enter the existing Central administrator token to access protected operational data.",
          )
        }
      />
    );
  return <>{children}</>;
}
export function when(value: unknown) {
  if (!value) return "—";
  const date = new Date(String(value));
  return Number.isNaN(date.valueOf())
    ? String(value)
    : new Intl.DateTimeFormat(undefined, {
        dateStyle: "medium",
        timeStyle: "short",
      }).format(date);
}
export function bytes(value: unknown) {
  const amount = Number(value);
  if (!Number.isFinite(amount)) return "—";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let scaled = amount;
  let index = 0;
  while (scaled >= 1024 && index < units.length - 1) {
    scaled /= 1024;
    index += 1;
  }
  return `${scaled.toFixed(index ? 1 : 0)} ${units[index]}`;
}
