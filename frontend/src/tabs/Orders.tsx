/**
 * Orders — the Orders_Surface (design §"Frontend structure" → `tabs/Orders.tsx`).
 *
 * A made-to-order lifecycle screen backed by the `/api/v1/orders` endpoints:
 *
 *  - **Create (Req 9.1)** — a form capturing a customer identifier, one or more
 *    line items (recipe name, quantity, selling price, optional customization),
 *    and a delivery date. Submitting posts the order, which the Service_Layer
 *    creates with an initial `pending` status. Field-level validation
 *    (Req 9.7, 9.8) is enforced server-side; errors surface inline.
 *  - **Status display (Req 9.2)** — each order renders with a colored status
 *    chip for `pending` / `delivered` / `cancelled`.
 *  - **Deliver / cancel (Req 9.3, 9.4)** — pending orders offer "Mark delivered"
 *    and "Cancel" actions; the Service_Layer enforces the legal transitions
 *    (Req 9.9) and a cancelled order's record is retained.
 *  - **Filters (Req 9.5)** — filter the list by delivery date and by status.
 *  - **Owner-only delete (Req 9.6)** — a delete affordance is rendered only when
 *    the signed-in user holds the Owner role. The backend independently enforces
 *    the permission (Req 5.4, 5.6); the UI gate is a convenience, not the
 *    security boundary.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  useIonRouter,
  useIonViewWillEnter,
  IonBadge,
  IonButton,
  IonButtons,
  IonCard,
  IonCardContent,
  IonCardHeader,
  IonCardSubtitle,
  IonCardTitle,
  IonContent,
  IonDatetime,
  IonDatetimeButton,
  IonHeader,
  IonInput,
  IonItem,
  IonLabel,
  IonList,
  IonListHeader,
  IonModal,
  IonNote,
  IonPage,
  IonSelect,
  IonSelectOption,
  IonSpinner,
  IonText,
  IonTitle,
  IonToolbar,
} from "@ionic/react";
import { TabTour } from "../components/TabTour";
import {
  ApiError,
  customers as customersApi,
  orders as ordersApi,
  recipes as recipesApi,
  type Customer,
  type Money,
  type Order,
  type OrderCreateBody,
  type OrderItemCreate,
  type OrderListQuery,
  type OrderStatus,
  type Recipe,
} from "../api/endpoints";
import { useAuth } from "../auth/AuthContext";

/** A single editable line-item row in the create form. */
interface DraftItem {
  recipe_name: string;
  quantity: string;
  selling_price: string;
  customization_charge: string;
  customization_note: string;
}

/** A fresh, empty line item. */
function emptyItem(): DraftItem {
  return {
    recipe_name: "",
    quantity: "1",
    selling_price: "",
    customization_charge: "",
    customization_note: "",
  };
}

/** The status-filter options, including an "all statuses" sentinel. */
type StatusFilter = OrderStatus | "all";

/** Ionic chip color per order status (Req 9.2). */
const STATUS_COLOR: Record<OrderStatus, string> = {
  pending: "warning",
  delivered: "success",
  cancelled: "medium",
};

