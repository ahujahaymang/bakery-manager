/**
 * Inventory tab — the Inventory_Surface (Req 10.5, 10.6, 10.7).
 *
 * Responsibilities:
 *  - **Category-grouped list** — load items via `inventory.list()` and render
 *    them grouped by category. The backend already returns categories ordered
 *    alphabetically ascending and items within each category ordered
 *    alphabetically ascending by name (Req 10.5); this surface renders that
 *    grouped response as delivered.
 *  - **Empty state** — while no inventory items exist, show an empty-state
 *    indication and render no category group at all (Req 10.6).
 *  - **Create / update forms** — a create form for new items and a per-item
 *    update form for quantity and/or cost, routed through `inventory.create`
 *    and `inventory.update`.
 *  - **Role-aware cost** — the cost value of every item is hidden from Staff
 *    (Req 10.7). The backend omits `cost_per_unit` from the payload for Staff
 *    principals; this surface additionally never renders a cost when the role
 *    is Staff or when the value is absent, and hides the cost inputs for Staff.
 *
 * Server-side validation (field ranges, existence) is authoritative (Req 10.2,
 * 10.4); the light client checks here are for fast feedback only.
 */

import { useCallback, useEffect, useMemo, useState, type ChangeEvent } from "react";
import {
  IonButton,
  IonButtons,
  IonContent,
  IonHeader,
  IonIcon,
  IonInput,
  IonItem,
  IonItemDivider,
  IonItemGroup,
  IonLabel,
  IonList,
  IonListHeader,
  IonModal,
  IonNote,
  IonPage,
  IonSpinner,
  IonText,
  IonTitle,
  IonToolbar,
} from "@ionic/react";
import { TabTour } from "../components/TabTour";
import { cameraOutline } from "ionicons/icons";
import {
  ApiError,
  ingestion,
  inventory,
  type InventoryCategoryGroup,
  type InventoryItem,
  type Money,
} from "../api/endpoints";
import { useAuth } from "../auth/AuthContext";

/** Coerce a backend {@link Money} value (number | string) to a display string. */
function formatMoney(value: Money | undefined | null): string {
  if (value === undefined || value === null) return "";
  const n = Number(value);
  return Number.isFinite(n) ? n.toFixed(2) : String(value);
}

/** Extract a user-facing message from an unknown thrown value. */
function messageFor(err: unknown, fallback: string): string {
  if (err instanceof ApiError) return err.message;
  if (err instanceof Error && err.message) return err.message;
  return fallback;
}

