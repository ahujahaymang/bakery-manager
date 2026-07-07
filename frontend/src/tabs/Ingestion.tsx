/**
 * Ingestion — the Image_Ingestion confirm-and-edit surface (Req 15.2, 15.3,
 * 15.6, 15.7; design §7 "Image ingestion").
 *
 * The flow is deliberately **not a chat**. The User:
 *  1. Picks a document type and an image, then uploads it to `/ingestion/extract`.
 *  2. Receives a typed `Ingestion_Draft`, which this surface renders as an
 *     **editable UI form** — never a chat reply (Req 15.2, 15.7).
 *  3. Edits **any field** of the draft (Req 15.3), then either:
 *       - **Confirms** → `POST /ingestion/confirm` persists via the matching
 *         domain create (Req 15.6); or
 *       - **Discards** → the draft is dropped **client-side only**. No server
 *         call is made, so nothing is persisted (Req 15.5, design §7).
 *
 * All five document types are supported (Req 15.6): receipt→expense,
 * recipe→recipe, order→order, catalog→product, payment→payment. Each renders
 * its own editable form component below, mirroring the typed draft shapes the
 * backend returns from `ingestion_router`.
 *
 * Numeric fields are held as strings while editing (so a field can be blanked
 * mid-edit) and coerced back to numbers only when building the confirm payload.
 */

import React, { useCallback, useState } from "react";
import {
  IonButton,
  IonContent,
  IonHeader,
  IonInput,
  IonItem,
  IonItemDivider,
  IonItemGroup,
  IonLabel,
  IonList,
  IonListHeader,
  IonNote,
  IonPage,
  IonSegment,
  IonSegmentButton,
  IonSpinner,
  IonText,
  IonTitle,
  IonToolbar,
} from "@ionic/react";
import {
  ApiError,
  ingestion,
  type IngestionDocType,
} from "../api/endpoints";

// =============================================================================
// Shared helpers
// =============================================================================

/** Extract a user-facing message from an unknown thrown value. */
function messageFor(err: unknown, fallback: string): string {
  if (err instanceof ApiError) return err.message;
  if (err instanceof Error && err.message) return err.message;
  return fallback;
}

/** Coerce an editable string field to a number, or `undefined` when blank/invalid. */
function numOrUndefined(value: string): number | undefined {
  const t = value.trim();
  if (t === "") return undefined;
  const n = Number(t);
  return Number.isFinite(n) ? n : undefined;
}

/** Coerce an editable string field to a trimmed string, or `undefined` when blank. */
function strOrUndefined(value: string): string | undefined {
  const t = value.trim();
  return t === "" ? undefined : t;
}

/** Read a value from an unknown record as a display string (blank when absent). */
function asStr(record: Record<string, unknown>, key: string): string {
  const v = record[key];
  if (v === undefined || v === null) return "";
  return String(v);
}

/** Read an array of records from an unknown record (empty when absent/typed wrong). */
function asRecords(record: Record<string, unknown>, key: string): Record<string, unknown>[] {
  const v = record[key];
  if (!Array.isArray(v)) return [];
  return v.filter((el): el is Record<string, unknown> => typeof el === "object" && el !== null);
}

/** The user-facing label for each supported document type (Req 15.6). */
const DOC_TYPE_LABELS: Record<IngestionDocType, string> = {
  receipt: "Receipt → Expense",
  recipe: "Recipe",
  order: "Order",
  catalog: "Catalog → Products",
  payment: "Payment",
  // `inventory` is offered from the Inventory tab's "Scan receipt" flow, not
  // this generic surface, so it is intentionally omitted from DOC_TYPES below.
  inventory: "Receipt → Inventory",
};

const DOC_TYPES: IngestionDocType[] = ["receipt", "recipe", "order", "catalog", "payment"];

// =============================================================================
// Container
// =============================================================================

