/**
 * checkout.js — checkout sheet and payment flow.
 */

let selectedPayment = "cash";

// Per-item extra charges: { variant_id: { charge, note } }
let itemExtras = {};

function openCheckout() {
  itemExtras = {};
  const lines = document.getElementById("order-lines");
  lines.innerHTML = "";
  let subtotal = 0;

  for (const [vid, qty] of Object.entries(cart)) {
    const item = window.session.items.find(i => i.variant_id === vid);
    if (!item) continue;
    const lineTotal = item.booth_price * qty;
    subtotal += lineTotal;

    const div = document.createElement("div");
    div.className = "order-line-group";
    div.innerHTML = `
      <div class="order-line">
        <span>${esc(item.product_name)} ${esc(item.variant_label)} × ${qty}</span>
        <span>₹${fmt(lineTotal)}</span>
      </div>
      <div class="extra-row" id="extra-row-${vid}">
        <input type="text" class="extra-note-input" placeholder="Add note (e.g. fondant, custom box)"
          id="extra-note-${vid}" oninput="updateExtra('${vid}')">
        <div class="extra-charge-wrap">
          <span class="extra-plus">+₹</span>
          <input type="number" class="extra-charge-input" min="0" step="1" placeholder="0"
            id="extra-charge-${vid}" oninput="updateExtra('${vid}')">
        </div>
      </div>
    `;
    lines.appendChild(div);
  }

  // Subtotal line (will be updated when extras change)
  const subtotalDiv = document.createElement("div");
  subtotalDiv.className = "order-line";
  subtotalDiv.id = "order-subtotal";
  subtotalDiv.innerHTML = `<span>Subtotal</span><span id="subtotal-val">₹${fmt(subtotal)}</span>`;
  lines.appendChild(subtotalDiv);

  // GST row
  const gstDiv = document.createElement("div");
  gstDiv.className = "order-line gst-row";
  gstDiv.innerHTML = `
    <span>GST</span>
    <div class="gst-input-wrap">
      <input type="number" id="gst-rate-input" min="0" max="100" step="0.5"
        placeholder="0" style="width:55px;padding:4px 6px;border:1px solid var(--border);border-radius:6px;text-align:right"
        oninput="updateOrderTotal()"> %
    </div>
  `;
  lines.appendChild(gstDiv);

  // Total line
  const totalDiv = document.createElement("div");
  totalDiv.className = "order-line total";
  totalDiv.id = "order-total-line";
  totalDiv.innerHTML = `<span>Total</span><span id="order-total-val">₹${fmt(subtotal)}</span>`;
  lines.appendChild(totalDiv);

  document.getElementById("customer-name-input").value = "";
  selectPayment("cash");
  document.getElementById("checkout-overlay").classList.remove("hidden");
}

function updateExtra(vid) {
  const note = document.getElementById(`extra-note-${vid}`).value.trim();
  const charge = parseFloat(document.getElementById(`extra-charge-${vid}`).value) || 0;
  itemExtras[vid] = { charge, note };
  updateOrderTotal();
}

function updateOrderTotal() {
  let subtotal = 0;
  for (const [vid, qty] of Object.entries(cart)) {
    const item = window.session.items.find(i => i.variant_id === vid);
    if (!item) continue;
    subtotal += item.booth_price * qty;
    const extra = itemExtras[vid];
    if (extra) subtotal += extra.charge || 0;
  }

  const gstRate = parseFloat(document.getElementById("gst-rate-input")?.value) || 0;
  const gstAmt = subtotal * gstRate / 100;
  const total = subtotal + gstAmt;

  const stEl = document.getElementById("subtotal-val");
  if (stEl) stEl.textContent = `₹${fmt(subtotal)}`;
  const totEl = document.getElementById("order-total-val");
  if (totEl) totEl.textContent = `₹${fmt(total)}`;
}

function closeCheckout() {
  document.getElementById("checkout-overlay").classList.add("hidden");
}

function closeCheckoutIfOutside(e) {
  if (e.target === document.getElementById("checkout-overlay")) closeCheckout();
}

function selectPayment(method) {
  selectedPayment = method;
  document.querySelectorAll(".pay-btn").forEach(btn => {
    btn.classList.toggle("selected", btn.dataset.method === method);
  });
}

async function confirmCheckout() {
  const btn = document.getElementById("confirm-btn");
  btn.disabled = true;
  btn.textContent = "Processing…";

  const gstRate = parseFloat(document.getElementById("gst-rate-input")?.value) || 0;

  const items = Object.entries(cart).map(([variant_id, quantity]) => {
    const extra = itemExtras[variant_id] || {};
    return {
      variant_id,
      quantity,
      extra_charge: extra.charge || 0,
      extra_note: extra.note || null,
    };
  });

  const customerName = document.getElementById("customer-name-input").value.trim() || null;

  try {
    const order = await submitCheckout(
      window.session.session_id, items, selectedPayment, customerName, gstRate
    );
    closeCheckout();

    if (selectedPayment === "razorpay" && order.razorpay_qr_url) {
      showQR(order);
    } else {
      showReceipt(order);
      // Note: showReceipt calls updateSessionStats — do NOT call it again here
    }
  } catch (e) {
    showToast("Error: " + e.message);
    btn.disabled = false;
    btn.textContent = "Confirm Sale";
  }
}
