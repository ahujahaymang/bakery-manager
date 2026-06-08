/**
 * setup.js — booth setup screen.
 * Owner can multi-select products from catalog, set booth prices,
 * add custom products, and create/update the booth session.
 */

let catalogData = null;       // full catalog from API
let setupSelection = {};      // { variant_id: booth_price }
let editingSession = false;   // true when adding to existing session

async function openSetup(editing = false) {
  editingSession = editing;
  setupSelection = {};
  window.customProducts = [];

  // Pre-populate with current session items if editing
  if (editing && window.session && window.session.items) {
    for (const item of window.session.items) {
      setupSelection[item.variant_id] = item.booth_price;
    }
  }

  // Pre-fill session name and mode
  const nameInput = document.getElementById("booth-name-input");
  if (nameInput && window.session) nameInput.value = window.session.name;

  // Restore mode toggle
  const mode = window.session ? (window.session.mode || "regular") : "regular";
  setMode(mode);

  showScreen("setup");

  try {
    catalogData = await fetchCatalog();
    renderSetup();
  } catch (e) {
    showToast("Could not load catalog: " + e.message);
  }
}

function renderSetup() {
  const container = document.getElementById("setup-categories");
  container.innerHTML = "";

  if (!catalogData || !catalogData.categories || catalogData.categories.length === 0) {
    container.innerHTML = `<p style="color:var(--muted);padding:20px;text-align:center">
      No products in catalog yet. Add products first.</p>`;
    updateSetupFooter();
    return;
  }

  for (const cat of catalogData.categories) {
    const section = document.createElement("div");
    section.className = "setup-section";
    section.innerHTML = `<h3>${esc(cat.name)}</h3><div class="setup-product-list" id="cat-${esc(cat.name)}"></div>`;
    container.appendChild(section);

    const list = section.querySelector(".setup-product-list");
    for (const product of cat.products) {
      for (const variant of product.variants) {
        const isSelected = variant.variant_id in setupSelection;
        const currentPrice = setupSelection[variant.variant_id] || variant.price;

        const row = document.createElement("div");
        row.className = "setup-product-row" + (isSelected ? " selected" : "");
        row.id = `setup-${variant.variant_id}`;
        row.innerHTML = `
          <div class="check" onclick="toggleSetupItem('${variant.variant_id}', ${variant.price})"></div>
          <div class="setup-product-info">
            <div class="name">${esc(product.name)}</div>
            <div class="variants">${esc(variant.size_label)}</div>
          </div>
          <input class="setup-price-input" type="number" min="0" step="1"
            value="${currentPrice}"
            placeholder="₹"
            onchange="updateSetupPrice('${variant.variant_id}', this.value)"
            onclick="event.stopPropagation()"
            ${isSelected ? "" : "disabled"}>
        `;
        list.appendChild(row);
      }
    }
  }

  updateSetupFooter();
}

function toggleSetupItem(variantId, defaultPrice) {
  const row = document.getElementById(`setup-${variantId}`);
  const input = row.querySelector(".setup-price-input");

  if (variantId in setupSelection) {
    delete setupSelection[variantId];
    row.classList.remove("selected");
    input.disabled = true;
  } else {
    setupSelection[variantId] = parseFloat(input.value) || defaultPrice;
    row.classList.add("selected");
    input.disabled = false;
    input.focus();
  }
  updateSetupFooter();
}

function updateSetupPrice(variantId, value) {
  if (variantId in setupSelection) {
    setupSelection[variantId] = parseFloat(value) || 0;
  }
}

function updateSetupFooter() {
  const count = Object.keys(setupSelection).length;
  document.getElementById("setup-count").textContent =
    count === 0 ? "No products selected" : `${count} product variant${count !== 1 ? "s" : ""} selected`;
  document.getElementById("create-booth-btn").disabled = count === 0;
}

function setMode(mode) {
  document.querySelectorAll(".mode-btn").forEach(b => {
    b.classList.toggle("selected", b.dataset.mode === mode);
  });
  const eventFields = document.getElementById("event-fields");
  if (eventFields) eventFields.style.display = mode === "event" ? "block" : "none";
}

