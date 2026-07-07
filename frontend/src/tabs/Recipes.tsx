/**
 * Recipes tab — the Recipes_Surface (design §"Frontend structure" → `tabs/Recipes.tsx`).
 *
 * A recipe-management screen backed by the `/api/v1/recipes` endpoints:
 *
 *  - **Create recipe (Req 11.1)** — a form capturing a recipe name (1–100 chars)
 *    and a yield per batch (> 0, ≤ 999,999). Submitting posts the recipe; the
 *    Service_Layer performs the authoritative validation (Req 11.5) and errors
 *    surface inline.
 *  - **Add component (Req 11.2)** — a per-recipe form associating an existing
 *    inventory item (chosen by name) with a quantity (> 0, ≤ 999,999) in the
 *    inventory item's unit and a component type of `ingredient` or `packaging`.
 *    Component-quantity validation is server-side (Req 11.6).
 *  - **Recipe list** — every recipe is listed with its name and yield per batch.
 *  - **Owner-only cost-per-unit (Req 11.4, 11.7)** — the cost-per-unit of each
 *    recipe is shown only when the signed-in user holds the Owner role. It is
 *    read from the Owner-only `GET /recipes/{id}/cost` endpoint and recomputed
 *    on every load, so a change to an inventory item's cost is reflected the
 *    next time this surface loads (Req 11.4). The Backend independently rejects
 *    the cost read for Staff (Req 11.7); the role gate here is a UX convenience.
 */

import { useCallback, useEffect, useState, type ChangeEvent } from "react";
import {
  useIonRouter,
  useIonViewWillEnter,
  IonButton,
  IonButtons,
  IonCard,
  IonCardContent,
  IonCardHeader,
  IonCardSubtitle,
  IonCardTitle,
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
  IonSelect,
  IonSelectOption,
  IonSpinner,
  IonText,
  IonTitle,
  IonToolbar,
} from "@ionic/react";
import { TabTour } from "../components/TabTour";
import { cameraOutline } from "ionicons/icons";
import { useLocation } from "react-router-dom";
import {
  ApiError,
  ingestion,
  inventory as inventoryApi,
  recipes as recipesApi,
  type InventoryItem,
  type Money,
  type Recipe,
  type RecipeComponentType,
  type RecipeCost,
} from "../api/endpoints";
import { useAuth } from "../auth/AuthContext";

/** Format a monetary value (INR) for display. */
function formatMoney(value: Money | undefined | null): string {
  if (value === undefined || value === null) return "";
  const n = Number(value);
  return Number.isFinite(n) ? `₹${n.toFixed(2)}` : String(value);
}

/** Extract a user-facing message from an unknown thrown value. */
function messageFor(err: unknown, fallback: string): string {
  if (err instanceof ApiError) return err.message;
  if (err instanceof Error && err.message) return err.message;
  return fallback;
}