export function Ingestion(): JSX.Element {
  const [docType, setDocType] = useState<IngestionDocType>("receipt");
  const [file, setFile] = useState<File | null>(null);

  const [extracting, setExtracting] = useState<boolean>(false);
  const [extractError, setExtractError] = useState<string | null>(null);

  // The extracted draft awaiting confirmation. `null` means "no draft yet" — we
  // show the upload form. A discard resets this to `null` (client-side only).
  const [draft, setDraft] = useState<Record<string, unknown> | null>(null);
  const [draftDocType, setDraftDocType] = useState<IngestionDocType>("receipt");

  const [confirmMessage, setConfirmMessage] = useState<string | null>(null);

  const onPickFile = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    const picked = e.target.files?.[0] ?? null;
    setFile(picked);
    setExtractError(null);
  }, []);

  const extract = useCallback(async (): Promise<void> => {
    if (!file) {
      setExtractError("Choose an image to extract.");
      return;
    }
    setExtracting(true);
    setExtractError(null);
    setConfirmMessage(null);
    try {
      // Req 15.1: upload → typed Ingestion_Draft. On >10 MB / unsupported format
      // the backend returns a validation error (Req 15.8); on no structured data
      // an extraction failure (Req 15.9). Both surface inline; no draft renders.
      const res = await ingestion.extract(docType, file);
      setDraft(res.draft ?? {});
      setDraftDocType(res.doc_type);
    } catch (err) {
      setDraft(null);
      setExtractError(messageFor(err, "We couldn't read that image. Please try another."));
    } finally {
      setExtracting(false);
    }
  }, [docType, file]);

  // Req 15.5 + design §7: discard is purely client-side. Drop the draft; do NOT
  // call the backend. Nothing was ever persisted (persistence only happens on
  // confirm), so simply returning to the upload form is a complete discard.
  const discard = useCallback((): void => {
    setDraft(null);
    setFile(null);
    setExtractError(null);
    setConfirmMessage(null);
  }, []);

  const onConfirmed = useCallback((summary: string): void => {
    setDraft(null);
    setFile(null);
    setConfirmMessage(summary);
  }, []);

  return (
    <IonPage>
      <IonHeader>
        <IonToolbar>
          <IonTitle>Scan a document</IonTitle>
        </IonToolbar>
      </IonHeader>

      <IonContent className="ion-padding">
        {draft === null ? (
          // ── Upload form (no chat — Req 15.7) ──
          <>
            <IonList>
              <IonItem>
                <IonLabel position="stacked">Document type</IonLabel>
              </IonItem>
              <IonItem lines="none">
                <IonSegment
                  scrollable
                  value={docType}
                  onIonChange={(e) => setDocType(e.detail.value as IngestionDocType)}
                >
                  {DOC_TYPES.map((dt) => (
                    <IonSegmentButton key={dt} value={dt}>
                      <IonLabel>{DOC_TYPE_LABELS[dt]}</IonLabel>
                    </IonSegmentButton>
                  ))}
                </IonSegment>
              </IonItem>

              <IonItem>
                <IonLabel position="stacked">Image</IonLabel>
                <input
                  type="file"
                  accept="image/*"
                  onChange={onPickFile}
                  style={{ marginTop: 8 }}
                />
              </IonItem>
            </IonList>

            {confirmMessage ? (
              <IonNote color="success" style={{ display: "block", margin: "8px 0" }}>
                {confirmMessage}
              </IonNote>
            ) : null}

            {extractError ? (
              <IonNote color="danger" style={{ display: "block", margin: "8px 0" }}>
                {extractError}
              </IonNote>
            ) : null}

            <IonButton expand="block" disabled={extracting || !file} onClick={() => void extract()}>
              {extracting ? <IonSpinner name="dots" /> : "Extract"}
            </IonButton>
          </>
        ) : (
          // ── Editable draft form (Req 15.2, 15.3) ──
          <DraftForm
            docType={draftDocType}
            draft={draft}
            onConfirmed={onConfirmed}
            onDiscard={discard}
          />
        )}
      </IonContent>
    </IonPage>
  );
}