export function Orders(): JSX.Element {
  const { role } = useAuth();
  const isOwner = role === "owner";

  const [orders, setOrders] = useState<Order[]>([]);
  const [loading, setLoading] = useState<boolean>(false);
  const [listError, setListError] = useState<string | null>(null);

  // ── Filters (Req 9.5) ──
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("all");
  const [dateFilter, setDateFilter] = useState<string | null>(null);

  // ── Create form (Req 9.1) ──
  const [showCreate, setShowCreate] = useState<boolean>(false);

  /** Build the list query from the active filters (Req 9.5). */
  const listQuery = useMemo<OrderListQuery>(() => {
    const q: OrderListQuery = {};
    if (statusFilter !== "all") q.status = statusFilter;
    if (dateFilter) q.delivery_date = dateFilter;
    return q;
  }, [statusFilter, dateFilter]);

  const refresh = useCallback(async () => {
    setLoading(true);
    setListError(null);
    try {
      const rows = await ordersApi.list(listQuery);
      setOrders(rows);
    } catch (err) {
      setListError(messageFor(err, "Could not load orders."));
    } finally {
      setLoading(false);
    }
  }, [listQuery]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  // Ionic keeps tab components mounted, so also reload whenever the Orders tab
  // becomes active — this reflects orders (and customers) created elsewhere,
  // e.g. after navigating away to add a customer/recipe and coming back.
  useIonViewWillEnter(() => {
    void refresh();
  });

  // ── Lifecycle actions (Req 9.3, 9.4, 9.6) ──
  const [actingId, setActingId] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  const runAction = useCallback(
    async (orderId: string, fn: () => Promise<unknown>) => {
      setActingId(orderId);
      setActionError(null);
      try {
        await fn();
        await refresh();
      } catch (err) {
        setActionError(messageFor(err, "The action could not be completed."));
      } finally {
        setActingId(null);
      }
    },
    [refresh]
  );

  const deliver = (id: string) => runAction(id, () => ordersApi.deliver(id)); // Req 9.3
  const cancel = (id: string) => runAction(id, () => ordersApi.cancel(id)); // Req 9.4
  const remove = (id: string) => runAction(id, () => ordersApi.remove(id)); // Req 9.6

  return (
    <IonPage>
      <TabTour
        tabKey="orders"
        title="Orders"
        intro="Create and track made-to-order jobs."
        points={[
          "Add a customer, items, quantities and a delivery date.",
          "Tap Estimate cost to price an item from its recipe.",
          "Mark orders delivered or cancel them — records are kept.",
          "Filter by date or status to plan your production.",
        ]}
      />
      <IonHeader>
        <IonToolbar>
          <IonTitle>Orders</IonTitle>
          <IonButtons slot="end">
            <IonButton onClick={() => setShowCreate(true)}>New order</IonButton>
          </IonButtons>
        </IonToolbar>
      </IonHeader>

      <IonContent className="ion-padding">
        {/* ── Filters (Req 9.5) ── */}
        <IonList>
          <IonItem>
            <IonSelect
              label="Status"
              value={statusFilter}
              onIonChange={(e) => setStatusFilter(e.detail.value as StatusFilter)}
              interface="popover"
            >
              <IonSelectOption value="all">All statuses</IonSelectOption>
              <IonSelectOption value="pending">Pending</IonSelectOption>
              <IonSelectOption value="delivered">Delivered</IonSelectOption>
              <IonSelectOption value="cancelled">Cancelled</IonSelectOption>
            </IonSelect>
          </IonItem>
          <IonItem>
            <IonLabel>Delivery date</IonLabel>
            {dateFilter ? (
              <>
                <IonDatetimeButton datetime="orders-date-filter" slot="end" />
                <IonButton
                  slot="end"
                  fill="clear"
                  size="small"
                  onClick={() => setDateFilter(null)}
                >
                  Clear
                </IonButton>
                <IonModal keepContentsMounted>
                  <IonDatetime
                    id="orders-date-filter"
                    presentation="date"
                    value={dateFilter}
                    onIonChange={(e) =>
                      setDateFilter(isoDateOf(e.detail.value as string | null))
                    }
                  />
                </IonModal>
              </>
            ) : (
              <IonButton
                slot="end"
                fill="outline"
                size="small"
                onClick={() => setDateFilter(todayIso())}
              >
                Filter by date
              </IonButton>
            )}
          </IonItem>
        </IonList>

        {actionError ? (
          <IonNote color="danger" style={{ display: "block", margin: "8px 0" }}>
            {actionError}
          </IonNote>
        ) : null}

        {/* ── Orders list (Req 9.2) ── */}
        {loading ? (
          <div style={{ textAlign: "center", marginTop: 24 }}>
            <IonSpinner />
          </div>
        ) : listError ? (
          <IonNote color="danger" style={{ display: "block", textAlign: "center", marginTop: 24 }}>
            {listError}
          </IonNote>
        ) : orders.length === 0 ? (
          <IonText color="medium">
            <p style={{ textAlign: "center", marginTop: 24 }}>No orders match these filters.</p>
          </IonText>
        ) : (
          orders.map((order) => (
            <OrderCard
              key={order.order_id}
              order={order}
              isOwner={isOwner}
              busy={actingId === order.order_id}
              onDeliver={() => deliver(order.order_id)}
              onCancel={() => cancel(order.order_id)}
              onDelete={() => remove(order.order_id)}
            />
          ))
        )}
      </IonContent>

      <CreateOrderModal
        isOpen={showCreate}
        isOwner={isOwner}
        onDismiss={() => setShowCreate(false)}
        onCreated={() => {
          setShowCreate(false);
          void refresh();
        }}
      />
    </IonPage>
  );
}

interface OrderCardProps {
  order: Order;
  isOwner: boolean;
  busy: boolean;
  onDeliver: () => void;
  onCancel: () => void;
  onDelete: () => void;
}

/** One order rendered as a card with a status chip and lifecycle actions. */
function OrderCard({
  order,
  isOwner,
  busy,
  onDeliver,
  onCancel,
  onDelete,
}: OrderCardProps): JSX.Element {
  const isPending = order.status === "pending";
  return (
    <IonCard>
      <IonCardHeader>
        <IonCardSubtitle>
          {/* Status chip (Req 9.2). */}
          <IonBadge color={STATUS_COLOR[order.status]}>{order.status}</IonBadge>{" "}
          Delivery {order.delivery_date}
        </IonCardSubtitle>
        <IonCardTitle style={{ fontSize: "1rem" }}>
          Order {shortId(order.order_id)}
        </IonCardTitle>
      </IonCardHeader>
      <IonCardContent>
        <IonList>
          {order.items.map((item) => (
            <IonItem key={item.order_item_id} lines="none">
              <IonLabel>
                <h3>
                  {item.recipe_name} × {item.quantity}
                </h3>
                <IonNote>
                  {formatMoney(item.selling_price)}
                  {Number(item.customization_charge) > 0
                    ? ` (+${formatMoney(item.customization_charge)})`
                    : ""}
                  {item.customization_note ? ` — ${item.customization_note}` : ""}
                </IonNote>
              </IonLabel>
            </IonItem>
          ))}
        </IonList>

        {order.delivery_address ? (
          <IonNote color="medium" style={{ display: "block", marginTop: 4 }}>
            {order.delivery_address}
          </IonNote>
        ) : null}

        <div style={{ display: "flex", gap: 8, marginTop: 12, flexWrap: "wrap" }}>
          {/* Deliver / cancel only apply to pending orders (Req 9.3, 9.4, 9.9). */}
          {isPending ? (
            <>
              <IonButton size="small" disabled={busy} onClick={onDeliver}>
                Mark delivered
              </IonButton>
              <IonButton size="small" fill="outline" color="medium" disabled={busy} onClick={onCancel}>
                Cancel
              </IonButton>
            </>
          ) : null}

          {/* Owner-only delete affordance (Req 9.6). */}
          {isOwner ? (
            <IonButton size="small" fill="clear" color="danger" disabled={busy} onClick={onDelete}>
              Delete
            </IonButton>
          ) : null}

          {busy ? <IonSpinner name="dots" /> : null}
        </div>
      </IonCardContent>
    </IonCard>
  );
}

interface CreateOrderModalProps {
  isOpen: boolean;
  isOwner: boolean;
  onDismiss: () => void;
  onCreated: () => void;
}

/** Per-row cost-estimate state (Owner-only, interconnect #2). */
type EstimateState =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "none"; name: string }
  | { kind: "choose"; matches: Recipe[] }
  | { kind: "done"; unitCost: number; quantity: number; total: number }
  | { kind: "error"; message: string };