export function Recipes(): JSX.Element {
  const { role } = useAuth();
  // Cost-per-unit is Owner-only (Req 11.7); the Backend enforces this too.
  const isOwner = role === "owner";

  const [recipes, setRecipes] = useState<Recipe[]>([]);
  const [loading, setLoading] = useState<boolean>(true);
  const [listError, setListError] = useState<string | null>(null);

  // Cost-per-unit keyed by recipe id, recomputed on every load (Req 11.4).
  const [costs, setCosts] = useState<Record<string, RecipeCost>>({});

  // ── Create recipe form (Req 11.1) ──
  const [showCreate, setShowCreate] = useState<boolean>(false);
  // Name to prefill the create form with (interconnect #4).
  const [prefillName, setPrefillName] = useState<string>("");

  const location = useLocation();
  const router = useIonRouter();

  // Interconnect #4: when arriving with `?new=<name>` (e.g. from the Orders tab's
  // "Add recipe"), auto-open the create form prefilled with the name, then clear
  // the param so it doesn't re-trigger on a later view-enter.
  useIonViewWillEnter(() => {
    const params = new URLSearchParams(location.search);
    const newName = params.get("new");
    if (newName !== null) {
      setPrefillName(newName);
      setShowCreate(true);
      router.push("/app/recipes", "none", "replace");
    }
  }, [location.search]);

  // ── Scan/upload a recipe → confirm-and-edit → create (Req 15.6). Available
  // to Owner and Staff alike, since recipe management is not Owner-gated. ──
  const [scanOpen, setScanOpen] = useState<boolean>(false);

  // ── Add component form (Req 11.2) ──
  const [componentTarget, setComponentTarget] = useState<Recipe | null>(null);

  const load = useCallback(async (): Promise<void> => {
    setLoading(true);
    setListError(null);
    try {
      const rows = await recipesApi.list();
      setRecipes(rows);

      // Owner-only: recompute cost-per-unit for each recipe on load (Req 11.4,
      // 11.7). A recipe whose components lack a resolvable cost yields an error
      // from the endpoint; we simply omit its cost rather than failing the list.
      if (isOwner) {
        const entries = await Promise.all(
          rows.map(async (recipe) => {
            try {
              const cost = await recipesApi.getCost(recipe.recipe_id);
              return [recipe.recipe_id, cost] as const;
            } catch {
              return null;
            }
          })
        );
        const next: Record<string, RecipeCost> = {};
        for (const entry of entries) {
          if (entry) next[entry[0]] = entry[1];
        }
        setCosts(next);
      } else {
        setCosts({});
      }
    } catch (err) {
      setListError(messageFor(err, "We couldn't load your recipes. Please try again."));
    } finally {
      setLoading(false);
    }
  }, [isOwner]);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <IonPage>
      <TabTour
        tabKey="recipes"
        title="Recipes"
        intro="Build recipes and know your costs."
        points={[
          "Add ingredients and packaging to each recipe.",
          "Owners see cost-per-unit based on current prices.",
          "Scan a handwritten or printed recipe to add it quickly.",
        ]}
      />
      <IonHeader>
        <IonToolbar>
          <IonTitle>Recipes</IonTitle>
          <IonButtons slot="end">
            {/* Scan/upload a recipe image → confirm-and-edit → create (Req 15.6).
                Recipes are Owner + Staff, so this is not Owner-gated. */}
            <IonButton onClick={() => setScanOpen(true)}>
              <IonIcon slot="start" icon={cameraOutline} />
              Scan recipe
            </IonButton>
            <IonButton
              onClick={() => {
                setPrefillName("");
                setShowCreate(true);
              }}
            >
              New recipe
            </IonButton>
          </IonButtons>
        </IonToolbar>
      </IonHeader>

      <IonContent className="ion-padding">
        {loading ? (
          <div style={{ textAlign: "center", marginTop: 24 }}>
            <IonSpinner name="dots" />
          </div>
        ) : listError ? (
          <div style={{ textAlign: "center", marginTop: 24 }}>
            <IonNote color="danger" style={{ display: "block" }}>
              {listError}
            </IonNote>
            <IonButton fill="clear" onClick={() => void load()}>
              Retry
            </IonButton>
          </div>
        ) : recipes.length === 0 ? (
          <div style={{ textAlign: "center", padding: "3rem 1.5rem" }}>
            <IonText color="medium">
              <p>No recipes yet.</p>
              <p>Create your first recipe to start tracking cost per unit.</p>
            </IonText>
            <IonButton
              onClick={() => {
                setPrefillName("");
                setShowCreate(true);
              }}
            >
              New recipe
            </IonButton>
          </div>
        ) : (
          recipes.map((recipe) => (
            <RecipeCard
              key={recipe.recipe_id}
              recipe={recipe}
              // Cost is rendered only for Owner and only when resolvable (Req 11.7).
              cost={isOwner ? costs[recipe.recipe_id] : undefined}
              onAddComponent={() => setComponentTarget(recipe)}
            />
          ))
        )}
      </IonContent>

      {/* Create recipe form (Req 11.1) */}
      <IonModal
        isOpen={showCreate}
        onDidDismiss={() => {
          setShowCreate(false);
          setPrefillName("");
        }}
      >
        {showCreate && (
          <CreateRecipeForm
            initialName={prefillName}
            onCancel={() => setShowCreate(false)}
            onSaved={() => {
              setShowCreate(false);
              void load();
            }}
          />
        )}
      </IonModal>

      {/* Scan/upload a recipe → confirm-and-edit → create (Req 15.6) */}
      <IonModal isOpen={scanOpen} onDidDismiss={() => setScanOpen(false)}>
        {scanOpen && (
          <ScanRecipeForm
            onCancel={() => setScanOpen(false)}
            onSaved={() => {
              setScanOpen(false);
              void load();
            }}
          />
        )}
      </IonModal>

      {/* Add component form (Req 11.2) */}
      <IonModal isOpen={componentTarget !== null} onDidDismiss={() => setComponentTarget(null)}>
        {componentTarget && (
          <AddComponentForm
            recipe={componentTarget}
            onCancel={() => setComponentTarget(null)}
            onSaved={() => {
              setComponentTarget(null);
              void load();
            }}
          />
        )}
      </IonModal>
    </IonPage>
  );
}