// =============================================================================
// Draft form dispatcher
// =============================================================================

interface DraftFormProps {
  docType: IngestionDocType;
  draft: Record<string, unknown>;
  onConfirmed: (summary: string) => void;
  onDiscard: () => void;
}

/** Route the extracted draft to the editable form for its document type. */
function DraftForm({ docType, draft, onConfirmed, onDiscard }: DraftFormProps): JSX.Element {
  switch (docType) {
    case "recipe":
      return <RecipeDraftForm draft={draft} onConfirmed={onConfirmed} onDiscard={onDiscard} />;
    case "order":
      return <OrderDraftForm draft={draft} onConfirmed={onConfirmed} onDiscard={onDiscard} />;
    case "catalog":
      return <CatalogDraftForm draft={draft} onConfirmed={onConfirmed} onDiscard={onDiscard} />;
    case "payment":
      return <PaymentDraftForm draft={draft} onConfirmed={onConfirmed} onDiscard={onDiscard} />;
    case "receipt":
    default:
      return <ReceiptDraftForm draft={draft} onConfirmed={onConfirmed} onDiscard={onDiscard} />;
  }
}

/** Shared confirm/discard action bar rendered at the foot of every draft form. */
function DraftActions({
  busy,
  error,
  onConfirm,
  onDiscard,
}: {
  busy: boolean;
  error: string | null;
  onConfirm: () => void;
  onDiscard: () => void;
}): JSX.Element {
  return (
    <>
      {error ? (
        <IonNote color="danger" style={{ display: "block", margin: "8px 0" }}>
          {error}
        </IonNote>
      ) : null}
      <div style={{ display: "flex", gap: 8, marginTop: 12 }}>
        <IonButton expand="block" style={{ flex: 1 }} disabled={busy} onClick={onConfirm}>
          {busy ? <IonSpinner name="dots" /> : "Confirm"}
        </IonButton>
        {/* Discard is client-side only — no server call (Req 15.5). */}
        <IonButton
          expand="block"
          style={{ flex: 1 }}
          fill="outline"
          color="medium"
          disabled={busy}
          onClick={onDiscard}
        >
          Discard
        </IonButton>
      </div>
    </>
  );
}

/** A small hook encapsulating the confirm call + busy/error state. */
function useConfirm(
  docType: IngestionDocType,
  onConfirmed: (summary: string) => void
): {
  busy: boolean;
  error: string | null;
  confirm: (draft: Record<string, unknown>, summary: string) => Promise<void>;
} {
  const [busy, setBusy] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);

  const confirm = useCallback(
    async (draft: Record<string, unknown>, summary: string): Promise<void> => {
      setBusy(true);
      setError(null);
      try {
        // Req 15.4/15.6: persist through the matching domain create. On domain
        // failure the backend persists nothing (Req 15.10) and reports it here.
        await ingestion.confirm({ doc_type: docType, draft });
        onConfirmed(summary);
      } catch (err) {
        setError(messageFor(err, "We couldn't save this. Please review and try again."));
      } finally {
        setBusy(false);
      }
    },
    [docType, onConfirmed]
  );

  return { busy, error, confirm };
}

// =============================================================================
// Receipt → Expense draft form (Req 15.6)
// =============================================================================

interface ReceiptItemState {
  name: string;
  quantity: string;
  unit: string;
  price: string;
}

/**
 * Editable form for a receipt draft (persisted as a `PurchaseExpense`).
 *
 * The backend expense create reads `amount`, `category`, `vendor_name`,
 * `expense_date`/`date`, plus optional `description`/`notes`. The extractor
 * fills `amount`/`date`/`customer_name`/`items`; we additionally expose the
 * `category`, `vendor_name`, and `description` fields the User will want to set.
 */