export function Inventory(): JSX.Element {
  const { role } = useAuth();
  // Staff never sees cost values or cost inputs (Req 10.7). The backend also
  // omits `cost_per_unit` for Staff, so we treat either signal as "hide cost".
  const isStaff = role === "staff";

  const [groups, setGroups] = useState<InventoryCategoryGroup[]>([]);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);

  // Form state: either creating a new item or editing an existing one.
  const [createOpen, setCreateOpen] = useState<boolean>(false);
  const [editing, setEditing] = useState<InventoryItem | null>(null);
  // Scan/upload-a-receipt-into-inventory flow (Req 15.6). Owner only — cost is
  // integral to the flow, so it is not offered to Staff.
  const [scanOpen, setScanOpen] = useState<boolean>(false);

  const load = useCallback(async (): Promise<void> => {
    setLoading(true);
    setError(null);
    try {
      const res = await inventory.list();
      setGroups(res.categories ?? []);
    } catch (err) {
      setError(messageFor(err, "We couldn't load your inventory. Please try again."));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  // No items exist when there are no category groups (the backend omits empty
  // categories). In that case show an empty state and render no group (Req 10.6).
  const isEmpty = useMemo(
    () => groups.every((g) => g.items.length === 0) || groups.length === 0,
    [groups]
  );

  const handleSaved = useCallback(async (): Promise<void> => {
    setCreateOpen(false);
    setEditing(null);
    await load();
  }, [load]);

  return (
    <IonPage>
      <TabTour
        tabKey="inventory"
        title="Inventory"
        intro="Track ingredients and packaging."
        points={[
          "Add items manually, or scan a purchase receipt to auto-fill them.",
          "Update quantities as you use stock or restock.",
          "Items are grouped by category for quick scanning.",
        ]}
      />
      <IonHeader>
        <IonToolbar>
          <IonTitle>Inventory</IonTitle>
          <IonButtons slot="end">
            {/* Scan/upload a purchase receipt to add stock (Req 15.6). Owner
                only — the flow captures cost per unit. */}
            {!isStaff && (
              <IonButton onClick={() => setScanOpen(true)}>
                <IonIcon slot="start" icon={cameraOutline} />
                Scan receipt
              </IonButton>
            )}
            <IonButton onClick={() => setCreateOpen(true)}>Add item</IonButton>
          </IonButtons>
        </IonToolbar>
      </IonHeader>

      <IonContent>
        {loading ? (
          <div style={{ display: "flex", justifyContent: "center", padding: "2rem" }}>
            <IonSpinner name="dots" />
          </div>
        ) : error ? (
          <IonList>
            <IonItem lines="none">
              <IonText color="danger">{error}</IonText>
            </IonItem>
            <IonButton expand="block" fill="clear" onClick={() => void load()}>
              Retry
            </IonButton>
          </IonList>
        ) : isEmpty ? (
          // Empty-state indication; no category group is rendered (Req 10.6).
          <div style={{ textAlign: "center", padding: "3rem 1.5rem" }}>
            <IonText color="medium">
              <p>No inventory items yet.</p>
              <p>Add your first ingredient or packaging item to get started.</p>
            </IonText>
            <IonButton onClick={() => setCreateOpen(true)}>Add item</IonButton>
          </div>
        ) : (
          // Category-grouped list, rendered in the backend's alphabetical order
          // for categories and for items within each category (Req 10.5).
          <IonList>
            {groups
              .filter((group) => group.items.length > 0)
              .map((group) => (
                <IonItemGroup key={group.category}>
                  <IonItemDivider>
                    <IonLabel>{group.category}</IonLabel>
                  </IonItemDivider>
                  {group.items.map((item) => (
                    <IonItem
                      key={item.item_id}
                      button
                      detail
                      onClick={() => setEditing(item)}
                    >
                      <IonLabel>
                        <h2>{item.name}</h2>
                        <p>
                          {formatMoney(item.quantity)} {item.unit}
                          {/* Cost is shown only when the value is present and the
                              signed-in user is not Staff (Req 10.7). */}
                          {!isStaff && item.cost_per_unit !== undefined && (
                            <> · ₹{formatMoney(item.cost_per_unit)}/{item.unit}</>
                          )}
                        </p>
                      </IonLabel>
                    </IonItem>
                  ))}
                </IonItemGroup>
              ))}
          </IonList>
        )}

        {/* Create form */}
        <IonModal isOpen={createOpen} onDidDismiss={() => setCreateOpen(false)}>
          <CreateItemForm
            hideCost={isStaff}
            onCancel={() => setCreateOpen(false)}
            onSaved={handleSaved}
          />
        </IonModal>

        {/* Update (quantity / cost) form */}
        <IonModal isOpen={editing !== null} onDidDismiss={() => setEditing(null)}>
          {editing && (
            <UpdateItemForm
              item={editing}
              hideCost={isStaff}
              onCancel={() => setEditing(null)}
              onSaved={handleSaved}
            />
          )}
        </IonModal>

        {/* Scan/upload a receipt → confirm-and-edit → add stock (Req 15.6) */}
        <IonModal isOpen={scanOpen} onDidDismiss={() => setScanOpen(false)}>
          {scanOpen && (
            <ScanReceiptForm onCancel={() => setScanOpen(false)} onSaved={handleSaved} />
          )}
        </IonModal>
      </IonContent>
    </IonPage>
  );
}

// =============================================================================
// Create form
// =============================================================================

interface CreateItemFormProps {
  /** Hide the cost input for Staff (Req 10.7). */
  hideCost: boolean;
  onCancel: () => void;
  onSaved: () => void | Promise<void>;
}

function CreateItemForm({ hideCost, onCancel, onSaved }: CreateItemFormProps): JSX.Element {
  const [name, setName] = useState<string>("");
  const [category, setCategory] = useState<string>("");
  const [quantity, setQuantity] = useState<string>("");
  const [unit, setUnit] = useState<string>("");
  const [cost, setCost] = useState<string>("");
  const [busy, setBusy] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async (): Promise<void> => {
    setError(null);
    if (!name.trim() || !category.trim() || !unit.trim() || quantity.trim() === "") {
      setError("Name, category, quantity, and unit are required.");
      return;
    }
    setBusy(true);
    try {
      await inventory.create({
        name: name.trim(),
        category: category.trim(),
        quantity: quantity.trim(),
        unit: unit.trim(),
        // Staff never enters cost; the backend requires the field, so send 0.
        cost_per_unit: hideCost ? "0" : cost.trim() === "" ? "0" : cost.trim(),
      });
      await onSaved();
    } catch (err) {
      setError(messageFor(err, "We couldn't create that item. Please try again."));
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <IonHeader>
        <IonToolbar>
          <IonTitle>Add item</IonTitle>
          <IonButtons slot="end">
            <IonButton onClick={onCancel}>Cancel</IonButton>
          </IonButtons>
        </IonToolbar>
      </IonHeader>
      <IonContent>
        <IonList>
          <IonItem>
            <IonLabel position="stacked">Name</IonLabel>
            <IonInput
              value={name}
              placeholder="e.g. All-purpose flour"
              onIonInput={(e) => setName(e.detail.value ?? "")}
            />
          </IonItem>
          <IonItem>
            <IonLabel position="stacked">Category</IonLabel>
            <IonInput
              value={category}
              placeholder="e.g. Dry goods"
              onIonInput={(e) => setCategory(e.detail.value ?? "")}
            />
          </IonItem>
          <IonItem>
            <IonLabel position="stacked">Quantity</IonLabel>
            <IonInput
              type="number"
              inputmode="decimal"
              value={quantity}
              placeholder="0.00"
              onIonInput={(e) => setQuantity(e.detail.value ?? "")}
            />
          </IonItem>
          <IonItem>
            <IonLabel position="stacked">Unit</IonLabel>
            <IonInput
              value={unit}
              placeholder="e.g. kg"
              onIonInput={(e) => setUnit(e.detail.value ?? "")}
            />
          </IonItem>
          {/* Cost input hidden for Staff (Req 10.7). */}
          {!hideCost && (
            <IonItem>
              <IonLabel position="stacked">Cost per unit</IonLabel>
              <IonInput
                type="number"
                inputmode="decimal"
                value={cost}
                placeholder="0.00"
                onIonInput={(e) => setCost(e.detail.value ?? "")}
              />
            </IonItem>
          )}
        </IonList>

        {error && (
          <IonItem lines="none">
            <IonText color="danger">{error}</IonText>
          </IonItem>
        )}

        <IonButton expand="block" disabled={busy} onClick={submit}>
          {busy ? <IonSpinner name="dots" /> : "Save item"}
        </IonButton>
      </IonContent>
    </>
  );
}

// =============================================================================
// Update form (quantity / cost)
// =============================================================================

interface UpdateItemFormProps {
  item: InventoryItem;
  /** Hide the cost input for Staff (Req 10.7). */
  hideCost: boolean;
  onCancel: () => void;
  onSaved: () => void | Promise<void>;
}

function UpdateItemForm({ item, hideCost, onCancel, onSaved }: UpdateItemFormProps): JSX.Element {
  const [quantity, setQuantity] = useState<string>(formatMoney(item.quantity));
  const [cost, setCost] = useState<string>(formatMoney(item.cost_per_unit));
  const [busy, setBusy] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);

  // Show the cost field only when the user may see cost and the item carries a
  // cost value (Staff payloads omit it entirely — Req 10.7).
  const showCost = !hideCost && item.cost_per_unit !== undefined;

  const submit = async (): Promise<void> => {
    setError(null);
    const body: { quantity?: Money; cost_per_unit?: Money } = {};
    if (quantity.trim() !== "") body.quantity = quantity.trim();
    if (showCost && cost.trim() !== "") body.cost_per_unit = cost.trim();

    if (body.quantity === undefined && body.cost_per_unit === undefined) {
      setError("Enter a new quantity or cost to update.");
      return;
    }
    setBusy(true);
    try {
      await inventory.update(item.item_id, body);
      await onSaved();
    } catch (err) {
      setError(messageFor(err, "We couldn't update that item. Please try again."));
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <IonHeader>
        <IonToolbar>
          <IonTitle>{item.name}</IonTitle>
          <IonButtons slot="end">
            <IonButton onClick={onCancel}>Cancel</IonButton>
          </IonButtons>
        </IonToolbar>
      </IonHeader>
      <IonContent>
        <IonList>
          <IonItem lines="none">
            <IonNote color="medium">{item.category}</IonNote>
          </IonItem>
          <IonItem>
            <IonLabel position="stacked">Quantity ({item.unit})</IonLabel>
            <IonInput
              type="number"
              inputmode="decimal"
              value={quantity}
              placeholder="0.00"
              onIonInput={(e) => setQuantity(e.detail.value ?? "")}
            />
          </IonItem>
          {/* Cost input hidden for Staff / when absent (Req 10.7). */}
          {showCost && (
            <IonItem>
              <IonLabel position="stacked">Cost per unit</IonLabel>
              <IonInput
                type="number"
                inputmode="decimal"
                value={cost}
                placeholder="0.00"
                onIonInput={(e) => setCost(e.detail.value ?? "")}
              />
            </IonItem>
          )}
        </IonList>

        {error && (
          <IonItem lines="none">
            <IonText color="danger">{error}</IonText>
          </IonItem>
        )}

        <IonButton expand="block" disabled={busy} onClick={submit}>
          {busy ? <IonSpinner name="dots" /> : "Save changes"}
        </IonButton>
      </IonContent>
    </>
  );
}

// =============================================================================
// Scan / upload receipt → inventory (confirm-and-edit, Req 15.6)
// =============================================================================

interface ScanReceiptFormProps {
  onCancel: () => void;
  onSaved: () => void | Promise<void>;
}

/** One editable stock row in the scanned-receipt confirm form. */
interface ScanItemState {
  name: string;
  category: string;
  quantity: string;
  unit: string;
  cost: string;
}

/** Read a value from an unknown record as a display string (blank when absent). */
function draftStr(record: Record<string, unknown>, key: string): string {
  const v = record[key];
  if (v === undefined || v === null) return "";
  return String(v);
}

/** Coerce an editable string to a number, or `undefined` when blank/invalid. */
function numOrUndefined(value: string): number | undefined {
  const t = value.trim();
  if (t === "") return undefined;
  const n = Number(t);
  return Number.isFinite(n) ? n : undefined;
}

/** Coerce an editable string to a trimmed string, or `undefined` when blank. */
function strOrUndefined(value: string): string | undefined {
  const t = value.trim();
  return t === "" ? undefined : t;
}

/**
 * Scan or upload a purchase receipt and turn its line items into inventory
 * stock (Req 15.6), following the confirm-and-edit pattern from the Ingestion
 * surface (never a chat):
 *
 *  1. The owner picks an image — an `<input type="file" accept="image/*"
 *     capture="environment">` so mobile offers the camera and desktop offers a
 *     file picker.
 *  2. On select we call `ingestion.extract("inventory", file)`; a spinner shows
 *     while extracting and any failure surfaces inline with no items added.
 *  3. The returned draft items render as an **editable form** (name, category,
 *     quantity, unit, cost per unit per row; add/remove rows).
 *  4. **Confirm** → `ingestion.confirm({ doc_type: "inventory", draft })`; on
 *     success we close and refresh the list. **Discard** is client-side only.
 */
function ScanReceiptForm({ onCancel, onSaved }: ScanReceiptFormProps): JSX.Element {
  const [extracting, setExtracting] = useState<boolean>(false);
  const [extractError, setExtractError] = useState<string | null>(null);

  // `null` = no draft yet (show the picker). Non-null = show the editable form.
  const [items, setItems] = useState<ScanItemState[] | null>(null);

  const [busy, setBusy] = useState<boolean>(false);
  const [confirmError, setConfirmError] = useState<string | null>(null);

  const onPickFile = useCallback(async (e: ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0] ?? null;
    // Allow re-picking the same file later.
    e.target.value = "";
    if (!file) return;

    setExtracting(true);
    setExtractError(null);
    setConfirmError(null);
    try {
      // Req 15.1: a purchase receipt is a receipt image; the backend reuses the
      // receipt extractor and shapes line items into an inventory draft. On
      // >10 MB / unsupported format (400) or no items (422) this throws and we
      // surface it inline — no items are added.
      const res = await ingestion.extract("inventory", file);
      const draft = res.draft ?? {};
      const rawItems = Array.isArray(draft.items) ? draft.items : [];
      const rows: ScanItemState[] = rawItems
        .filter((el): el is Record<string, unknown> => typeof el === "object" && el !== null)
        .map((it) => ({
          name: draftStr(it, "name"),
          category: draftStr(it, "category"),
          quantity: draftStr(it, "quantity"),
          unit: draftStr(it, "unit"),
          cost: draftStr(it, "cost"),
        }));
      // Guarantee at least one editable row so the owner can always fill it in.
      setItems(rows.length > 0 ? rows : [{ name: "", category: "", quantity: "", unit: "", cost: "" }]);
    } catch (err) {
      setItems(null);
      setExtractError(messageFor(err, "We couldn't read that receipt. Please try another image."));
    } finally {
      setExtracting(false);
    }
  }, []);

  const updateItem = (i: number, patch: Partial<ScanItemState>) =>
    setItems((prev) => (prev ? prev.map((it, idx) => (idx === i ? { ...it, ...patch } : it)) : prev));
  const addItem = () =>
    setItems((prev) => [...(prev ?? []), { name: "", category: "", quantity: "", unit: "", cost: "" }]);
  const removeItem = (i: number) =>
    setItems((prev) => (prev ? prev.filter((_, idx) => idx !== i) : prev));

  const submit = async (): Promise<void> => {
    if (!items || items.length === 0) return;
    setConfirmError(null);
    setBusy(true);
    try {
      const payload = {
        items: items.map((it) => ({
          name: strOrUndefined(it.name),
          category: strOrUndefined(it.category),
          quantity: numOrUndefined(it.quantity),
          unit: strOrUndefined(it.unit),
          cost: numOrUndefined(it.cost),
        })),
      };
      // Req 15.6/15.10: persist via inventory create; the backend persists
      // nothing on domain failure and reports it here.
      await ingestion.confirm({ doc_type: "inventory", draft: payload });
      await onSaved();
    } catch (err) {
      setConfirmError(messageFor(err, "We couldn't save these items. Please review and try again."));
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <IonHeader>
        <IonToolbar>
          <IonTitle>Scan receipt</IonTitle>
          <IonButtons slot="end">
            {/* Discard is client-side only — nothing is persisted until confirm. */}
            <IonButton onClick={onCancel}>Cancel</IonButton>
          </IonButtons>
        </IonToolbar>
      </IonHeader>
      <IonContent className="ion-padding">
        {items === null ? (
          // ── Picker: take a photo or upload an image ──
          <>
            <IonText color="medium">
              <p>
                Take a photo of a purchase receipt or upload one. We'll pull out the
                items so you can review and add them to inventory.
              </p>
            </IonText>
            <IonItem>
              <IonLabel position="stacked">Receipt image</IonLabel>
              <input
                type="file"
                accept="image/*"
                capture="environment"
                onChange={(e) => void onPickFile(e)}
                disabled={extracting}
                style={{ marginTop: 8 }}
              />
            </IonItem>

            {extracting ? (
              <div style={{ display: "flex", justifyContent: "center", padding: "1.5rem" }}>
                <IonSpinner name="dots" />
              </div>
            ) : null}

            {extractError ? (
              <IonNote color="danger" style={{ display: "block", margin: "12px 0" }}>
                {extractError}
              </IonNote>
            ) : null}
          </>
        ) : (
          // ── Editable stock rows (confirm-and-edit) ──
          <>
            <IonList>
              <IonListHeader>
                <IonLabel>Review items</IonLabel>
                <IonButton size="small" onClick={addItem}>
                  Add row
                </IonButton>
              </IonListHeader>
              {items.map((it, i) => (
                <IonItemGroup key={i}>
                  <IonItemDivider>
                    <IonLabel>Item {i + 1}</IonLabel>
                    <IonButton
                      slot="end"
                      fill="clear"
                      size="small"
                      color="danger"
                      onClick={() => removeItem(i)}
                    >
                      Remove
                    </IonButton>
                  </IonItemDivider>
                  <IonItem>
                    <IonLabel position="stacked">Name</IonLabel>
                    <IonInput
                      value={it.name}
                      placeholder="e.g. All-purpose flour"
                      onIonInput={(e) => updateItem(i, { name: e.detail.value ?? "" })}
                    />
                  </IonItem>
                  <IonItem>
                    <IonLabel position="stacked">Category</IonLabel>
                    <IonInput
                      value={it.category}
                      placeholder="ingredient or packaging"
                      onIonInput={(e) => updateItem(i, { category: e.detail.value ?? "" })}
                    />
                  </IonItem>
                  <IonItem>
                    <IonLabel position="stacked">Quantity</IonLabel>
                    <IonInput
                      type="number"
                      inputmode="decimal"
                      value={it.quantity}
                      placeholder="0.00"
                      onIonInput={(e) => updateItem(i, { quantity: e.detail.value ?? "" })}
                    />
                  </IonItem>
                  <IonItem>
                    <IonLabel position="stacked">Unit</IonLabel>
                    <IonInput
                      value={it.unit}
                      placeholder="e.g. kg"
                      onIonInput={(e) => updateItem(i, { unit: e.detail.value ?? "" })}
                    />
                  </IonItem>
                  <IonItem>
                    <IonLabel position="stacked">Cost per unit</IonLabel>
                    <IonInput
                      type="number"
                      inputmode="decimal"
                      value={it.cost}
                      placeholder="0.00"
                      onIonInput={(e) => updateItem(i, { cost: e.detail.value ?? "" })}
                    />
                  </IonItem>
                </IonItemGroup>
              ))}
            </IonList>

            {confirmError ? (
              <IonNote color="danger" style={{ display: "block", margin: "12px 0" }}>
                {confirmError}
              </IonNote>
            ) : null}

            <div style={{ display: "flex", gap: 8, marginTop: 12 }}>
              <IonButton
                expand="block"
                style={{ flex: 1 }}
                disabled={busy || items.length === 0}
                onClick={() => void submit()}
              >
                {busy ? <IonSpinner name="dots" /> : "Confirm"}
              </IonButton>
              {/* Discard is client-side only (Req 15.5). */}
              <IonButton
                expand="block"
                style={{ flex: 1 }}
                fill="outline"
                color="medium"
                disabled={busy}
                onClick={onCancel}
              >
                Discard
              </IonButton>
            </div>
          </>
        )}
      </IonContent>
    </>
  );
}

export default Inventory;
