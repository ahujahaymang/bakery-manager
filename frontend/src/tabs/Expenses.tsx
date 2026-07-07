/**
 * Expenses — the Expenses_Surface (design §"Frontend structure" →
 * `tabs/Expenses.tsx`, "Expenses is Owner-only").
 *
 * An Owner-only surface backed by the `/api/v1/expenses` endpoints:
 *
 *  - **Create (Req 14.1, 14.3)** — a form capturing an amount, a category
 *    chosen from the predefined expense category list (mirrors
 *    `PurchaseExpense.CATEGORIES` in `app/models.py`), an expense date, an
 *    optional description (≤ 500 chars), an optional vendor, and a
 *    capital-asset flag. Submitting posts the expense, which the Service_Layer
 *    creates and echoes back. Field-level validation (amount range, category
 *    membership, description length — Req 14.2) is enforced server-side; errors
 *    surface inline.
 *  - **Category + date-range filter (Req 14.4)** — filter the list by category
 *    together with an inclusive start-date-to-end-date range. Only expenses
 *    whose category matches and whose date falls within the inclusive range are
 *    displayed.
 *  - **Invalid-range handling (Req 14.5)** — if the start date is later than the
 *    end date, the filter is rejected client-side: no request is issued, the
 *    displayed records are left unchanged, and an invalid-date-range error is
 *    shown.
 *  - **Owner-only surface (Req 14.6)** — the TabBar already hides Expenses for
 *    Staff / Sell_Mode, but this surface also guards gracefully: a non-Owner
 *    session sees an access-restricted notice rather than the expense data. The
 *    Backend independently enforces the permission (Req 5.4, 5.6); the UI gate
 *    is a convenience, not the security boundary.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  IonButton,
  IonButtons,
  IonCheckbox,
  IonContent,
  IonDatetime,
  IonDatetimeButton,
  IonHeader,
  IonInput,
  IonItem,
  IonLabel,
  IonList,
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
  expenses as expensesApi,
  type Expense,
  type ExpenseCreateBody,
  type ExpenseListQuery,
  type Money,
} from "../api/endpoints";
import { useAuth } from "../auth/AuthContext";

/**
 * The predefined expense categories, mirroring `PurchaseExpense.CATEGORIES` in
 * `app/models.py`. The Service_Layer rejects any category outside this set
 * (Req 14.2); constraining the UI to these values keeps client input in the
 * valid space (Req 14.1).
 */
export const EXPENSE_CATEGORIES = [
  "ingredients",
  "packaging",
  "equipment",
  "utilities",
  "rent",
  "marketing",
  "other",
] as const;

export type ExpenseCategory = (typeof EXPENSE_CATEGORIES)[number];

/** The category-filter options, including an "all categories" sentinel. */
type CategoryFilter = ExpenseCategory | "all";