function ReceiptDraftForm({
  draft,
  onConfirmed,
  onDiscard,
}: Omit<DraftFormProps, "docType">): JSX.Element {
  const [amount, setAmount] = useState<string>(asStr(draft, "amount"));
  const [category, setCategory] = useState<string>(asStr(draft, "category") || "other");
  const [vendor, setVendor] = useState<string>(
    asStr(draft, "vendor_name") || asStr(draft, "customer_name")
  );
  const [expenseDate, setExpenseDate] = useState<string>(
    asStr(draft, "expense_date") || asStr(draft, "date")
  );
  const [method, setMethod] = useState<string>(asStr(draft, "method"));
  const [description, setDescription] = useState<string>(asStr(draft, "description"));
  const [items, setItems] = useState<ReceiptItemState[]>(() =>
    asRecords(draft, "items").map((it) => ({
      name: asStr(it, "name"),
      quantity: asStr(it, "quantity"),
      unit: asStr(it, "unit"),
      price: asStr(it, "price"),
    }))
  );

  const { busy, error, confirm } = useConfirm("receipt", onConfirmed);

  const updateItem = (i: number, patch: Partial<ReceiptItemState>) =>
    setItems((prev) => prev.map((it, idx) => (idx === i ? { ...it, ...patch } : it)));
  const addItem = () =>
    setItems((prev) => [...prev, { name: "", quantity: "", unit: "", price: "" }]);
  const removeItem = (i: number) => setItems((prev) => prev.filter((_, idx) => idx !== i));

  const submit = () => {
    const payload: Record<string, unknown> = {
      amount: numOrUndefined(amount),
      category: strOrUndefined(category),
      vendor_name: strOrUndefined(vendor),
      expense_date: strOrUndefined(expenseDate),
      method: strOrUndefined(method),
      description: strOrUndefined(description),
      items: items.map((it) => ({
        name: strOrUndefined(it.name),
        quantity: numOrUndefined(it.quantity),
        unit: strOrUndefined(it.unit),
        price: numOrUndefined(it.price),
      })),
    };
    void confirm(payload, "Expense recorded.");
  };

  return (
    <>
      <DraftHeader title="Review expense" />
      <IonList>
        <FieldInput label="Amount" type="number" value={amount} onChange={setAmount} />
        <FieldInput label="Category" value={category} onChange={setCategory} />
        <FieldInput label="Vendor" value={vendor} onChange={setVendor} />
        <FieldInput label="Date" type="date" value={expenseDate} onChange={setExpenseDate} />
        <FieldInput label="Payment method" value={method} onChange={setMethod} />
        <FieldInput label="Description" value={description} onChange={setDescription} />
      </IonList>

      <IonList>
        <IonListHeader>
          <IonLabel>Line items</IonLabel>
          <IonButton size="small" onClick={addItem}>
            Add item
          </IonButton>
        </IonListHeader>
        {items.map((it, i) => (
          <IonItemGroup key={i}>
            <IonItemDivider>
              <IonLabel>Item {i + 1}</IonLabel>
              <IonButton slot="end" fill="clear" size="small" color="danger" onClick={() => removeItem(i)}>
                Remove
              </IonButton>
            </IonItemDivider>
            <FieldInput label="Name" value={it.name} onChange={(v) => updateItem(i, { name: v })} />
            <FieldInput
              label="Quantity"
              type="number"
              value={it.quantity}
              onChange={(v) => updateItem(i, { quantity: v })}
            />
            <FieldInput label="Unit" value={it.unit} onChange={(v) => updateItem(i, { unit: v })} />
            <FieldInput
              label="Price"
              type="number"
              value={it.price}
              onChange={(v) => updateItem(i, { price: v })}
            />
          </IonItemGroup>
        ))}
      </IonList>

      <DraftActions busy={busy} error={error} onConfirm={submit} onDiscard={onDiscard} />
    </>
  );
}

// =============================================================================
// Payment draft form (Req 15.6)
// =============================================================================

/**
 * Editable form for a payment draft (persisted via `PaymentService`). The
 * backend needs an order/customer identifier plus amount and method.
 */
