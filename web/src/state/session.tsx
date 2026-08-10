import {
  createContext,
  useContext,
  useMemo,
  useState,
  type ReactNode,
} from "react";

interface Session {
  adminToken: string | null;
  setAdminToken: (token: string | null) => void;
  user: { displayName: string; roles: string[] } | null;
  can: (permission: string) => boolean;
}
const Context = createContext<Session | null>(null);
const KEY = "upm.central.admin-token";

export function SessionProvider({ children }: { children: ReactNode }) {
  const [adminToken, update] = useState<string | null>(() =>
    sessionStorage.getItem(KEY),
  );
  const setAdminToken = (token: string | null) => {
    const clean = token?.trim() || null;
    if (clean) sessionStorage.setItem(KEY, clean);
    else sessionStorage.removeItem(KEY);
    update(clean);
  };
  const value = useMemo<Session>(
    () => ({
      adminToken,
      setAdminToken,
      user: adminToken
        ? { displayName: "Current operator", roles: ["administrator"] }
        : null,
      can: () => Boolean(adminToken),
    }),
    [adminToken],
  );
  return <Context.Provider value={value}>{children}</Context.Provider>;
}

export function useSession() {
  const value = useContext(Context);
  if (!value) throw new Error("SessionProvider is required");
  return value;
}