/** The create-order form (Req 9.1) presented in a modal. */
function CreateOrderModal({
  isOpen,
  isOwner,
  onDismiss,
  onCreated,
}: CreateOrderModalProps): JSX.Element {
  const router = useIonRouter();

  const [customer, setCustomer] = useState<string>("");
  const [deliveryDate, setDeliveryDate] = useState<string>(todayIso());
  const [deliveryAddress, setDeliveryAddress] = useState<string>("");
  const [items, setItems] = useState<DraftItem[]>([emptyItem()]);
  const [submitting, setSubmitting] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);

  // ── Client-side customer resolution (interconnect #3) ──
  const [customerNotFound, setCustomerNotFound] = useState<boolean>(false);
  const [customerMatches, setCustomerMatches] = useState<Customer[] | null>(null);

  // ── Estimate-cost support (interconnect #2, Owner only) ──
  // Recipes are loaded lazily and cached for the life of the open modal.
  const [recipeCache, setRecipeCache] = useState<Recipe[] | null>(null);
  const [estimates, setEstimates] = useState<Record<number, EstimateState>>({});

  // Reset the form each time the modal opens.
  useEffect(() => {
    if (isOpen) {
      setCustomer("");
      setDeliveryDate(todayIso());
      setDeliveryAddress("");
      setItems([emptyItem()]);
      setSubmitting(false);
      setError(null);
      setCustomerNotFound(false);
      setCustomerMatches(null);
      setRecipeCache(null);
      setEstimates({});
    }
  }, [isOpen]);

  const setEstimate = (index: number, state: EstimateState) =>
    setEstimates((prev) => ({ ...prev, [index]: state }));

  const updateItem = (index: number, patch: Partial<DraftItem>) => {
    setItems((prev) => prev.map((it, i) => (i === index ? { ...it, ...patch } : it)));
    // Editing a row invalidates any prior estimate for it.
    setEstimates((prev) => {
      if (!(index in prev)) return prev;
      const next = { ...prev };
      delete next[index];
      return next;
    });
  };
  const addItem = () => setItems((prev) => [...prev, emptyItem()]);
  const removeItem = (index: number) =>
    setItems((prev) => (prev.length > 1 ? prev.filter((_, i) => i !== index) : prev));

  /** Load recipes once and cache them within the modal. */
  const loadRecipes = useCallback(async (): Promise<Recipe[]> => {
    if (recipeCache) return recipeCache;
    const rows = await recipesApi.list();
    setRecipeCache(rows);
    return rows;
  }, [recipeCache]);

  /** Compute + display the estimate for a resolved single recipe (interconnect #2). */
  const computeEstimate = useCallback(
    async (index: number, recipe: Recipe, quantity: number) => {
      setEstimate(index, { kind: "loading" });
      try {
        const cost = await recipesApi.getCost(recipe.recipe_id);
        const unitCost = Number(cost.unit_cost);
        // The recipe's unit_cost already accounts for yield, so multiply by the
        // ordered quantity only.
        const total = unitCost * quantity;
        setEstimate(index, { kind: "done", unitCost, quantity, total });
      } catch (err) {
        if (err instanceof ApiError && err.status === 403) {
          setEstimate(index, {
            kind: "error",
            message: "Cost estimates are available to the owner only.",
          });
          return;
        }
        // e.g. unresolved component costs — surface the server's message.
        setEstimate(index, {
          kind: "error",
          message: messageFor(err, "Could not estimate the cost of this item."),
        });
      }
    },
    []
  );

  /** Resolve a row's recipe by name and estimate its cost (interconnect #2). */
  const estimateRow = useCallback(
    async (index: number) => {
      const item = items[index];
      const name = item.recipe_name.trim();
      const quantity = Number(item.quantity);
      if (!name) {
        setEstimate(index, { kind: "error", message: "Enter an item name first." });
        return;
      }
      setEstimate(index, { kind: "loading" });
      let recipes: Recipe[];
      try {
        recipes = await loadRecipes();
      } catch (err) {
        setEstimate(index, {
          kind: "error",
          message: messageFor(err, "Could not load recipes."),
        });
        return;
      }

      const lower = name.toLowerCase();
      // Case-insensitive EXACT match first; otherwise case-insensitive SUBSTRING.
      const exact = recipes.filter((r) => r.name.toLowerCase() === lower);
      const matches =
        exact.length > 0
          ? exact
          : recipes.filter((r) => r.name.toLowerCase().includes(lower));

      if (matches.length === 0) {
        setEstimate(index, { kind: "none", name });
      } else if (matches.length === 1) {
        await computeEstimate(index, matches[0], quantity);
      } else {
        setEstimate(index, { kind: "choose", matches });
      }
    },
    [items, loadRecipes, computeEstimate]
  );

  /** Navigate to the Recipes tab to create a recipe, prefilling the name (interconnect #4). */
  const goAddRecipe = useCallback(
    (name: string) => {
      onDismiss();
      router.push(`/app/recipes?new=${encodeURIComponent(name)}`);
    },
    [onDismiss, router]
  );

  /** Navigate to the Customers tab to create a customer, prefilling the name (interconnect #4). */
  const goAddCustomer = useCallback(() => {
    const name = customer.trim();
    onDismiss();
    router.push(`/app/customers?new=${encodeURIComponent(name)}`);
  }, [customer, onDismiss, router]);

  /** Post the order; server enforces the authoritative validation (Req 9.7, 9.8). */
  const doCreate = useCallback(
    async (customerIdentifier: string) => {
      setError(null);
      const body: OrderCreateBody = {
        customer_identifier: customerIdentifier,
        delivery_date: deliveryDate,
        delivery_address: deliveryAddress.trim() || null,
        items: items.map(toOrderItem),
      };
      setSubmitting(true);
      try {
        await ordersApi.create(body); // Req 9.1 — created as pending
        onCreated();
      } catch (err) {
        // Keep handling the server error path gracefully if create still fails.
        setError(messageFor(err, "The order could not be created."));
      } finally {
        setSubmitting(false);
      }
    },
    [deliveryDate, deliveryAddress, items, onCreated]
  );

  /**
   * Resolve the customer client-side before creating (interconnect #3):
   *  - 0 results → don't submit; offer to add a new customer.
   *  - exactly 1 → proceed with create.
   *  - >1 → ask the owner to pick; resolve uniquely by the chosen phone.
   * A failed search falls back to server-side resolution.
   */
  const submit = useCallback(async () => {
    setError(null);
    setCustomerNotFound(false);
    setCustomerMatches(null);
    const identifier = customer.trim();
    if (!identifier) {
      setError("Enter a customer name or phone number.");
      return;
    }

    setSubmitting(true);
    let matches: Customer[] | null = null;
    try {
      matches = await customersApi.search(identifier);
    } catch {
      matches = null; // search unavailable — fall back to server-side resolution
    }

    if (matches) {
      if (matches.length === 0) {
        setCustomerNotFound(true);
        setSubmitting(false);
        return;
      }
      if (matches.length > 1) {
        setCustomerMatches(matches);
        setSubmitting(false);
        return;
      }
      // exactly one — proceed as today with the typed identifier.
      await doCreate(identifier);
      return;
    }
    await doCreate(identifier);
  }, [customer, doCreate]);

  return (
    <IonModal isOpen={isOpen} onDidDismiss={onDismiss}>
      <IonHeader>
        <IonToolbar>
          <IonTitle>New order</IonTitle>
          <IonButtons slot="end">
            <IonButton onClick={onDismiss}>Close</IonButton>
          </IonButtons>
        </IonToolbar>
      </IonHeader>

      <IonContent className="ion-padding">
        <IonList>
          <IonItem>
            <IonInput
              label="Customer (name or phone)"
              labelPlacement="stacked"
              value={customer}
              onIonInput={(e) => {
                setCustomer(e.detail.value ?? "");
                setCustomerNotFound(false);
                setCustomerMatches(null);
              }}
              placeholder="e.g. Priya or 9876543210"
            />
          </IonItem>

          <IonItem>
            <IonLabel>Delivery date</IonLabel>
            <IonDatetimeButton datetime="orders-create-date" slot="end" />
            <IonModal keepContentsMounted>
              <IonDatetime
                id="orders-create-date"
                presentation="date"
                value={deliveryDate}
                onIonChange={(e) =>
                  setDeliveryDate(isoDateOf(e.detail.value as string | null) ?? todayIso())
                }
              />
            </IonModal>
          </IonItem>

          <IonItem>
            <IonInput
              label="Delivery address (optional)"
              labelPlacement="stacked"
              value={deliveryAddress}
              onIonInput={(e) => setDeliveryAddress(e.detail.value ?? "")}
            />
          </IonItem>
        </IonList>

        {/* Customer-not-found interconnect (#3): don't submit, offer to add one. */}
        {customerNotFound ? (
          <div style={{ margin: "8px 0" }}>
            <IonNote color="danger" style={{ display: "block" }}>
              No customer found matching "{customer.trim()}".
            </IonNote>
            <IonButton size="small" onClick={goAddCustomer}>
              Add new customer
            </IonButton>
          </div>
        ) : null}

        {/* Multiple-customer interconnect (#3): let the owner pick one. */}
        {customerMatches ? (
          <div style={{ margin: "8px 0" }}>
            <IonNote color="medium" style={{ display: "block" }}>
              Multiple customers match "{customer.trim()}". Pick one:
            </IonNote>
            <IonList>
              {customerMatches.map((c) => (
                <IonItem key={c.customer_id} button detail onClick={() => void doCreate(c.phone)}>
                  <IonLabel>
                    <h3>{c.name}</h3>
                    <IonNote>{c.phone}</IonNote>
                  </IonLabel>
                </IonItem>
              ))}
            </IonList>
          </div>
        ) : null}

        <IonList>
          <IonListHeader>
            <IonLabel>Items</IonLabel>
            <IonButton size="small" onClick={addItem}>
              Add item
            </IonButton>
          </IonListHeader>

          {items.map((item, index) => (
            <div key={index} style={{ marginBottom: 8 }}>
              <IonItem>
                <IonInput
                  label="Item"
                  labelPlacement="stacked"
                  value={item.recipe_name}
                  onIonInput={(e) => updateItem(index, { recipe_name: e.detail.value ?? "" })}
                  placeholder="Recipe / product name"
                />
              </IonItem>
              <IonItem>
                <IonInput
                  label="Quantity"
                  labelPlacement="stacked"
                  type="number"
                  inputmode="numeric"
                  min={1}
                  max={999999}
                  value={item.quantity}
                  onIonInput={(e) => updateItem(index, { quantity: e.detail.value ?? "" })}
                />
                <IonInput
                  label="Unit price"
                  labelPlacement="stacked"
                  type="number"
                  inputmode="decimal"
                  min={0}
                  value={item.selling_price}
                  onIonInput={(e) => updateItem(index, { selling_price: e.detail.value ?? "" })}
                />
              </IonItem>
              <IonItem>
                <IonInput
                  label="Customization charge (optional)"
                  labelPlacement="stacked"
                  type="number"
                  inputmode="decimal"
                  min={0}
                  value={item.customization_charge}
                  onIonInput={(e) => updateItem(index, { customization_charge: e.detail.value ?? "" })}
                />
              </IonItem>
              <IonItem>
                <IonInput
                  label="Customization note (optional)"
                  labelPlacement="stacked"
                  value={item.customization_note}
                  onIonInput={(e) => updateItem(index, { customization_note: e.detail.value ?? "" })}
                />
                {items.length > 1 ? (
                  <IonButton
                    slot="end"
                    fill="clear"
                    color="danger"
                    size="small"
                    onClick={() => removeItem(index)}
                  >
                    Remove
                  </IonButton>
                ) : null}
              </IonItem>

              {/* Estimate cost is Owner-only (recipes.getCost is Owner-only). */}
              {isOwner ? (
                <EstimateRow
                  state={estimates[index] ?? { kind: "idle" }}
                  onEstimate={() => void estimateRow(index)}
                  onPick={(recipe) => void computeEstimate(index, recipe, Number(item.quantity))}
                  onAddRecipe={(name) => goAddRecipe(name)}
                />
              ) : null}
            </div>
          ))}
        </IonList>

        {error ? (
          <IonNote color="danger" style={{ display: "block", margin: "8px 0" }}>
            {error}
          </IonNote>
        ) : null}

        <IonButton expand="block" disabled={submitting} onClick={() => void submit()}>
          {submitting ? <IonSpinner name="dots" /> : "Create order"}
        </IonButton>
      </IonContent>
    </IonModal>
  );
}