interface RecipeCardProps {
  recipe: Recipe;
  /** The Owner-only cost breakdown, when resolvable (Req 11.4, 11.7). */
  cost: RecipeCost | undefined;
  onAddComponent: () => void;
}

/** One recipe rendered as a card with its yield and (Owner-only) cost per unit. */
function RecipeCard({ recipe, cost, onAddComponent }: RecipeCardProps): JSX.Element {
  return (
    <IonCard>
      <IonCardHeader>
        <IonCardSubtitle>Yield {recipe.yield_per_batch} per batch</IonCardSubtitle>
        <IonCardTitle style={{ fontSize: "1.1rem" }}>{recipe.name}</IonCardTitle>
      </IonCardHeader>
      <IonCardContent>
        {/* Cost per unit is only ever present for Owner (Req 11.7). */}
        {cost ? (
          <IonText>
            <p style={{ margin: "0 0 8px" }}>
              <strong>Cost per unit: {formatMoney(cost.unit_cost)}</strong>
            </p>
            <IonNote color="medium">
              Ingredients {formatMoney(cost.ingredient_cost)} · Packaging{" "}
              {formatMoney(cost.packaging_cost)}
            </IonNote>
          </IonText>
        ) : null}

        <div style={{ marginTop: 12 }}>
          <IonButton size="small" fill="outline" onClick={onAddComponent}>
            Add component
          </IonButton>
        </div>
      </IonCardContent>
    </IonCard>
  );
}

interface CreateRecipeFormProps {
  /** Optional name to prefill (interconnect #4). */
  initialName?: string;
  onCancel: () => void;
  onSaved: () => void;
}

