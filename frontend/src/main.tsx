import { createRoot } from "react-dom/client";
import App from "./App";

// Service-worker registration is handled by vite-plugin-pwa's auto-injected
// registerSW.js (registerType: "autoUpdate" in vite.config.ts). We deliberately
// do NOT register a second time here: a duplicate registration caused a
// spurious "offline capability unavailable" banner (a benign `redundant` event
// during the duplicate/update) and extra work in constrained WebViews. Letting
// the plugin own registration keeps a single, correct SW lifecycle.
const container = document.getElementById("root");
if (!container) {
  throw new Error("Root container #root not found");
}
createRoot(container).render(<App />);
