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
  document.getElementById("header-orders-btn").style.display = "inline-block";
  document.getElementById("header-setup-btn").style.display = "inline-block";
  document.getElementById("header-end-btn").style.display = "inline-block";
  document.getElementById("session-meta").innerHTML =
    `<div>${esc(window.session.name)}</div><strong id="sale-count">0 sales</strong>`;

  // Show event countdown if event session
  if (window.session.ends_at) {
    updateEventCountdown(window.session.ends_at);
  }

  // Fetch business name for receipts
  try {
    const info = await apiGet("/info");
    window.businessName = info.business_name || "Register";
  } catch (e) {
    window.businessName = "Register";
  }

  renderGrid();
}

// ── Event countdown ────────────────────────────────────────────────────────
function updateEventCountdown(endsAtIso) {
  const el = document.getElementById("event-countdown");
  if (!el) return;

  const endsAt = new Date(endsAtIso);

  function tick() {
    const now = new Date();
    const diff = endsAt - now;

    if (diff <= 0) {
      el.textContent = "⏰ Event ended";
      el.classList.remove("hidden");
      return;
    }

    const days = Math.floor(diff / 86400000);
    const hours = Math.floor((diff % 86400000) / 3600000);
    const mins = Math.floor((diff % 3600000) / 60000);

    let label = "";
    if (days > 0) label = `🎪 ${days}d ${hours}h left`;
    else if (hours > 0) label = `🎪 ${hours}h ${mins}m left`;
    else label = `🎪 ${mins}m left`;

    el.textContent = label;
    el.classList.remove("hidden");
  }

  tick();
  setInterval(tick, 60000); // update every minute
}

// ── Orders screen ─────────────────────────────────────────────────────────
async function openOrders() {
  showScreen("orders");
  const list = document.getElementById("orders-list");
  list.innerHTML = `<div style="text-align:center;padding:40px;color:var(--muted)">Loading…</div>`;

  try {
    const data = await fetchSessionOrders();
    const orders = data.orders || [];

    document.getElementById("orders-title").textContent =
      `${esc(data.session_name || "Today")}'s Orders`;

    if (orders.length === 0) {
      list.innerHTML = `<div style="text-align:center;padding:40px;color:var(--muted)">No orders yet</div>`;
      document.getElementById("orders-summary").textContent = "";
      return;
    }

    const total = orders.reduce((s, o) => s + o.total_amount, 0);
    document.getElementById("orders-summary").textContent =
      `${orders.length} order${orders.length !== 1 ? "s" : ""} · ₹${fmt(total)}`;

    list.innerHTML = orders.map((o, idx) => {
      const time = o.created_at
        ? new Date(o.created_at).toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit" })
        : "";
      const itemLines = o.items.map(i => {
        let line = `<div class="o-item">${esc(i.recipe_name)} ×${i.quantity} — ₹${fmt(i.unit_price * i.quantity)}`;
        if (i.customization_charge > 0) line += ` + ₹${fmt(i.customization_charge)} (${esc(i.customization_note || "extra")})`;
        line += `</div>`;
        return line;
      }).join("");

      return `
        <div class="order-card">
          <div class="order-card-header">
            <span class="order-num">#${idx + 1} · ${esc(o.customer_name)}</span>
            <span class="order-time">${time}</span>
          </div>
          ${itemLines}
          <div class="order-card-footer">
            <span class="order-method">${esc(o.payment_method)}</span>
            <span class="order-total">₹${fmt(o.total_amount)}</span>
          </div>
        </div>
      `;
    }).join("");
  } catch (e) {
    list.innerHTML = `<div style="text-align:center;padding:40px;color:var(--accent)">${e.message}</div>`;
  }
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