function getMode() {
  const btn = document.querySelector(".mode-btn.selected");
  return btn ? btn.dataset.mode : "regular";
}

function addCustomProduct() {
  const name = document.getElementById("custom-name").value.trim();
  const size = document.getElementById("custom-size").value.trim() || "standard";
  const price = parseFloat(document.getElementById("custom-price").value);

  if (!name || !price || price <= 0) {
    showToast("Enter product name and price");
    return;
  }

  // Add to a temporary "Custom" section in the selection
  // We'll pass these as new products to the create endpoint
  const tempId = `custom-${Date.now()}`;
  setupSelection[tempId] = price;

  // Store custom product details for the create call
  if (!window.customProducts) window.customProducts = [];
  window.customProducts.push({ temp_id: tempId, name, size_label: size, price });

  showToast(`Added: ${name} (${size}) ₹${price}`);
  document.getElementById("custom-name").value = "";
  document.getElementById("custom-size").value = "";
  document.getElementById("custom-price").value = "";
  updateSetupFooter();
}

async function createBooth() {
  const btn = document.getElementById("create-booth-btn");
  btn.disabled = true;
  btn.textContent = "Opening…";

  const sessionName = document.getElementById("booth-name-input").value.trim() ||
    (window.session ? window.session.name : null) ||
    `Booth ${new Date().toLocaleDateString("en-IN", { day: "2-digit", month: "short" })}`;

  // Build items array: [{variant_id, booth_price}]
  const items = Object.entries(setupSelection)
    .filter(([id]) => !id.startsWith("custom-"))
    .map(([variant_id, booth_price]) => ({ variant_id, booth_price }));

  // Add custom products
  const customItems = (window.customProducts || []).map(p => ({
    custom: true,
    name: p.name,
    size_label: p.size_label,
    booth_price: p.price,
  }));

  const allItems = [...items, ...customItems];

  if (allItems.length === 0) {
    showToast("Select at least one product");
    btn.disabled = false;
    btn.textContent = "Open Register";
    return;
  }

  const mode = getMode();
  const durationDays = mode === "event"
    ? parseInt(document.getElementById("event-days").value) || 1
    : null;

  try {
    await createBoothSession(sessionName, allItems, mode, durationDays);
    window.session = await fetchActiveSession();
    window.customProducts = [];

    if (!window.session || window.session.items.length === 0) {
      showToast("Session created but no items added — check product catalog");
      btn.disabled = false;
      btn.textContent = "Open Register";
      return;
    }

    // Update header
    document.getElementById("no-session").style.display = "none";
    document.getElementById("product-grid").style.display = "grid";
    document.getElementById("header-orders-btn").style.display = "inline-block";
    document.getElementById("header-setup-btn").style.display = "inline-block";
    document.getElementById("header-end-btn").style.display = "inline-block";
    document.getElementById("session-meta").innerHTML =
      `<div>${esc(window.session.name)}</div><strong id="sale-count">0 sales</strong>`;

    // Show event countdown in header if event mode
    if (window.session.ends_at) {
      updateEventCountdown(window.session.ends_at);
    }

    renderGrid();
    showScreen("sell");
    const modeLabel = mode === "event" ? ` (${durationDays}-day event)` : "";
    showToast(`✅ ${sessionName}${modeLabel} is live with ${window.session.items.length} products!`);
  } catch (e) {
    showToast("Error: " + e.message);
    btn.disabled = false;
    btn.textContent = editingSession ? "Update Register" : "Open Register";
  }
}

async function endBooth() {
  if (!confirm("End the register session? This will close sales for today.")) return;
  try {
    const summary = await endBoothSession();
    window.session = null;
    showScreen("sell");
    showToast(`Session ended · ₹${fmt(summary.total_revenue || 0)} revenue`);
    // Reload to show no-session state
    setTimeout(() => location.reload(), 1500);
  } catch (e) {
    showToast("Error: " + e.message);
  }
}