function PaymentDraftForm({
  draft,
  onConfirmed,
  onDiscard,
}: Omit<DraftFormProps, "docType">): JSX.Element {
  const [orderIdentifier, setOrderIdentifier] = useState<string>(
    asStr(draft, "order_identifier") ||
      asStr(draft, "order_id") ||
      asStr(draft, "customer_name") ||
      asStr(draft, "customer_phone")
  );
  const [amount, setAmount] = useState<string>(asStr(draft, "amount"));
  const [method, setMethod] = useState<string>(asStr(draft, "method"));

  const { busy, error, confirm } = useConfirm("payment", onConfirmed);

  const submit = () => {
    const payload: Record<string, unknown> = {
      order_identifier: strOrUndefined(orderIdentifier),
      amount: numOrUndefined(amount),
      method: strOrUndefined(method),
    };
    void confirm(payload, "Payment recorded.");
  };

  return (
    <>
      <DraftHeader title="Review payment" />
      <IonList>
        <FieldInput
          label="Order / customer"
          value={orderIdentifier}
          onChange={setOrderIdentifier}
        />
        <FieldInput label="Amount" type="number" value={amount} onChange={setAmount} />
        <FieldInput label="Method" value={method} onChange={setMethod} />
      </IonList>

      <DraftActions busy={busy} error={error} onConfirm={submit} onDiscard={onDiscard} />
    </>
  );
}

// =============================================================================
// Recipe draft form (Req 15.6)
// =============================================================================

interface ComponentState {
  item_name: string;
  quantity: string;
  unit: string;
}

function componentsFrom(records: Record<string, unknown>[]): ComponentState[] {
  return records.map((c) => ({
    item_name: asStr(c, "item_name"),
    quantity: asStr(c, "quantity"),
    unit: asStr(c, "unit"),
  }));
}

/**
 * Editable form for a recipe draft (persisted via `RecipeService.create_recipe`
 * + `add_component`). Ingredients and packaging are edited as separate lists.
 */
function RecipeDraftForm({
  draft,
  onConfirmed,
  onDiscard,
}: Omit<DraftFormProps, "docType">): JSX.Element {
  const [name, setName] = useState<string>(asStr(draft, "name"));
  const [yieldPerBatch, setYieldPerBatch] = useState<string>(asStr(draft, "yield_per_batch"));
  const [ingredients, setIngredients] = useState<ComponentState[]>(() =>
    componentsFrom(asRecords(draft, "ingredients"))
  );
  const [packaging, setPackaging] = useState<ComponentState[]>(() =>
    componentsFrom(asRecords(draft, "packaging"))
  );

  const { busy, error, confirm } = useConfirm("recipe", onConfirmed);

  const submit = () => {
    const toPayload = (rows: ComponentState[]) =>
      rows.map((c) => ({
        item_name: strOrUndefined(c.item_name),
        quantity: numOrUndefined(c.quantity),
        unit: strOrUndefined(c.unit),
      }));
    const payload: Record<string, unknown> = {
      name: strOrUndefined(name),
      yield_per_batch: numOrUndefined(yieldPerBatch),
      ingredients: toPayload(ingredients),
      packaging: toPayload(packaging),
    };
    void confirm(payload, "Recipe created.");
  };

  return (
    <>
      <DraftHeader title="Review recipe" />
      <IonList>
        <FieldInput label="Name" value={name} onChange={setName} />
        <FieldInput
          label="Yield per batch"
          type="number"
          value={yieldPerBatch}
          onChange={setYieldPerBatch}
        />
      </IonList>

      <ComponentListEditor
        title="Ingredients"
        rows={ingredients}
        onChange={setIngredients}
      />
      <ComponentListEditor title="Packaging" rows={packaging} onChange={setPackaging} />

      <DraftActions busy={busy} error={error} onConfirm={submit} onDiscard={onDiscard} />
    </>
  );
}

