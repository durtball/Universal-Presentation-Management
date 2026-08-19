import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { centralApi } from "../api/central";
import { siteApi } from "../api/site";
import { setSiteCsrfToken, type Deployment } from "../api/client";
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

export function SessionProvider({ children,deployment="central" }: { children: ReactNode;deployment?:Deployment }) {
  const [status, setStatus] = useState<SessionContext["status"]>("loading");
  const [csrfToken, setCsrfToken] = useState<string | null>(null);
  const [user, setUser] = useState<AuthSession["user"] | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    (deployment==="central"?centralApi().session(controller.signal):siteApi.session(controller.signal))
      .then((value) => {
        setUser(value.user);
        setCsrfToken(value.csrf_token ?? null);
        if(deployment==="site")setSiteCsrfToken(value.csrf_token ?? null);
        setStatus("authenticated");
      })
      .catch(() => {
        setUser(null);
        setCsrfToken(null);
        setStatus("unauthenticated");
      });
    return () => controller.abort();
  }, [deployment]);

  const value = useMemo<SessionContext>(
    () => ({
      status,
      csrfToken,
      user,
      login: async (username, password) => {
        const authenticated = await (deployment==="central"?centralApi().login(username,password):siteApi.login(username,password));
        setUser(authenticated.user);
        setCsrfToken(authenticated.csrf_token ?? null);
        if(deployment==="site")setSiteCsrfToken(authenticated.csrf_token ?? null);
        setStatus("authenticated");
      },
      logout: async () => {
        try {
          await (deployment==="central"?centralApi(csrfToken).logout():siteApi.logout());
        } finally {
          setUser(null);
          setCsrfToken(null);
          if(deployment==="site")setSiteCsrfToken(null);
          setStatus("unauthenticated");
        }
      },
      can: (permission) => user?.roles.includes("administrator") || user?.permissions?.includes(permission) || user?.permissions?.includes("*") || false,
    }),
    [status, csrfToken, user, deployment],
  );
  return <Context.Provider value={value}>{children}</Context.Provider>;
}

export function useSession() {
  const value = useContext(Context);
  if (!value) throw new Error("SessionProvider is required");
  return value;
}
