import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { centralApi } from "../api/central";
import type { AuthSession } from "../api/types";

interface SessionContext {
  status: "loading" | "authenticated" | "unauthenticated";
  csrfToken: string | null;
  user: AuthSession["user"] | null;
  login: (username: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  can: (permission: string) => boolean;
}

const Context = createContext<SessionContext | null>(null);

export function SessionProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<SessionContext["status"]>("loading");
  const [csrfToken, setCsrfToken] = useState<string | null>(null);
  const [user, setUser] = useState<AuthSession["user"] | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    centralApi()
      .session(controller.signal)
      .then((value) => {
        setUser(value.user);
        setCsrfToken(value.csrf_token ?? null);
        setStatus("authenticated");
      })
      .catch(() => {
        setUser(null);
        setCsrfToken(null);
        setStatus("unauthenticated");
      });
    return () => controller.abort();
  }, []);

  const value = useMemo<SessionContext>(
    () => ({
      status,
      csrfToken,
      user,
      login: async (username, password) => {
        const authenticated = await centralApi().login(username, password);
        setUser(authenticated.user);
        setCsrfToken(authenticated.csrf_token ?? null);
        setStatus("authenticated");
      },
      logout: async () => {
        try {
          await centralApi(csrfToken).logout();
        } finally {
          setUser(null);
          setCsrfToken(null);
          setStatus("unauthenticated");
        }
      },
      can: () => user?.roles.includes("administrator") ?? false,
    }),
    [status, csrfToken, user],
  );
  return <Context.Provider value={value}>{children}</Context.Provider>;
}

export function useSession() {
  const value = useContext(Context);
  if (!value) throw new Error("SessionProvider is required");
  return value;
}
