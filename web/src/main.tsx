import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { App } from "./App";
import { applyStoredPreferences, PreferencesProvider } from "./state/preferences";
import { SessionProvider } from "./state/session";
import "./styles.css";

applyStoredPreferences();

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <BrowserRouter>
      <PreferencesProvider>
        <SessionProvider deployment={__UPM_DEPLOYMENT__}>
          <App deployment={__UPM_DEPLOYMENT__} />
        </SessionProvider>
      </PreferencesProvider>
    </BrowserRouter>
  </StrictMode>,
);