/** A reusable editor for a list of recipe components (ingredient or packaging). */
function ComponentListEditor({
  title,
  rows,
  onChange,
}: {
  title: string;
  rows: ComponentState[];
  onChange: (rows: ComponentState[]) => void;
}): JSX.Element {
  const update = (i: number, patch: Partial<ComponentState>) =>
    onChange(rows.map((r, idx) => (idx === i ? { ...r, ...patch } : r)));
  const add = () => onChange([...rows, { item_name: "", quantity: "", unit: "" }]);
  const remove = (i: number) => onChange(rows.filter((_, idx) => idx !== i));

  return (
    <IonList>
      <IonListHeader>
        <IonLabel>{title}</IonLabel>
        <IonButton size="small" onClick={add}>
          Add
        </IonButton>
      </IonListHeader>
      {rows.length === 0 ? (
        <IonItem lines="none">
          <IonText color="medium">None</IonText>
        </IonItem>
      ) : (
        rows.map((r, i) => (
          <IonItemGroup key={i}>
            <IonItemDivider>
              <IonLabel>{title.replace(/s$/, "")} {i + 1}</IonLabel>
              <IonButton slot="end" fill="clear" size="small" color="danger" onClick={() => remove(i)}>
                Remove
              </IonButton>
            </IonItemDivider>
            <FieldInput
              label="Item"
              value={r.item_name}
              onChange={(v) => update(i, { item_name: v })}
            />
            <FieldInput
              label="Quantity"
              type="number"
              value={r.quantity}
              onChange={(v) => update(i, { quantity: v })}
            />
            <FieldInput label="Unit" value={r.unit} onChange={(v) => update(i, { unit: v })} />
          </IonItemGroup>
        ))
      )}
    </IonList>
  );
}

// =============================================================================
// Order draft form (Req 15.6)
// =============================================================================

interface OrderItemState {
  recipe_name: string;
  quantity: string;
  selling_price: string;
}

/** Editable form for an order draft (persisted via `OrderService.create_order`). */
function OrderDraftForm({
  draft,
  onConfirmed,
  onDiscard,
}: Omit<DraftFormProps, "docType">): JSX.Element {
  const [customerName, setCustomerName] = useState<string>(asStr(draft, "customer_name"));
  const [customerPhone, setCustomerPhone] = useState<string>(asStr(draft, "customer_phone"));
  const [deliveryDate, setDeliveryDate] = useState<string>(asStr(draft, "delivery_date"));
  const [items, setItems] = useState<OrderItemState[]>(() =>
    asRecords(draft, "items").map((it) => ({
      recipe_name: asStr(it, "recipe_name"),
      quantity: asStr(it, "quantity"),
      selling_price: asStr(it, "selling_price"),
    }))
  );

  const { busy, error, confirm } = useConfirm("order", onConfirmed);

  const updateItem = (i: number, patch: Partial<OrderItemState>) =>
    setItems((prev) => prev.map((it, idx) => (idx === i ? { ...it, ...patch } : it)));
  const addItem = () =>
    setItems((prev) => [...prev, { recipe_name: "", quantity: "", selling_price: "" }]);
  const removeItem = (i: number) => setItems((prev) => prev.filter((_, idx) => idx !== i));

  const submit = () => {
    const payload: Record<string, unknown> = {
      customer_name: strOrUndefined(customerName),
      customer_phone: strOrUndefined(customerPhone),
      delivery_date: strOrUndefined(deliveryDate),
      items: items.map((it) => ({
        recipe_name: strOrUndefined(it.recipe_name),
        quantity: numOrUndefined(it.quantity),
        selling_price: numOrUndefined(it.selling_price),
      })),
    };
    void confirm(payload, "Order created.");
  };

  return (
    <>
      <DraftHeader title="Review order" />
      <IonList>
        <FieldInput label="Customer name" value={customerName} onChange={setCustomerName} />
        <FieldInput label="Customer phone" value={customerPhone} onChange={setCustomerPhone} />
        <FieldInput
          label="Delivery date"
          type="date"
          value={deliveryDate}
          onChange={setDeliveryDate}
        />
      </IonList>

      <IonList>
        <IonListHeader>
          <IonLabel>Items</IonLabel>
          <IonButton size="small" onClick={addItem}>
            Add item
          </IonButton>
        </IonListHeader>
        {items.map((it, i) => (
          <IonItemGroup key={i}>
            <IonItemDivider>
              <IonLabel>Item {i + 1}</IonLabel>
              <IonButton slot="end" fill="clear" size="small" color="danger" onClick={() => removeItem(i)}>
                Remove
              </IonButton>
            </IonItemDivider>
            <FieldInput
              label="Recipe / product"
              value={it.recipe_name}
              onChange={(v) => updateItem(i, { recipe_name: v })}
            />
            <FieldInput
              label="Quantity"
              type="number"
              value={it.quantity}
              onChange={(v) => updateItem(i, { quantity: v })}
            />
            <FieldInput
              label="Selling price"
              type="number"
              value={it.selling_price}
              onChange={(v) => updateItem(i, { selling_price: v })}
            />
          </IonItemGroup>
        ))}
      </IonList>

      <DraftActions busy={busy} error={error} onConfirm={submit} onDiscard={onDiscard} />
    </>
  );
}

