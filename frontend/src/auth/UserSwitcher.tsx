/**
 * UserSwitcher — switch the active user on a shared device, and elevate to
 * Manage_Mode (Req 4.9, 6.3).
 *
 * Two related flows on one screen:
 *  - **Switch user (Req 4.9)** — when more than one user is registered on a
 *    trusted device, pick a user and re-authenticate with their PIN or WebAuthn
 *    credential *without* OTP. On success the active user changes and the App
 *    re-applies the opening-mode policy (Sell for Staff, Manage for Owner).
 *  - **Elevate to Manage_Mode (Req 6.3)** — from Sell_Mode, unlock full access
 *    with an Owner-role PIN or WebAuthn credential. The backend enforces the
 *    Owner requirement and the 5-attempt / 30s lockout (Req 6.7); a failed
 *    attempt keeps the App in Sell_Mode (Req 6.6).
 *
 * The switcher loads the tenant's users on mount (Owner-gated on the server);
 * for a Staff principal the list call is rejected and only the elevate path is
 * offered.
 */

import { useEffect, useState } from "react";
import {
  IonButton,
  IonButtons,
  IonContent,
  IonHeader,
  IonItem,
  IonLabel,
  IonList,
  IonListHeader,
  IonModal,
  IonNote,
  IonTitle,
  IonToolbar,
} from "@ionic/react";
import { ApiError, type AuthedUserResponse } from "../api/endpoints";
import { useAuth, type UnlockCredential } from "./AuthContext";
import { PinPad } from "./PinPad";
import { WebAuthnButton } from "./WebAuthnButton";

export interface UserSwitcherProps {
  /** Controls modal visibility. */
  isOpen: boolean;
  /** Called when the switcher should close (cancel or completion). */
  onDismiss: () => void;
}

/** What the credential prompt is being collected for. */
type Intent =
  | { kind: "switch"; user: AuthedUserResponse }
  | { kind: "elevate" };

export function UserSwitcher({ isOpen, onDismiss }: UserSwitcherProps): JSX.Element {
  const {
    currentUser,
    knownUsers,
    mode,
    loadUsers,
    switchUser,
    elevateToManage,
  } = useAuth();

  const [intent, setIntent] = useState<Intent | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [listError, setListError] = useState<string | null>(null);

  // Load the tenant's users when the switcher opens (Owner-gated server-side).
  useEffect(() => {
    if (!isOpen) return;
    setError(null);
    setListError(null);
    setIntent(null);
    void loadUsers().catch((err) => {
      // Staff cannot list users; that's expected — only the elevate path shows.
      setListError(messageFor(err, "Only the owner can list users on this device."));
    });
  }, [isOpen, loadUsers]);

  const applyCredential = async (credential: UnlockCredential): Promise<void> => {
    if (!intent) return;
    setError(null);
    try {
      if (intent.kind === "switch") {
        await switchUser(intent.user, credential); // Req 4.9 — no OTP
        onDismiss();
      } else {
        const elevated = await elevateToManage(credential); // Req 6.3
        if (elevated) {
          onDismiss();
        } else {
          // Verification failed — remain in Sell_Mode (Req 6.6).
          setError("Owner verification failed. Staying in Sell mode.");
          setIntent(null);
        }
      }
    } catch (err) {
      setError(messageFor(err, "Authentication failed."));
      setIntent(null);
    }
  };

  const others = knownUsers.filter((u) => u.user_id !== currentUser?.user_id);

  return (
    <IonModal isOpen={isOpen} onDidDismiss={onDismiss}>
      <IonHeader>
        <IonToolbar>
          <IonTitle>{intent ? "Confirm identity" : "Switch user"}</IonTitle>
          <IonButtons slot="end">
            <IonButton onClick={() => (intent ? setIntent(null) : onDismiss())}>
              {intent ? "Back" : "Close"}
            </IonButton>
          </IonButtons>
        </IonToolbar>
      </IonHeader>

      <IonContent className="ion-padding">
        {intent ? (
          <CredentialPrompt
            intent={intent}
            onPin={(pin) => applyCredential({ kind: "pin", pin })}
            onWebAuthnResult={(ok) => {
              if (ok) void applyCredential({ kind: "webauthn" });
            }}
          />
        ) : (
          <IonList>
            {/* Switch to another registered user (Req 4.9). */}
            {others.length > 0 && (
              <>
                <IonListHeader>
                  <IonLabel>Switch to</IonLabel>
                </IonListHeader>
                {others.map((u) => (
                  <IonItem
                    key={u.user_id}
                    button
                    detail
                    onClick={() => setIntent({ kind: "switch", user: u })}
                  >
                    <IonLabel>
                      <h2>{u.name}</h2>
                      <IonNote>{u.role === "owner" ? "Owner" : "Staff"}</IonNote>
                    </IonLabel>
                  </IonItem>
                ))}
              </>
            )}

            {/* Elevate to Manage_Mode from Sell_Mode (Req 6.3). */}
            {mode === "Sell_Mode" && (
              <>
                <IonListHeader>
                  <IonLabel>Owner access</IonLabel>
                </IonListHeader>
                <IonItem button detail onClick={() => setIntent({ kind: "elevate" })}>
                  <IonLabel>
                    <h2>Enter Manage mode</h2>
                    <IonNote>Requires an owner PIN or biometrics</IonNote>
                  </IonLabel>
                </IonItem>
              </>
            )}

            {listError && others.length === 0 && (
              <IonItem lines="none">
                <IonNote color="medium">{listError}</IonNote>
              </IonItem>
            )}
          </IonList>
        )}

        {error ? (
          <IonNote color="danger" style={{ display: "block", textAlign: "center", marginTop: 12 }}>
            {error}
          </IonNote>
        ) : null}
      </IonContent>
    </IonModal>
  );
}

interface CredentialPromptProps {
  intent: Intent;
  onPin: (pin: string) => void;
  onWebAuthnResult: (ok: boolean) => void;
}

/** Collects a PIN or WebAuthn assertion to authorize a switch/elevation. */
function CredentialPrompt({ intent, onPin, onWebAuthnResult }: CredentialPromptProps): JSX.Element {
  const title =
    intent.kind === "switch"
      ? `Unlock as ${intent.user.name}`
      : "Verify owner to manage";

  return (
    <div>
      <WebAuthnButton action="authenticate" onAuthenticated={onWebAuthnResult} />
      {/* Capture the raw PIN (no local verification) so switchUser/elevateToManage
          can verify it against the *target* identity, not the current user. */}
      <PinPad mode="capture" title={title} onCapture={onPin} />
    </div>
  );
}

/** Extract a user-facing message from an unknown thrown value. */
function messageFor(err: unknown, fallback: string): string {
  if (err instanceof ApiError) return err.message;
  if (err instanceof Error && err.message) return err.message;
  return fallback;
}
