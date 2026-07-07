import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { VitePWA } from "vite-plugin-pwa";

// The built SPA is emitted to ../app/webapp_static/ and served by FastAPI
// mounted at /app (mirroring the existing booth static mount). The app base
// path therefore matches the mount point.
export default defineConfig({
  base: "/app/",
  plugins: [
    react(),
    // Workbox-backed service worker plus the web app manifest. The service
    // worker source lives in src/sw.ts (injectManifest strategy): it precaches
    // the app shell and runtime-caches the product catalog (task 6.3). Vite
    // injects the precache manifest (built HTML/CSS/JS/icons) into
    // self.__WB_MANIFEST inside src/sw.ts.
    VitePWA({
      registerType: "autoUpdate",
      strategies: "injectManifest",
      srcDir: "src",
      filename: "sw.ts",
      injectManifest: {
        // App-shell assets precached into self.__WB_MANIFEST.
        globPatterns: ["**/*.{js,css,html,ico,png,svg,woff2}"],
      },
      // Web app manifest (Req 1.1, 1.6): installable, standalone, launches at
      // /app. Icons are served from public/icons/ relative to the /app/ base.
      manifest: {
        name: "KitchenOS",
        short_name: "KitchenOS",
        description:
          "KitchenOS app-first PWA for home bakers and cloud kitchens",
        start_url: "/app",
        scope: "/app/",
        display: "standalone",
        background_color: "#ffffff",
        theme_color: "#111827",
        icons: [
          {
            src: "icons/icon-192.png",
            sizes: "192x192",
            type: "image/png",
            purpose: "any maskable",
          },
          {
            src: "icons/icon-512.png",
            sizes: "512x512",
            type: "image/png",
            purpose: "any maskable",
          },
        ],
      },
    }),
  ],
  build: {
    outDir: "../app/webapp_static",
    emptyOutDir: true,
  },
});
