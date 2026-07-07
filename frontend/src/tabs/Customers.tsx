/**
 * Customers — the Customers_Surface (design §"Frontend structure" → `tabs/Customers.tsx`).
 *
 * A lightweight customer directory backed by the `/api/v1/customers` endpoints:
 *
 *  - **Create (Req 12.1)** — a form capturing a customer name (1–100 chars) and
 *    a phone number (8–15 digits), routed through `customers.create`. The
 *    Service_Layer creates the customer scoped to the current Tenant. Field-level
 *    validation (Req 12.4) and duplicate-phone conflicts (Req 12.3) are enforced
 *    server-side; errors surface inline. A light client check gives fast feedback.
 *  - **Search-as-you-type (Req 12.2)** — the search term is debounced and issued
 *    to `customers.search`, which matches name (partial) or phone and caps at 50
 *    results. Debouncing keeps the surface responsive so matches render well
 *    within the 2-second budget while the user is still typing.
 *  - **Empty-result indication (Req 12.5)** — when a non-empty search matches no
 *    customers of the current Tenant, an explicit empty-result message is shown.
 *
 * Server-side validation and tenant scoping are authoritative (Req 12.1–12.5);
 * the client-side checks here exist only for immediate feedback.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import {
  useIonRouter,
  useIonViewWillEnter,
  IonButton,
  IonButtons,
  IonContent,
  IonHeader,
  IonInput,
  IonItem,
  IonLabel,
  IonList,
  IonModal,
  IonNote,
  IonPage,
  IonSearchbar,
  IonSpinner,
  IonText,
  IonTitle,
  IonToolbar,
} from "@ionic/react";
import { TabTour } from "../components/TabTour";
import { useLocation } from "react-router-dom";
import { ApiError, customers as customersApi, type Customer } from "../api/endpoints";

/**
 * Debounce interval for search-as-you-type. Short enough to feel live while
 * the user is typing, and well inside the 2-second display budget (Req 12.2).
 */
const SEARCH_DEBOUNCE_MS = 300;

/** Extract a user-facing message from an unknown thrown value. */
function messageFor(err: unknown, fallback: string): string {
  if (err instanceof ApiError) return err.message;
  if (err instanceof Error && err.message) return err.message;
  return fallback;
}

export function Customers(): JSX.Element {
  const [term, setTerm] = useState<string>("");
  const [results, setResults] = useState<Customer[]>([]);
  const [searching, setSearching] = useState<boolean>(false);
  const [searchError, setSearchError] = useState<string | null>(null);
  // Tracks whether at least one search has completed, so we only show the
  // empty-result indication after a real, resolved query (Req 12.5) rather
  // than on the initial, untouched surface.
  const [searched, setSearched] = useState<boolean>(false);

  const [createOpen, setCreateOpen] = useState<boolean>(false);
  // Name to prefill the create form with (interconnect #4, set from the `new`
  // query param when another tab sends the user here to add a customer).
  const [prefillName, setPrefillName] = useState<string>("");

  const location = useLocation();
  const router = useIonRouter();

  // Interconnect #4: when arriving with `?new=<name>` (e.g. from the Orders tab's
  // "Add new customer"), auto-open the create form prefilled with the name, then
  // clear the param so it doesn't re-trigger on a later view-enter.
  useIonViewWillEnter(() => {
    const params = new URLSearchParams(location.search);
    const newName = params.get("new");
    if (newName !== null) {
      setPrefillName(newName);
      setCreateOpen(true);
      router.push("/app/customers", "none", "replace");
    }
  }, [location.search]);

  // Guards against out-of-order responses: only the latest issued search may
  // commit its results, so a slow earlier request can't overwrite a newer one.
  const requestSeq = useRef<number>(0);

  const runSearch = useCallback(async (q: string): Promise<void> => {
    const trimmed = q.trim();
    const seq = ++requestSeq.current;

    // An empty term clears the surface back to its initial (un-searched) state.
    if (trimmed === "") {
      setResults([]);
      setSearched(false);
      setSearchError(null);
      setSearching(false);
      return;
    }

    setSearching(true);
    setSearchError(null);
    try {
      const rows = await customersApi.search(trimmed); // Req 12.2
      if (seq !== requestSeq.current) return; // a newer search superseded this one
      setResults(rows);
      setSearched(true);
    } catch (err) {
      if (seq !== requestSeq.current) return;
      setSearchError(messageFor(err, "We couldn't search customers. Please try again."));
      setResults([]);
      setSearched(true);
    } finally {
      if (seq === requestSeq.current) setSearching(false);
    }
  }, []);

  // Debounce the search-as-you-type so each keystroke doesn't fire a request,
  // while still surfacing matches within the 2-second budget (Req 12.2).
  useEffect(() => {
    const handle = window.setTimeout(() => {
      void runSearch(term);
    }, SEARCH_DEBOUNCE_MS);
    return () => window.clearTimeout(handle);
  }, [term, runSearch]);

  const handleCreated = useCallback(
    (created: Customer): void => {
      setCreateOpen(false);
      // Reflect the new customer immediately by searching for it.
      setTerm(created.phone);
    },
    [setTerm]
  );

  const hasTerm = term.trim() !== "";
  const showEmptyResult = hasTerm && searched && !searching && !searchError && results.length === 0;

  return (
    <IonPage>
      <TabTour
        tabKey="customers"
        title="Customers"
        intro="Keep your customer list handy."
        points={[
          "Add a customer's name, phone and address.",
          "Search by name or phone to reuse them on orders and invoices.",
        ]}
      />
      <IonHeader>
        <IonToolbar>
          <IonTitle>Customers</IonTitle>
          <IonButtons slot="end">
            <IonButton
              onClick={() => {
                setPrefillName("");
                setCreateOpen(true);
              }}
            >
              Add customer
            </IonButton>
          </IonButtons>
        </IonToolbar>
      </IonHeader>

      <IonContent>
        {/* Search-as-you-type (Req 12.2). */}
        <IonSearchbar
          value={term}
          debounce={0}
          placeholder="Search by name or phone"
          onIonInput={(e) => setTerm(e.detail.value ?? "")}
        />

        {searching ? (
          <div style={{ display: "flex", justifyContent: "center", padding: "2rem" }}>
            <IonSpinner name="dots" />
          </div>
        ) : searchError ? (
          <div style={{ textAlign: "center", padding: "2rem 1.5rem" }}>
            <IonText color="danger">
              <p>{searchError}</p>
            </IonText>
            <IonButton fill="clear" onClick={() => void runSearch(term)}>
              Retry
            </IonButton>
          </div>
        ) : showEmptyResult ? (
          // Empty-result indication for a search that matched nothing (Req 12.5).
          <div style={{ textAlign: "center", padding: "3rem 1.5rem" }}>
            <IonText color="medium">
              <p>No customers match "{term.trim()}".</p>
              <p>Check the spelling, or add a new customer.</p>
            </IonText>
            <IonButton
              onClick={() => {
                setPrefillName(term.trim());
                setCreateOpen(true);
              }}
            >
              Add customer
            </IonButton>
          </div>
        ) : !hasTerm ? (
          // Initial, untouched state — prompt the user to start searching.
          <div style={{ textAlign: "center", padding: "3rem 1.5rem" }}>
            <IonText color="medium">
              <p>Search your customers by name or phone number.</p>
            </IonText>
          </div>
        ) : (
          <IonList>
            {results.map((customer) => (
              <IonItem key={customer.customer_id}>
                <IonLabel>
                  <h2>{customer.name}</h2>
                  <p>{customer.phone}</p>
                  {customer.address ? <IonNote color="medium">{customer.address}</IonNote> : null}
                </IonLabel>
              </IonItem>
            ))}
          </IonList>
        )}

        {/* Create form (Req 12.1). */}
        <IonModal
          isOpen={createOpen}
          onDidDismiss={() => {
            setCreateOpen(false);
            setPrefillName("");
          }}
        >
          {createOpen && (
            <CreateCustomerForm
              initialName={prefillName}
              onCancel={() => setCreateOpen(false)}
              onCreated={handleCreated}
            />
          )}
        </IonModal>
      </IonContent>
    </IonPage>
  );
}

