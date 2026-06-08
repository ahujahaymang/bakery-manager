/**
 * receipt.js — receipt rendering, invoice generation, and cancel sale.
 */

let lastOrder = null;

function showReceipt(order) {
  lastOrder = order;
  clearCart();

  // Reload session items to get updated sold_qty
  fetchActiveSession().then(s => {
    if (s) { window.session = s; renderGrid(); }
  });

  const box = document.getElementById("receipt-box");
  const now = new Date();
  const dateStr = now.toLocaleDateString("en-IN", { day: "2-digit", month: "short", year: "numeric" });

  const itemsHtml = order.items.map(i => `
    <div class="r-line">
      <span>${esc(i.product_name)} ${esc(i.variant_label)} ×${i.quantity}</span>
      <span>₹${fmt(i.line_total)}</span>
    </div>
  `).join("");

  box.innerHTML = `
    <div class="biz">${esc(window.businessName || "Register")}</div>
    <hr class="divider">
    <div class="r-line"><span>Date</span><span>${dateStr}</span></div>
    <div class="r-line"><span>Order</span><span>#${order.order_id.slice(0,8).toUpperCase()}</span></div>
    <hr class="divider">
    ${itemsHtml}
    <div class="r-line r-total">
      <span>Total</span><span>₹${fmt(order.total_amount)}</span>
    </div>
    <div class="r-line">
      <span>Paid (${esc(order.payment_method)})</span>
      <span>₹${fmt(order.total_amount)}</span>
    </div>
    <div class="thank">Thank you! 🎉</div>
  `;

  showScreen("receipt");
  updateSessionStats();
}

function newSale() {
  lastOrder = null;
  showScreen("sell");
}

async function invoiceOrder() {
  if (!lastOrder) return;
  const btn = document.getElementById("invoice-btn");
  btn.disabled = true;
  btn.textContent = "Generating…";

  // Ask for GST rate
  const taxInput = prompt("Enter GST rate % (0 for no tax):", "0");
  const taxRate = parseFloat(taxInput) || 0;

  try {
    const result = await generateInvoice(lastOrder.order_id, taxRate);
    // Decode base64 PDF and trigger download
    const byteChars = atob(result.data);
    const byteArray = new Uint8Array(byteChars.length);
    for (let i = 0; i < byteChars.length; i++) {
      byteArray[i] = byteChars.charCodeAt(i);
    }
    const blob = new Blob([byteArray], { type: "application/pdf" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = result.filename || "invoice.pdf";
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
    showToast("📄 Invoice downloaded");
  } catch (e) {
    showToast("Invoice error: " + e.message);
  } finally {
    btn.disabled = false;
    btn.textContent = "📄 Invoice";
  }
}

async function cancelSale() {
  if (!lastOrder) return;
  if (!confirm("Cancel this sale? The payment will be voided.")) return;
  try {
    await cancelOrder(lastOrder.order_id);
    showToast("Sale cancelled");
    lastOrder = null;
    // Reload session to restore stock
    const s = await fetchActiveSession();
    if (s) { window.session = s; renderGrid(); }
    showScreen("sell");
  } catch (e) {
    showToast("Could not cancel: " + e.message);
  }
}
