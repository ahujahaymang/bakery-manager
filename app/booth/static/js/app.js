/**
 * app.js — boot, screen switching, shared utilities.
 */

let saleCount = 0;

// ── Boot ───────────────────────────────────────────────────────────────────
async function boot() {
  try {
    window.session = await fetchActiveSession();
  } catch (e) {
    console.error("Failed to load session", e);
  }

  // Show setup screen if no session OR session has no items yet
  if (!window.session || window.session.items.length === 0) {
    document.getElementById("no-session").style.display = "block";
    document.getElementById("product-grid").style.display = "none";
    document.getElementById("header-setup-btn").style.display = "none";
    document.getElementById("header-end-btn").style.display = "none";
    // If there's a session but no items, pre-fill the session name
    if (window.session && window.session.items.length === 0) {
      const nameInput = document.getElementById("booth-name-input");
      if (nameInput) nameInput.value = window.session.name;
    }
    return;
  }

  document.getElementById("no-session").style.display = "none";
  document.getElementById("product-grid").style.display = "grid";
  document.getElementById("header-setup-btn").style.display = "inline-block";
  document.getElementById("header-end-btn").style.display = "inline-block";
  document.getElementById("session-meta").innerHTML =
    `<div>${esc(window.session.name)}</div><strong id="sale-count">0 sales</strong>`;

  // Fetch business name for receipts
  try {
    const info = await apiGet("/info");
    window.businessName = info.business_name || "Booth";
  } catch (e) {
    window.businessName = "Booth";
  }

  renderGrid();
}

// ── Screen switching ───────────────────────────────────────────────────────
function showScreen(name) {
  document.querySelectorAll(".screen").forEach(s => s.classList.remove("active"));
  document.getElementById(name + "-screen").classList.add("active");

  // Show/hide cart bar only on sell screen
  if (name !== "sell") {
    document.getElementById("cart-bar").classList.add("hidden");
  } else {
    updateCartBar();
  }
}

// ── Session stats ──────────────────────────────────────────────────────────
function updateSessionStats() {
  saleCount++;
  const el = document.getElementById("sale-count");
  if (el) el.textContent = `${saleCount} sale${saleCount !== 1 ? "s" : ""}`;
}

// ── Shared utilities ───────────────────────────────────────────────────────
function esc(str) {
  return String(str || "")
    .replace(/&/g, "&amp;").replace(/</g, "&lt;")
    .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

function fmt(n) {
  return Number(n).toLocaleString("en-IN");
}

function showToast(msg, duration = 2500) {
  const t = document.getElementById("toast");
  t.textContent = msg;
  t.classList.add("show");
  setTimeout(() => t.classList.remove("show"), duration);
}

// ── Init ───────────────────────────────────────────────────────────────────
boot();