export function Expenses(): JSX.Element {
  const { role } = useAuth();
  const isOwner = role === "owner";

  const [rows, setRows] = useState<Expense[]>([]);
  const [loading, setLoading] = useState<boolean>(false);
  const [listError, setListError] = useState<string | null>(null);

  // ── Filters (Req 14.4, 14.5) ──
  const [categoryFilter, setCategoryFilter] = useState<CategoryFilter>("all");
  const [startDate, setStartDate] = useState<string | null>(null);
  const [endDate, setEndDate] = useState<string | null>(null);
  const [filterError, setFilterError] = useState<string | null>(null);

  // ── Create form (Req 14.1) ──
  const [showCreate, setShowCreate] = useState<boolean>(false);

  /**
   * True when a start/end range is set but the start date is later than the end
   * date — an invalid range (Req 14.5). When invalid, we neither issue a request
   * nor alter the displayed records; we only surface the error.
   */
  const rangeInvalid = useMemo(
    () => Boolean(startDate && endDate && startDate > endDate),
    [startDate, endDate]
  );

  /** Build the list query from the active filters (Req 14.4). */
  const listQuery = useMemo<ExpenseListQuery>(() => {
    const q: ExpenseListQuery = {};
    if (categoryFilter !== "all") q.category = categoryFilter;
    if (startDate) q.start_date = startDate;
    if (endDate) q.end_date = endDate;
    return q;
  }, [categoryFilter, startDate, endDate]);

  const refresh = useCallback(async () => {
    // Reject an invalid date range: do not request, leave records unchanged,
    // surface an error (Req 14.5).
    if (startDate && endDate && startDate > endDate) {
      setFilterError("The start date must be on or before the end date.");
      return;
    }
    setFilterError(null);
    setLoading(true);
    setListError(null);
    try {
      const data = await expensesApi.list(listQuery);
      setRows(data);
    } catch (err) {
      setListError(messageFor(err, "Could not load expenses."));
    } finally {
      setLoading(false);
    }
  }, [listQuery, startDate, endDate]);

  useEffect(() => {
    // Only an Owner may read expenses (Req 14.6); avoid a request that the
    // Backend would reject with 403 for a non-Owner session.
    if (isOwner) void refresh();
  }, [isOwner, refresh]);

  // Owner-only surface (Req 14.6). The TabBar hides Expenses for Staff, but if
  // this surface is reached by a non-Owner session, degrade gracefully.
  if (!isOwner) {
    return (
      <IonPage>
        <IonHeader>
          <IonToolbar>
            <IonTitle>Expenses</IonTitle>
          </IonToolbar>
        </IonHeader>
        <IonContent className="ion-padding">
          <IonText color="medium">
            <p style={{ textAlign: "center", marginTop: 24 }}>
              Expenses are available to the business owner only.
            </p>
          </IonText>
        </IonContent>
      </IonPage>
    );
  }

  return (
    <IonPage>
      <TabTour
        tabKey="expenses"
        title="Expenses"
        intro="Record where your money goes."
        points={[
          "Log expenses by category with a date and amount.",
          "Filter by category and date range to review spending.",
        ]}
      />
      <IonHeader>
        <IonToolbar>
          <IonTitle>Expenses</IonTitle>
          <IonButtons slot="end">
            <IonButton onClick={() => setShowCreate(true)}>Add expense</IonButton>
          </IonButtons>
        </IonToolbar>
      </IonHeader>

      <IonContent className="ion-padding">
        {/* ── Filters (Req 14.4, 14.5) ── */}
        <IonList>
          <IonItem>
            <IonSelect
              label="Category"
              value={categoryFilter}
              onIonChange={(e) => setCategoryFilter(e.detail.value as CategoryFilter)}
              interface="popover"
            >
              <IonSelectOption value="all">All categories</IonSelectOption>
              {EXPENSE_CATEGORIES.map((cat) => (
                <IonSelectOption key={cat} value={cat}>
                  {cat}
                </IonSelectOption>
              ))}
            </IonSelect>
          </IonItem>

          <IonItem>
            <IonLabel>Start date</IonLabel>
            {startDate ? (
              <>
                <IonDatetimeButton datetime="expenses-start-filter" slot="end" />
                <IonButton
                  slot="end"
                  fill="clear"
                  size="small"
                  onClick={() => setStartDate(null)}
                >
                  Clear
                </IonButton>
                <IonModal keepContentsMounted>
                  <IonDatetime
                    id="expenses-start-filter"
                    presentation="date"
                    value={startDate}
                    onIonChange={(e) =>
                      setStartDate(isoDateOf(e.detail.value as string | null))
                    }
                  />
                </IonModal>
              </>
            ) : (
              <IonButton
                slot="end"
                fill="outline"
                size="small"
                onClick={() => setStartDate(todayIso())}
              >
                Set start
              </IonButton>
            )}
          </IonItem>

          <IonItem>
            <IonLabel>End date</IonLabel>
            {endDate ? (
              <>
                <IonDatetimeButton datetime="expenses-end-filter" slot="end" />
                <IonButton
                  slot="end"
                  fill="clear"
                  size="small"
                  onClick={() => setEndDate(null)}
                >
                  Clear
                </IonButton>
                <IonModal keepContentsMounted>
                  <IonDatetime
                    id="expenses-end-filter"
                    presentation="date"
                    value={endDate}
                    onIonChange={(e) =>
                      setEndDate(isoDateOf(e.detail.value as string | null))
                    }
                  />
                </IonModal>
              </>
            ) : (
              <IonButton
                slot="end"
                fill="outline"
                size="small"
                onClick={() => setEndDate(todayIso())}
              >
                Set end
              </IonButton>
            )}
          </IonItem>
        </IonList>

        {/* Invalid date-range error (Req 14.5): shown without altering rows. */}
        {rangeInvalid ? (
          <IonNote color="danger" style={{ display: "block", margin: "8px 0" }}>
            The start date must be on or before the end date.
          </IonNote>
        ) : filterError ? (
          <IonNote color="danger" style={{ display: "block", margin: "8px 0" }}>
            {filterError}
          </IonNote>
        ) : null}

        {/* ── Expenses list ── */}
        {loading ? (
          <div style={{ textAlign: "center", marginTop: 24 }}>
            <IonSpinner />
          </div>
        ) : listError ? (
          <IonNote color="danger" style={{ display: "block", textAlign: "center", marginTop: 24 }}>
            {listError}
          </IonNote>
        ) : rows.length === 0 ? (
          <IonText color="medium">
            <p style={{ textAlign: "center", marginTop: 24 }}>No expenses match these filters.</p>
          </IonText>
        ) : (
          <IonList>
            {rows.map((expense) => (
              <ExpenseRow key={expense.expense_id} expense={expense} />
            ))}
          </IonList>
        )}
      </IonContent>

      <CreateExpenseModal
        isOpen={showCreate}
        onDismiss={() => setShowCreate(false)}
        onCreated={() => {
          setShowCreate(false);
          void refresh();
        }}
      />
    </IonPage>
  );
}

