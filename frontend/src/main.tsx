import { createRoot } from "react-dom/client";
import App from "./App";
import { registerServiceWorker } from "./registerSW";

const container = document.getElementById("root");
if (!container) {
  throw new Error("Root container #root not found");
}
createRoot(container).render(<App />);

// Register the Workbox service worker on first load (Req 1.2). Registration is
// best-effort and never blocks the app: if it fails, the app keeps running over
// the network and surfaces an "offline capability unavailable" indication
// (Req 1.3).
void registerServiceWorker();
