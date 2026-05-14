/**
 * cart.js — cart state and product grid rendering.
 */

let cart = {}; // { variant_id: quantity }

function changeQty(variantId, delta) {
  const item = window.session.items.find(i => i.variant_id === variantId);
  if (!item) return;

  const current = cart[variantId] || 0;
  const next = Math.max(0, current + delta);

  if (delta > 0 && item.remaining !== null && next > item.remaining) return;

  if (next === 0) delete cart[variantId];
  else cart[variantId] = next;

  document.getElementById(`qty-${variantId}`).textContent = next;
  updateCartBar();
}

function updateCartBar() {
  const bar = document.getElementById("cart-bar");
  const totalQty = Object.values(cart).reduce((a, b) => a + b, 0);
  const totalAmt = Object.entries(cart).reduce((sum, [vid, qty]) => {
    const item = window.session.items.find(i => i.variant_id === vid);
    return sum + (item ? item.booth_price * qty : 0);
  }, 0);

  if (totalQty === 0) { bar.classList.add("hidden"); return; }
  bar.classList.remove("hidden");
  document.getElementById("cart-summary").innerHTML =
    `${totalQty} item${totalQty > 1 ? "s" : ""} · <strong>₹${fmt(totalAmt)}</strong>`;
}

function clearCart() {
  cart = {};
  updateCartBar();
}

function renderGrid() {
  const grid = document.getElementById("product-grid");
  grid.innerHTML = "";

  const grouped = {};
  for (const item of window.session.items) {
    if (!grouped[item.product_name]) grouped[item.product_name] = [];
    grouped[item.product_name].push(item);
  }

  for (const [productName, variants] of Object.entries(grouped).sort()) {
    for (const item of variants.sort((a, b) => a.variant_label.localeCompare(b.variant_label))) {
      const soldOut = item.remaining !== null && item.remaining <= 0;
      const qty = cart[item.variant_id] || 0;

      const card = document.createElement("div");
      card.className = "product-card" + (soldOut ? " sold-out" : "");
      card.id = `card-${item.variant_id}`;
      card.innerHTML = `
        <div class="name">${esc(productName)}</div>
        <div class="variant">${esc(item.variant_label)}</div>
        <div class="price">₹${fmt(item.booth_price)}</div>
        ${soldOut
          ? `<div class="sold-out-badge">Sold Out</div>`
          : `<div class="stock">${item.remaining === null ? "∞" : item.remaining + " left"}</div>
             <div class="qty-row">
               <button class="qty-btn" onclick="changeQty('${item.variant_id}', -1)">−</button>
               <span class="qty-val" id="qty-${item.variant_id}">${qty}</span>
               <button class="qty-btn" onclick="changeQty('${item.variant_id}', 1)">+</button>
             </div>`
        }
      `;
      grid.appendChild(card);
    }
  }
}