// =============================================================================
// Create form
// =============================================================================

interface CreateCustomerFormProps {
  /** Optional name to prefill (interconnect #4). */
  initialName?: string;
  onCancel: () => void;
  onCreated: (customer: Customer) => void;
}

/** True when the string is 8–15 digits (Req 12.1, 12.4). */
function isValidPhone(phone: string): boolean {
  return /^\d{8,15}$/.test(phone);
}

function CreateCustomerForm({ initialName, onCancel, onCreated }: CreateCustomerFormProps): JSX.Element {
  const [name, setName] = useState<string>(initialName ?? "");
  const [phone, setPhone] = useState<string>("");
  const [address, setAddress] = useState<string>("");
  const [busy, setBusy] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async (): Promise<void> => {
    setError(null);
    const trimmedName = name.trim();
    const trimmedPhone = phone.trim();

    // Light client checks for fast feedback; the server is authoritative
    // (name 1–100 chars, phone 8–15 digits — Req 12.1, 12.4).
    if (trimmedName === "" || trimmedName.length > 100) {
      setError("Enter a customer name of 1 to 100 characters.");
      return;
    }
    if (!isValidPhone(trimmedPhone)) {
      setError("Enter a phone number of 8 to 15 digits.");
      return;
    }

    setBusy(true);
    try {
      const created = await customersApi.create({
        name: trimmedName,
        phone: trimmedPhone,
        address: address.trim() || null,
      });
      onCreated(created);
    } catch (err) {
      // Surfaces server-side validation (Req 12.4) and duplicate-phone
      // conflicts (Req 12.3) inline.
      setError(messageFor(err, "We couldn't create that customer. Please try again."));
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <IonHeader>
        <IonToolbar>
          <IonTitle>Add customer</IonTitle>
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
              placeholder="e.g. Priya Sharma"
              onIonInput={(e) => setName(e.detail.value ?? "")}
            />
          </IonItem>
          <IonItem>
            <IonLabel position="stacked">Phone</IonLabel>
            <IonInput
              type="tel"
              inputmode="tel"
              value={phone}
              maxlength={15}
              placeholder="e.g. 9876543210"
              onIonInput={(e) => setPhone(e.detail.value ?? "")}
            />
          </IonItem>
          <IonItem>
            <IonLabel position="stacked">Address (optional)</IonLabel>
            <IonInput
              value={address}
              placeholder="Delivery address"
              onIonInput={(e) => setAddress(e.detail.value ?? "")}
            />
          </IonItem>
        </IonList>

        {error && (
          <IonItem lines="none">
            <IonText color="danger">{error}</IonText>
          </IonItem>
        )}

        <IonButton expand="block" disabled={busy} onClick={submit}>
          {busy ? <IonSpinner name="dots" /> : "Save customer"}
        </IonButton>
      </IonContent>
    </>
  );
}

export default Customers;
