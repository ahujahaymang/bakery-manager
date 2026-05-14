/**
 * checkout.js — checkout sheet and payment flow.
 */

let selectedPayment = "cash";

function openCheckout() {
  const lines = document.getElementById("order-lines");
  lines.innerHTML = "";
  let total = 0;

  for (const [vid, qty] of Object.entries(cart)) {
    const item = window.session.items.find(i => i.variant_id === vid);
    if (!item) continue;
    const lineTotal = item.booth_price * qty;
    total += lineTotal;
    const div = document.createElement("div");
    div.className = "order-line";
    div.innerHTML = `
      <span>${esc(item.product_name)} ${esc(item.variant_label)} × ${qty}</span>
      <span>₹${fmt(lineTotal)}</span>
    `;
    lines.appendChild(div);
  }

  const totalDiv = document.createElement("div");
  totalDiv.className = "order-line total";
  totalDiv.innerHTML = `<span>Total</span><span>₹${fmt(total)}</span>`;
  lines.appendChild(totalDiv);

  document.getElementById("customer-name-input").value = "";
  selectPayment("cash");
  document.getElementById("checkout-overlay").classList.remove("hidden");
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

  const items = Object.entries(cart).map(([variant_id, quantity]) => ({ variant_id, quantity }));
  const customerName = document.getElementById("customer-name-input").value.trim() || null;

  try {
    const order = await submitCheckout(
      window.session.session_id, items, selectedPayment, customerName
    );
    closeCheckout();

    if (selectedPayment === "razorpay" && order.razorpay_qr_url) {
      showQR(order);
    } else {
      showReceipt(order);
      updateSessionStats();
    }
  } catch (e) {
    showToast("Error: " + e.message);
    btn.disabled = false;
    btn.textContent = "Confirm Sale";
  }
}
