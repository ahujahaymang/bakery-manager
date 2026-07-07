/**
 * TabBar — the App_Shell's bottom navigation (design §"Frontend structure" →
 * `tabs/TabBar.tsx`, "role-based tab hiding").
 *
 * It renders **exactly** the eight tabs the App_Shell must present — Sell,
 * Orders, Inventory, Recipes, Customers, Invoices, Expenses, Ask/Insights
 * (Req 1.9) — using Ionic's tab bar, then filters which of those are shown
 * based on the current (role, mode) pair from {@link useAuth} (Req 1.10, 6.1,
 * 6.2, 14.6).
 *
 * Visibility policy (single source of truth: {@link visibleTabs}):
 *  - **Manage_Mode + Owner** exposes every surface (Req 6.2, 5.2).
 *  - **Sell_Mode** (and therefore any Staff session, which always opens in
 *    Sell_Mode — Req 6.4) exposes the Sell_Surface and the surfaces designated
 *    for Staff, and hides every Owner-only surface (Req 6.1). The only fully
 *    Owner-only surface is **Expenses**, which is hidden for Staff / Sell_Mode
 *    (Req 14.6); all other tabs remain visible with server-side field-level
 *    restrictions applied elsewhere (cost/financial omission, Req 10.7/11.7/16.6).
 *
 * These controls are UX affordances only. The Backend independently enforces
 * role permissions on every request (Req 5.6), so hiding a tab is never the
 * primary access control.
 */

import { IonTabBar, IonTabButton, IonIcon, IonLabel } from "@ionic/react";
import {
  cartOutline,
  receiptOutline,
  cubeOutline,
  bookOutline,
  peopleOutline,
  documentTextOutline,
  walletOutline,
  sparklesOutline,
} from "ionicons/icons";
import { useAuth, type AppMode } from "../auth/AuthContext";
import type { UserRole } from "../api/endpoints";

/** Stable identifier for each of the eight required tabs (Req 1.9). */
export type TabKey =
  | "sell"
  | "orders"
  | "inventory"
  | "recipes"
  | "customers"
  | "invoices"
  | "expenses"
  | "insights";

/** Static description of a navigation tab. */
export interface TabDefinition {
  /** Stable key used for routing and React list keys. */
  key: TabKey;
  /** Human-readable label shown under the icon. */
  label: string;
  /** Route path this tab navigates to (matches the App router surfaces). */
  href: string;
  /** ionicons glyph rendered for the tab. */
  icon: string;
  /**
   * True when the tab's surface is Owner-only and must be hidden outside of
   * Manage_Mode (currently only Expenses — Req 14.6, 6.1).
   */
  ownerOnly: boolean;
}

/**
 * The eight tabs the App_Shell always defines, in display order (Req 1.9).
 * Exported so the property/unit tests (task 6.7) can assert the full set and
 * ordering independently of any (role, mode) filtering.
 */
export const TAB_ORDER: readonly TabDefinition[] = [
  { key: "sell", label: "Sell", href: "/app/sell", icon: cartOutline, ownerOnly: false },
  { key: "orders", label: "Orders", href: "/app/orders", icon: receiptOutline, ownerOnly: false },
  { key: "inventory", label: "Inventory", href: "/app/inventory", icon: cubeOutline, ownerOnly: false },
  { key: "recipes", label: "Recipes", href: "/app/recipes", icon: bookOutline, ownerOnly: false },
  { key: "customers", label: "Customers", href: "/app/customers", icon: peopleOutline, ownerOnly: false },
  { key: "invoices", label: "Invoices", href: "/app/invoices", icon: documentTextOutline, ownerOnly: false },
  { key: "expenses", label: "Expenses", href: "/app/expenses", icon: walletOutline, ownerOnly: true },
  { key: "insights", label: "Ask/Insights", href: "/app/insights", icon: sparklesOutline, ownerOnly: false },
] as const;

/**
 * Pure computation of the visible tab set for a given (role, mode) pair.
 *
 * A tab is visible when it is not Owner-only, OR when the session is an Owner
 * operating in Manage_Mode. This means:
 *  - Sell_Mode (any user) and every Staff session hide the Owner-only Expenses
 *    surface while keeping the Staff-permitted surfaces (Req 1.10, 6.1, 14.6).
 *  - Owner + Manage_Mode sees all eight surfaces (Req 6.2).
 *  - A signed-out session (role `null`) never surfaces Owner-only tabs.
 *
 * The returned tabs preserve {@link TAB_ORDER}. This helper holds the whole
 * policy so it can be unit/property tested (task 6.7) without rendering.
 */
export function visibleTabs(
  role: UserRole | null,
  mode: AppMode
): TabDefinition[] {
  const inOwnerManageMode = role === "owner" && mode === "Manage_Mode";
  return TAB_ORDER.filter((tab) => !tab.ownerOnly || inOwnerManageMode);
}

/**
 * Ionic tab bar wired to the auth context. Renders only the tabs permitted for
 * the active (role, mode). Intended to be used as the `slot="bottom"` child of
 * an `IonTabs` in the App shell.
 */
export function TabBar(): JSX.Element {
  const { role, mode } = useAuth();
  const tabs = visibleTabs(role, mode);

  return (
    <IonTabBar slot="bottom">
      {tabs.map((tab) => (
        <IonTabButton key={tab.key} tab={tab.key} href={tab.href}>
          <IonIcon aria-hidden="true" icon={tab.icon} />
          <IonLabel>{tab.label}</IonLabel>
        </IonTabButton>
      ))}
    </IonTabBar>
  );
}

export default TabBar;