interface EstimateRowProps {
  state: EstimateState;
  onEstimate: () => void;
  onPick: (recipe: Recipe) => void;
  onAddRecipe: (name: string) => void;
}

/** The per-row cost-estimate affordance + result (Owner only, interconnect #2). */
function EstimateRow({ state, onEstimate, onPick, onAddRecipe }: EstimateRowProps): JSX.Element {
  return (
    <div style={{ padding: "4px 16px 8px" }}>
      <IonButton
        size="small"
        fill="outline"
        disabled={state.kind === "loading"}
        onClick={onEstimate}
      >
        {state.kind === "loading" ? <IonSpinner name="dots" /> : "Estimate cost"}
      </IonButton>

      {state.kind === "done" ? (
        <IonNote color="medium" style={{ display: "block", marginTop: 4 }}>
          Estimated cost: {formatMoney(state.total)} ({formatMoney(state.unitCost)}/unit ×{" "}
          {state.quantity})
        </IonNote>
      ) : null}

      {state.kind === "error" ? (
        <IonNote color="danger" style={{ display: "block", marginTop: 4 }}>
          {state.message}
        </IonNote>
      ) : null}

      {state.kind === "none" ? (
        <div style={{ marginTop: 4 }}>
          <IonNote color="danger" style={{ display: "block" }}>
            No recipe found for "{state.name}".
          </IonNote>
          <IonButton size="small" fill="clear" onClick={() => onAddRecipe(state.name)}>
            Add recipe
          </IonButton>
        </div>
      ) : null}

      {state.kind === "choose" ? (
        <div style={{ marginTop: 4 }}>
          <IonNote color="medium" style={{ display: "block" }}>
            Multiple recipes match. Pick one:
          </IonNote>
          <IonList>
            {state.matches.map((r) => (
              <IonItem key={r.recipe_id} button detail onClick={() => onPick(r)}>
                <IonLabel>{r.name}</IonLabel>
              </IonItem>
            ))}
          </IonList>
        </div>
      ) : null}
    </div>
  );
}