/** The create-recipe form (Req 11.1) presented in a modal. */
function CreateRecipeForm({ initialName, onCancel, onSaved }: CreateRecipeFormProps): JSX.Element {
  const [name, setName] = useState<string>(initialName ?? "");
  const [yieldPerBatch, setYieldPerBatch] = useState<string>("");
  const [busy, setBusy] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async (): Promise<void> => {
    setError(null);
    // Light client checks for fast feedback; the Service_Layer is authoritative
    // (name 1–100 chars, yield > 0 and ≤ 999,999 — Req 11.1, 11.5).
    if (!name.trim()) {
      setError("A recipe name is required.");
      return;
    }
    const yieldNum = Number(yieldPerBatch);
    if (yieldPerBatch.trim() === "" || !Number.isFinite(yieldNum) || yieldNum <= 0) {
      setError("Yield per batch must be greater than 0.");
      return;
    }
    setBusy(true);
    try {
      await recipesApi.create({ name: name.trim(), yield_per_batch: yieldNum }); // Req 11.1
      onSaved();
    } catch (err) {
      setError(messageFor(err, "We couldn't create that recipe. Please try again."));
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <IonHeader>
        <IonToolbar>
          <IonTitle>New recipe</IonTitle>
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
              maxlength={100}
              placeholder="e.g. Chocolate chip cookies"
              onIonInput={(e) => setName(e.detail.value ?? "")}
            />
          </IonItem>
          <IonItem>
            <IonLabel position="stacked">Yield per batch</IonLabel>
            <IonInput
              type="number"
              inputmode="numeric"
              min={1}
              max={999999}
              value={yieldPerBatch}
              placeholder="e.g. 24"
              onIonInput={(e) => setYieldPerBatch(e.detail.value ?? "")}
            />
          </IonItem>
        </IonList>

        {error && (
          <IonItem lines="none">
            <IonText color="danger">{error}</IonText>
          </IonItem>
        )}

        <IonButton expand="block" disabled={busy} onClick={submit}>
          {busy ? <IonSpinner name="dots" /> : "Save recipe"}
        </IonButton>
      </IonContent>
    </>
  );
}

interface AddComponentFormProps {
  recipe: Recipe;
  onCancel: () => void;
  onSaved: () => void;
}

/** The add-component form (Req 11.2): pick an inventory item, quantity, and type. */
function AddComponentForm({ recipe, onCancel, onSaved }: AddComponentFormProps): JSX.Element {
  const [items, setItems] = useState<InventoryItem[]>([]);
  const [loadingItems, setLoadingItems] = useState<boolean>(true);
  const [itemName, setItemName] = useState<string>("");
  const [quantity, setQuantity] = useState<string>("");
  const [type, setType] = useState<RecipeComponentType>("ingredient");
  const [busy, setBusy] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);

  // Load inventory items so the User associates an existing item (Req 11.2).
  useEffect(() => {
    let active = true;
    (async () => {
      setLoadingItems(true);
      try {
        const res = await inventoryApi.list();
        const flat = (res.categories ?? []).flatMap((group) => group.items);
        if (active) setItems(flat);
      } catch (err) {
        if (active) setError(messageFor(err, "We couldn't load inventory items."));
      } finally {
        if (active) setLoadingItems(false);
      }
    })();
    return () => {
      active = false;
    };
  }, []);

  // The unit of the selected inventory item, shown alongside the quantity input.
  const selectedItem = items.find((it) => it.name === itemName);

  const submit = async (): Promise<void> => {
    setError(null);
    if (!itemName) {
      setError("Select an inventory item.");
      return;
    }
    const qtyNum = Number(quantity);
    if (quantity.trim() === "" || !Number.isFinite(qtyNum) || qtyNum <= 0) {
      setError("Quantity must be greater than 0.");
      return;
    }
    setBusy(true);
    try {
      // Associate the inventory item as a recipe component (Req 11.2); the
      // Service_Layer validates the quantity range (Req 11.6).
      await recipesApi.addComponent(recipe.recipe_id, {
        item_name: itemName,
        quantity: quantity.trim(),
        component_type: type,
      });
      onSaved();
    } catch (err) {
      setError(messageFor(err, "We couldn't add that component. Please try again."));
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <IonHeader>
        <IonToolbar>
          <IonTitle>Add component</IonTitle>
          <IonButtons slot="end">
            <IonButton onClick={onCancel}>Cancel</IonButton>
          </IonButtons>
        </IonToolbar>
      </IonHeader>
      <IonContent>
        <IonList>
          <IonItem lines="none">
            <IonNote color="medium">To {recipe.name}</IonNote>
          </IonItem>

          {loadingItems ? (
            <IonItem lines="none">
              <IonSpinner name="dots" />
            </IonItem>
          ) : items.length === 0 ? (
            <IonItem lines="none">
              <IonText color="medium">
                Add an inventory item first, then attach it to this recipe.
              </IonText>
            </IonItem>
          ) : (
            <IonItem>
              <IonSelect
                label="Inventory item"
                labelPlacement="stacked"
                value={itemName}
                placeholder="Choose an item"
                onIonChange={(e) => setItemName(e.detail.value as string)}
              >
                {items.map((item) => (
                  <IonSelectOption key={item.item_id} value={item.name}>
                    {item.name}
                  </IonSelectOption>
                ))}
              </IonSelect>
            </IonItem>
          )}

          <IonItem>
            <IonLabel position="stacked">
              Quantity{selectedItem ? ` (${selectedItem.unit})` : ""}
            </IonLabel>
            <IonInput
              type="number"
              inputmode="decimal"
              min={0}
              max={999999}
              value={quantity}
              placeholder="0.00"
              onIonInput={(e) => setQuantity(e.detail.value ?? "")}
            />
          </IonItem>

          <IonItem>
            <IonSelect
              label="Type"
              value={type}
              interface="popover"
              onIonChange={(e) => setType(e.detail.value as RecipeComponentType)}
            >
              <IonSelectOption value="ingredient">Ingredient</IonSelectOption>
              <IonSelectOption value="packaging">Packaging</IonSelectOption>
            </IonSelect>
          </IonItem>
        </IonList>

        {error && (
          <IonItem lines="none">
            <IonText color="danger">{error}</IonText>
          </IonItem>
        )}

        <IonButton
          expand="block"
          disabled={busy || loadingItems || items.length === 0}
          onClick={submit}
        >
          {busy ? <IonSpinner name="dots" /> : "Add component"}
        </IonButton>
      </IonContent>
    </>
  );
}

// =============================================================================
// Scan / upload recipe → confirm-and-edit → create (Req 15.6)
// =============================================================================

interface ScanRecipeFormProps {
  onCancel: () => void;
  onSaved: () => void | Promise<void>;
}

/** One editable recipe-component row (ingredient or packaging). */
interface ScanComponentState {
  item_name: string;
  quantity: string;
  unit: string;
}

/** Read a value from an unknown record as a display string (blank when absent). */
function draftStr(record: Record<string, unknown>, key: string): string {
  const v = record[key];
  if (v === undefined || v === null) return "";
  return String(v);
}

/** Read an array of records from an unknown record (empty when absent/typed wrong). */
function draftRecords(record: Record<string, unknown>, key: string): Record<string, unknown>[] {
  const v = record[key];
  if (!Array.isArray(v)) return [];
  return v.filter((el): el is Record<string, unknown> => typeof el === "object" && el !== null);
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

/** Map extracted component records into editable rows. */
function componentsFrom(records: Record<string, unknown>[]): ScanComponentState[] {
  return records.map((c) => ({
    item_name: draftStr(c, "item_name"),
    quantity: draftStr(c, "quantity"),
    unit: draftStr(c, "unit"),
  }));
}

/**
 * Scan or upload a recipe image and turn it into a recipe (Req 15.6), following
 * the confirm-and-edit pattern from the Inventory "Scan receipt" flow (never a
 * chat):
 *
 *  1. Pick an image — an `<input type="file" accept="image/*"
 *     capture="environment">` so mobile offers the camera and desktop offers a
 *     file picker.
 *  2. On select we call `ingestion.extract("recipe", file)`; a spinner shows
 *     while extracting and any failure (400/422) surfaces inline with nothing
 *     saved.
 *  3. The returned draft renders as an **editable form** — recipe name, yield
 *     per batch, plus editable Ingredients and Packaging lists (each row:
 *     item name, quantity, unit; add/remove rows).
 *  4. **Confirm** → `ingestion.confirm({ doc_type: "recipe", draft })`; on
 *     success we close and refresh the list. **Discard/Cancel** is client-side
 *     only.
 */
function ScanRecipeForm({ onCancel, onSaved }: ScanRecipeFormProps): JSX.Element {
  const [extracting, setExtracting] = useState<boolean>(false);
  const [extractError, setExtractError] = useState<string | null>(null);

  // `null` = no draft yet (show the picker). Non-null = show the editable form.
  const [draft, setDraft] = useState<{
    name: string;
    yieldPerBatch: string;
    ingredients: ScanComponentState[];
    packaging: ScanComponentState[];
  } | null>(null);

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
      // Req 15.1: upload → typed recipe draft. On >10 MB / unsupported format
      // (400) or extraction failure (422) this throws and we surface it inline —
      // nothing is saved.
      const res = await ingestion.extract("recipe", file);
      const d = res.draft ?? {};
      setDraft({
        name: draftStr(d, "name"),
        yieldPerBatch: draftStr(d, "yield_per_batch"),
        ingredients: componentsFrom(draftRecords(d, "ingredients")),
        packaging: componentsFrom(draftRecords(d, "packaging")),
      });
    } catch (err) {
      setDraft(null);
      setExtractError(messageFor(err, "We couldn't read that recipe. Please try another image."));
    } finally {
      setExtracting(false);
    }
  }, []);

  const submit = async (): Promise<void> => {
    if (!draft) return;
    setConfirmError(null);
    setBusy(true);
    try {
      const toPayload = (rows: ScanComponentState[]) =>
        rows.map((c) => ({
          item_name: strOrUndefined(c.item_name),
          quantity: numOrUndefined(c.quantity),
          unit: strOrUndefined(c.unit),
        }));
      const payload: Record<string, unknown> = {
        name: strOrUndefined(draft.name),
        yield_per_batch: numOrUndefined(draft.yieldPerBatch),
        ingredients: toPayload(draft.ingredients),
        packaging: toPayload(draft.packaging),
      };
      // Req 15.6/15.10: persist via recipe create; the backend persists nothing
      // on domain failure and reports it here.
      await ingestion.confirm({ doc_type: "recipe", draft: payload });
      await onSaved();
    } catch (err) {
      setConfirmError(messageFor(err, "We couldn't save this recipe. Please review and try again."));
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <IonHeader>
        <IonToolbar>
          <IonTitle>Scan recipe</IonTitle>
          <IonButtons slot="end">
            {/* Discard is client-side only — nothing is persisted until confirm. */}
            <IonButton onClick={onCancel}>Cancel</IonButton>
          </IonButtons>
        </IonToolbar>
      </IonHeader>
      <IonContent className="ion-padding">
        {draft === null ? (
          // ── Picker: take a photo or upload an image ──
          <>
            <IonText color="medium">
              <p>
                Take a photo of a recipe or upload one. We'll pull out the name,
                yield, and components so you can review and save it.
              </p>
            </IonText>
            <IonItem>
              <IonLabel position="stacked">Recipe image</IonLabel>
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
          // ── Editable recipe form (confirm-and-edit) ──
          <>
            <IonList>
              <IonItem>
                <IonLabel position="stacked">Name</IonLabel>
                <IonInput
                  value={draft.name}
                  maxlength={100}
                  placeholder="e.g. Chocolate chip cookies"
                  onIonInput={(e) =>
                    setDraft((prev) => (prev ? { ...prev, name: e.detail.value ?? "" } : prev))
                  }
                />
              </IonItem>
              <IonItem>
                <IonLabel position="stacked">Yield per batch</IonLabel>
                <IonInput
                  type="number"
                  inputmode="numeric"
                  min={1}
                  max={999999}
                  value={draft.yieldPerBatch}
                  placeholder="e.g. 24"
                  onIonInput={(e) =>
                    setDraft((prev) =>
                      prev ? { ...prev, yieldPerBatch: e.detail.value ?? "" } : prev
                    )
                  }
                />
              </IonItem>
            </IonList>

            <ScanComponentListEditor
              title="Ingredients"
              rows={draft.ingredients}
              onChange={(rows) =>
                setDraft((prev) => (prev ? { ...prev, ingredients: rows } : prev))
              }
            />
            <ScanComponentListEditor
              title="Packaging"
              rows={draft.packaging}
              onChange={(rows) =>
                setDraft((prev) => (prev ? { ...prev, packaging: rows } : prev))
              }
            />

            {confirmError ? (
              <IonNote color="danger" style={{ display: "block", margin: "12px 0" }}>
                {confirmError}
              </IonNote>
            ) : null}

            <div style={{ display: "flex", gap: 8, marginTop: 12 }}>
              <IonButton
                expand="block"
                style={{ flex: 1 }}
                disabled={busy}
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

/** A reusable editor for a list of recipe components (ingredient or packaging). */
function ScanComponentListEditor({
  title,
  rows,
  onChange,
}: {
  title: string;
  rows: ScanComponentState[];
  onChange: (rows: ScanComponentState[]) => void;
}): JSX.Element {
  const update = (i: number, patch: Partial<ScanComponentState>) =>
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
              <IonLabel>
                {title.replace(/s$/, "")} {i + 1}
              </IonLabel>
              <IonButton
                slot="end"
                fill="clear"
                size="small"
                color="danger"
                onClick={() => remove(i)}
              >
                Remove
              </IonButton>
            </IonItemDivider>
            <IonItem>
              <IonLabel position="stacked">Item name</IonLabel>
              <IonInput
                value={r.item_name}
                placeholder="e.g. All-purpose flour"
                onIonInput={(e) => update(i, { item_name: e.detail.value ?? "" })}
              />
            </IonItem>
            <IonItem>
              <IonLabel position="stacked">Quantity</IonLabel>
              <IonInput
                type="number"
                inputmode="decimal"
                value={r.quantity}
                placeholder="0.00"
                onIonInput={(e) => update(i, { quantity: e.detail.value ?? "" })}
              />
            </IonItem>
            <IonItem>
              <IonLabel position="stacked">Unit</IonLabel>
              <IonInput
                value={r.unit}
                placeholder="e.g. kg"
                onIonInput={(e) => update(i, { unit: e.detail.value ?? "" })}
              />
            </IonItem>
          </IonItemGroup>
        ))
      )}
    </IonList>
  );
}

export default Recipes;
