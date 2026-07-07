/**
 * TabTour — a one-time, per-tab info popup shown the first time a signed-in user
 * opens a tab (a lightweight product "tour").
 *
 * Behaviour (per the product decision):
 *  - Shown once per **user account** — the "seen" flag is keyed by the current
 *    user's id in localStorage (`kitchenos.tour.<user_id>.<tabKey>`), so each
 *    user sees each tab's intro exactly once on this device and never again
 *    after dismissing it. (Per-device: a brand-new device would show it again;
 *    that's an acceptable tradeoff for a first-run hint and avoids a backend
 *    round-trip.)
 *  - Never blocks usage: it's a dismissible modal with a single "Got it" action.
 *
 * Rendered near the top of each tab. The modal portals out of the normal flow,
 * so placement within the tab's content does not affect layout.
 */

import {
  IonButton,
  IonContent,
  IonHeader,
  IonIcon,
  IonItem,
  IonLabel,
  IonList,
  IonModal,
  IonNote,
  IonTitle,
  IonToolbar,
} from "@ionic/react";
import { checkmarkCircleOutline } from "ionicons/icons";
import { useEffect, useState } from "react";
import { useAuth } from "../auth/AuthContext";

export interface TabTourProps {
  /** Stable key for the tab, e.g. "sell", "orders". */
  tabKey: string;
  /** Heading shown in the popup, e.g. "Sell". */
  title: string;
  /** One-line intro describing the tab's purpose. */
  intro: string;
  /** Bullet points describing what the user can do here. */
  points: string[];
}

/** localStorage key marking a (user, tab) intro as already seen. */
function seenKey(userId: string, tabKey: string): string {
  return `kitchenos.tour.${userId}.${tabKey}`;
}

export function TabTour({ tabKey, title, intro, points }: TabTourProps): JSX.Element | null {
  const { currentUser } = useAuth();
  const userId = currentUser?.user_id ?? null;
  const [open, setOpen] = useState<boolean>(false);

  useEffect(() => {
    if (!userId) return;
    try {
      if (!window.localStorage.getItem(seenKey(userId, tabKey))) {
        setOpen(true);
      }
    } catch {
      /* storage unavailable — just skip the tour */
    }
  }, [userId, tabKey]);

  const dismiss = (): void => {
    setOpen(false);
    try {
      if (userId) window.localStorage.setItem(seenKey(userId, tabKey), "1");
    } catch {
      /* storage unavailable — nothing to persist */
    }
  };

  if (!userId) return null;

  return (
    <IonModal isOpen={open} onDidDismiss={dismiss} data-testid={`tour-${tabKey}`}>
      <IonHeader>
        <IonToolbar>
          <IonTitle>Welcome to {title}</IonTitle>
        </IonToolbar>
      </IonHeader>
      <IonContent className="ion-padding">
        <IonNote>{intro}</IonNote>
        <IonList>
          {points.map((p, i) => (
            <IonItem key={i} lines={i === points.length - 1 ? "none" : "full"}>
              <IonIcon icon={checkmarkCircleOutline} slot="start" color="primary" />
              <IonLabel className="ion-text-wrap">{p}</IonLabel>
            </IonItem>
          ))}
        </IonList>
        <IonButton expand="block" onClick={dismiss}>
          Got it
        </IonButton>
      </IonContent>
    </IonModal>
  );
}

export default TabTour;
