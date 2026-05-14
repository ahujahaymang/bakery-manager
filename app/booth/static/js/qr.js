/**
 * qr.js — Razorpay QR overlay and payment polling.
 */

let qrPollTimer = null;
let qrExpireTimer = null;

function showQR(order) {
  document.getElementById("qr-amount").textContent = `₹${fmt(order.total_amount)}`;
  document.getElementById("qr-img").src = order.razorpay_qr_url;
  document.getElementById("qr-expired-msg").style.display = "none";
  document.getElementById("qr-status-text").textContent = "Waiting for payment…";
  document.getElementById("qr-spinner").style.display = "block";
  document.getElementById("qr-overlay").classList.remove("hidden");

  qrPollTimer = setInterval(() => pollPaymentStatus(order), 2000);
  qrExpireTimer = setTimeout(() => {
    clearInterval(qrPollTimer);
    document.getElementById("qr-expired-msg").style.display = "block";
    document.getElementById("qr-status-text").textContent = "";
    document.getElementById("qr-spinner").style.display = "none";
  }, 15 * 60 * 1000);
}

async function pollPaymentStatus(order) {
  try {
    const status = await fetchPaymentStatus(order.order_id);
    if (status === "completed") {
      clearInterval(qrPollTimer);
      clearTimeout(qrExpireTimer);
      document.getElementById("qr-overlay").classList.add("hidden");
      showReceipt(order);
    }
  } catch (e) { /* ignore network blips */ }
}

function cancelQR() {
  clearInterval(qrPollTimer);
  clearTimeout(qrExpireTimer);
  document.getElementById("qr-overlay").classList.add("hidden");
}

async function retryQR() {
  await confirmCheckout();
}