interface ExpenseRowProps {
  expense: Expense;
}

/** One expense rendered as a list row with amount, category, date, and flags. */
function ExpenseRow({ expense }: ExpenseRowProps): JSX.Element {
  const capital = isTrueFlag(expense.is_capital);
  return (
    <IonItem lines="full">
      <IonLabel>
        <h2>
          {formatMoney(expense.amount)}
          {capital ? " · capital asset" : ""}
        </h2>
        <p>
          {expense.category} · {expense.expense_date}
          {expense.vendor_name ? ` · ${expense.vendor_name}` : ""}
        </p>
        {expense.description ? (
          <IonNote color="medium">{expense.description}</IonNote>
        ) : null}
      </IonLabel>
    </IonItem>
  );
}

interface CreateExpenseModalProps {
  isOpen: boolean;
  onDismiss: () => void;
  onCreated: () => void;
}

/** The create-expense form (Req 14.1, 14.3) presented in a modal. */
function CreateExpenseModal({ isOpen, onDismiss, onCreated }: CreateExpenseModalProps): JSX.Element {
  const [amount, setAmount] = useState<string>("");
  const [category, setCategory] = useState<ExpenseCategory>("other");
  const [expenseDate, setExpenseDate] = useState<string>(todayIso());
  const [vendor, setVendor] = useState<string>("");
  const [description, setDescription] = useState<string>("");
  const [isCapital, setIsCapital] = useState<boolean>(false);
  const [submitting, setSubmitting] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);

  // Reset the form each time the modal opens.
  useEffect(() => {
    if (isOpen) {
      setAmount("");
      setCategory("other");
      setExpenseDate(todayIso());
      setVendor("");
      setDescription("");
      setIsCapital(false);
      setSubmitting(false);
      setError(null);
    }
  }, [isOpen]);

  const submit = async () => {
    setError(null);
    // Light client check for fast feedback; the Service_Layer enforces the
    // authoritative amount range and description length (Req 14.2).
    if (amount.trim() === "") {
      setError("Enter an amount.");
      return;
    }
    if (description.length > 500) {
      setError("The description must be 500 characters or fewer.");
      return;
    }
    const body: ExpenseCreateBody = {
      amount: amount.trim(),
      expense_date: expenseDate,
      category, // constrained to a predefined category (Req 14.1)
      is_capital: isCapital, // capital-asset flag (Req 14.3)
    };
    if (vendor.trim()) body.vendor_name = vendor.trim();
    if (description.trim()) body.description = description.trim();

    setSubmitting(true);
    try {
      await expensesApi.create(body); // Req 14.1 — creates the expense record
      onCreated();
    } catch (err) {
      setError(messageFor(err, "The expense could not be recorded."));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <IonModal isOpen={isOpen} onDidDismiss={onDismiss}>
      <IonHeader>
        <IonToolbar>
          <IonTitle>Add expense</IonTitle>
          <IonButtons slot="end">
            <IonButton onClick={onDismiss}>Close</IonButton>
          </IonButtons>
        </IonToolbar>
      </IonHeader>

      <IonContent className="ion-padding">
        <IonList>
          <IonItem>
            <IonInput
              label="Amount"
              labelPlacement="stacked"
              type="number"
              inputmode="decimal"
              min={0.01}
              value={amount}
              placeholder="0.00"
              onIonInput={(e) => setAmount(e.detail.value ?? "")}
            />
          </IonItem>

          <IonItem>
            {/* Category is chosen from the predefined list only (Req 14.1). */}
            <IonSelect
              label="Category"
              labelPlacement="stacked"
              value={category}
              onIonChange={(e) => setCategory(e.detail.value as ExpenseCategory)}
              interface="popover"
            >
              {EXPENSE_CATEGORIES.map((cat) => (
                <IonSelectOption key={cat} value={cat}>
                  {cat}
                </IonSelectOption>
              ))}
            </IonSelect>
          </IonItem>

          <IonItem>
            <IonLabel>Expense date</IonLabel>
            <IonDatetimeButton datetime="expenses-create-date" slot="end" />
            <IonModal keepContentsMounted>
              <IonDatetime
                id="expenses-create-date"
                presentation="date"
                value={expenseDate}
                onIonChange={(e) =>
                  setExpenseDate(isoDateOf(e.detail.value as string | null) ?? todayIso())
                }
              />
            </IonModal>
          </IonItem>

          <IonItem>
            <IonInput
              label="Vendor (optional)"
              labelPlacement="stacked"
              value={vendor}
              onIonInput={(e) => setVendor(e.detail.value ?? "")}
              placeholder="Shop / supplier name"
            />
          </IonItem>

          <IonItem>
            <IonInput
              label="Description (optional)"
              labelPlacement="stacked"
              value={description}
              maxlength={500}
              onIonInput={(e) => setDescription(e.detail.value ?? "")}
              placeholder="e.g. OTG oven 45L"
            />
          </IonItem>

          {/* Capital-asset flag (Req 14.3). */}
          <IonItem>
            <IonCheckbox
              checked={isCapital}
              onIonChange={(e) => setIsCapital(e.detail.checked)}
            >
              Capital asset
            </IonCheckbox>
          </IonItem>
        </IonList>

        {error ? (
          <IonNote color="danger" style={{ display: "block", margin: "8px 0" }}>
            {error}
          </IonNote>
        ) : null}

        <IonButton expand="block" disabled={submitting} onClick={submit}>
          {submitting ? <IonSpinner name="dots" /> : "Save expense"}
        </IonButton>
      </IonContent>
    </IonModal>
  );
}

/** Format a monetary value (INR) for display. */
function formatMoney(value: Money): string {
  const n = Number(value);
  if (Number.isNaN(n)) return String(value);
  return `₹${n.toFixed(2)}`;
}

/**
 * Interpret the backend's SQLite-safe capital-asset flag. `is_capital` is
 * serialized as the string `"true"` / `"false"` (see `PurchaseExpense` in
 * `app/models.py`), so compare case-insensitively against `"true"`.
 */
function isTrueFlag(value: string | null): boolean {
  return typeof value === "string" && value.toLowerCase() === "true";
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

/** Extract a user-facing message from an unknown thrown value. */
function messageFor(err: unknown, fallback: string): string {
  if (err instanceof ApiError) return err.message;
  if (err instanceof Error && err.message) return err.message;
  return fallback;
}

export default Expenses;