// =============================================================================
// Catalog → Products draft form (Req 15.6)
// =============================================================================

interface VariantState {
  size_label: string;
  price: string;
}

interface ProductState {
  name: string;
  variants: VariantState[];
}

interface CategoryState {
  name: string;
  products: ProductState[];
}

function categoriesFrom(draft: Record<string, unknown>): CategoryState[] {
  return asRecords(draft, "categories").map((cat) => ({
    name: asStr(cat, "name"),
    products: asRecords(cat, "products").map((p) => ({
      name: asStr(p, "name"),
      variants: asRecords(p, "variants").map((v) => ({
        size_label: asStr(v, "size_label"),
        price: asStr(v, "price"),
      })),
    })),
  }));
}

/**
 * Editable form for a catalog draft (each product persisted via
 * `ProductService.create_product`). Categories → products → priced variants
 * are all editable, mirroring the nested catalog draft shape.
 */
function CatalogDraftForm({
  draft,
  onConfirmed,
  onDiscard,
}: Omit<DraftFormProps, "docType">): JSX.Element {
  const [categories, setCategories] = useState<CategoryState[]>(() => categoriesFrom(draft));

  const { busy, error, confirm } = useConfirm("catalog", onConfirmed);

  const updateCategory = (ci: number, patch: Partial<CategoryState>) =>
    setCategories((prev) => prev.map((c, i) => (i === ci ? { ...c, ...patch } : c)));
  const addCategory = () =>
    setCategories((prev) => [...prev, { name: "", products: [{ name: "", variants: [{ size_label: "", price: "" }] }] }]);
  const removeCategory = (ci: number) =>
    setCategories((prev) => prev.filter((_, i) => i !== ci));

  const updateProduct = (ci: number, pi: number, patch: Partial<ProductState>) =>
    updateCategory(ci, {
      products: categories[ci].products.map((p, i) => (i === pi ? { ...p, ...patch } : p)),
    });
  const addProduct = (ci: number) =>
    updateCategory(ci, {
      products: [...categories[ci].products, { name: "", variants: [{ size_label: "", price: "" }] }],
    });
  const removeProduct = (ci: number, pi: number) =>
    updateCategory(ci, { products: categories[ci].products.filter((_, i) => i !== pi) });

  const updateVariant = (ci: number, pi: number, vi: number, patch: Partial<VariantState>) =>
    updateProduct(ci, pi, {
      variants: categories[ci].products[pi].variants.map((v, i) => (i === vi ? { ...v, ...patch } : v)),
    });
  const addVariant = (ci: number, pi: number) =>
    updateProduct(ci, pi, {
      variants: [...categories[ci].products[pi].variants, { size_label: "", price: "" }],
    });
  const removeVariant = (ci: number, pi: number, vi: number) =>
    updateProduct(ci, pi, {
      variants: categories[ci].products[pi].variants.filter((_, i) => i !== vi),
    });

  const submit = () => {
    const payload: Record<string, unknown> = {
      categories: categories.map((c) => ({
        name: strOrUndefined(c.name),
        products: c.products.map((p) => ({
          name: strOrUndefined(p.name),
          variants: p.variants.map((v) => ({
            size_label: strOrUndefined(v.size_label),
            price: numOrUndefined(v.price),
          })),
        })),
      })),
    };
    void confirm(payload, "Products created.");
  };

  return (
    <>
      <DraftHeader title="Review catalog" />
      {categories.length === 0 ? (
        <IonItem lines="none">
          <IonText color="medium">No categories detected. Add one to continue.</IonText>
        </IonItem>
      ) : null}

      {categories.map((cat, ci) => (
        <IonList key={ci}>
          <IonListHeader>
            <IonLabel>Category {ci + 1}</IonLabel>
            <IonButton size="small" fill="clear" color="danger" onClick={() => removeCategory(ci)}>
              Remove
            </IonButton>
          </IonListHeader>
          <FieldInput
            label="Category name"
            value={cat.name}
            onChange={(v) => updateCategory(ci, { name: v })}
          />

          {cat.products.map((product, pi) => (
            <IonItemGroup key={pi}>
              <IonItemDivider>
                <IonLabel>Product {pi + 1}</IonLabel>
                <IonButton
                  slot="end"
                  fill="clear"
                  size="small"
                  color="danger"
                  onClick={() => removeProduct(ci, pi)}
                >
                  Remove
                </IonButton>
              </IonItemDivider>
              <FieldInput
                label="Product name"
                value={product.name}
                onChange={(v) => updateProduct(ci, pi, { name: v })}
              />
              {product.variants.map((variant, vi) => (
                <IonItem key={vi}>
                  <IonInput
                    label="Size"
                    labelPlacement="stacked"
                    value={variant.size_label}
                    onIonInput={(e) => updateVariant(ci, pi, vi, { size_label: e.detail.value ?? "" })}
                  />
                  <IonInput
                    label="Price"
                    labelPlacement="stacked"
                    type="number"
                    inputmode="decimal"
                    value={variant.price}
                    onIonInput={(e) => updateVariant(ci, pi, vi, { price: e.detail.value ?? "" })}
                  />
                  <IonButton
                    slot="end"
                    fill="clear"
                    size="small"
                    color="danger"
                    onClick={() => removeVariant(ci, pi, vi)}
                  >
                    ✕
                  </IonButton>
                </IonItem>
              ))}
              <IonButton size="small" fill="clear" onClick={() => addVariant(ci, pi)}>
                Add variant
              </IonButton>
            </IonItemGroup>
          ))}

          <IonButton size="small" fill="clear" onClick={() => addProduct(ci)}>
            Add product
          </IonButton>
        </IonList>
      ))}

      <IonButton size="small" onClick={addCategory} style={{ marginTop: 8 }}>
        Add category
      </IonButton>

      <DraftActions busy={busy} error={error} onConfirm={submit} onDiscard={onDiscard} />
    </>
  );
}

// =============================================================================
// Small shared form primitives
// =============================================================================

/** A stacked-label header shown above each draft form. */
function DraftHeader({ title }: { title: string }): JSX.Element {
  return (
    <IonItem lines="none">
      <IonLabel>
        <h2>{title}</h2>
        <IonNote color="medium">Edit any field, then confirm or discard.</IonNote>
      </IonLabel>
    </IonItem>
  );
}

/** A single labeled, controlled text/number input row (Req 15.3 — every field editable). */
function FieldInput({
  label,
  value,
  onChange,
  type = "text",
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  type?: "text" | "number" | "date";
}): JSX.Element {
  return (
    <IonItem>
      <IonInput
        label={label}
        labelPlacement="stacked"
        type={type}
        inputmode={type === "number" ? "decimal" : undefined}
        value={value}
        onIonInput={(e) => onChange(e.detail.value ?? "")}
      />
    </IonItem>
  );
}

export default Ingestion;