/** Convert a draft line item into the API payload shape. */
function toOrderItem(draft: DraftItem): OrderItemCreate {
  const item: OrderItemCreate = {
    recipe_name: draft.recipe_name.trim(),
    quantity: Number(draft.quantity),
    selling_price: draft.selling_price.trim(),
  };
  if (draft.customization_charge.trim()) {
    item.customization_charge = draft.customization_charge.trim();
  }
  if (draft.customization_note.trim()) {
    item.customization_note = draft.customization_note.trim();
  }
  return item;
}

/** Format a monetary value (INR) for display. */
function formatMoney(value: Money): string {
  const n = Number(value);
  if (Number.isNaN(n)) return String(value);
  return `₹${n.toFixed(2)}`;
}

/** Today's date as an ISO `YYYY-MM-DD` string. */
function todayIso(): string {
  return new Date().toISOString().slice(0, 10);
}

/** Reduce an ISO date/date-time string to its `YYYY-MM-DD` date part. */
function isoDateOf(value: string | null): string | null {
  if (!value) return null;
  return value.slice(0, 10);
}

/** A short, readable fragment of a UUID for card headings. */
function shortId(id: string): string {
  return id.slice(0, 8);
}

/** Extract a user-facing message from an unknown thrown value. */
function messageFor(err: unknown, fallback: string): string {
  if (err instanceof ApiError) return err.message;
  if (err instanceof Error && err.message) return err.message;
  return fallback;
}

export default Orders;
