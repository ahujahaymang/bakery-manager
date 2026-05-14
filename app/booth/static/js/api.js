/**
 * api.js — all fetch calls to the booth backend.
 * All functions return parsed JSON or throw on error.
 */

const API = `/booth/${TENANT_ID}/api`;

async function apiGet(path) {
  const res = await fetch(`${API}${path}`);
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || `HTTP ${res.status}`);
  }
  return res.json();
}

async function apiPost(path, body) {
  const res = await fetch(`${API}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || `HTTP ${res.status}`);
  }
  return res.json();
}

async function fetchActiveSession() {
  const data = await apiGet("/session/active");
  return data.session;
}

async function fetchCatalog() {
  return apiGet("/catalog");
}

async function submitCheckout(sessionId, items, paymentMethod, customerName) {
  return apiPost("/checkout", {
    session_id: sessionId,
    items,
    payment_method: paymentMethod,
    customer_name: customerName || null,
  });
}

async function fetchPaymentStatus(orderId) {
  const data = await apiGet(`/checkout/status/${orderId}`);
  return data.status;
}

async function cancelOrder(orderId) {
  return apiPost(`/cancel/${orderId}`, {});
}

async function createBoothSession(name, items) {
  return apiPost("/session/create", { name, items });
}

async function endBoothSession() {
  return apiPost("/session/end", {});
}
