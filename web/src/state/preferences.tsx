import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

export type Theme = "glass" | "classic";
export type Motion = "full" | "reduced" | "off";
interface Preferences {
  theme: Theme;
  motion: Motion;
  effectiveMotion: Motion;
  setTheme: (theme: Theme) => void;
  setMotion: (motion: Motion) => void;
}
const Context = createContext<Preferences | null>(null);

function stored<T extends string>(key: string, allowed: T[], fallback: T): T {
  try {
    const value = localStorage.getItem(key) as T | null;
    return value && allowed.includes(value) ? value : fallback;
  } catch {
    return fallback;
  }
}

export function PreferencesProvider({ children }: { children: ReactNode }) {
  const [theme, setThemeState] = useState<Theme>(() =>
    stored("upm.theme", ["glass", "classic"], "glass"),
  );
  const [motion, setMotionState] = useState<Motion>(() =>
    stored("upm.motion", ["full", "reduced", "off"], "full"),
  );
  const [systemReduced, setSystemReduced] = useState(
    () =>
      window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false,
  );
  useEffect(() => {
    const query = window.matchMedia?.("(prefers-reduced-motion: reduce)");
    const change = () => setSystemReduced(query.matches);
    query?.addEventListener("change", change);
    return () => query?.removeEventListener("change", change);
  }, []);
  const effectiveMotion: Motion =
    motion === "full" && systemReduced ? "reduced" : motion;
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    localStorage.setItem("upm.theme", theme);
  }, [theme]);
  useEffect(() => {
    document.documentElement.dataset.motion = effectiveMotion;
    localStorage.setItem("upm.motion", motion);
  }, [motion, effectiveMotion]);
  const value = useMemo(
    () => ({
      theme,
      motion,
      effectiveMotion,
      setTheme: setThemeState,
      setMotion: setMotionState,
    }),
    [theme, motion, effectiveMotion],
  );
  return <Context.Provider value={value}>{children}</Context.Provider>;
}

export function usePreferences() {
  const value = useContext(Context);
  if (!value) throw new Error("PreferencesProvider is required");
  return value;
}
