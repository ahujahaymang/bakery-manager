/**
 * WebAuthnButton — register or authenticate with a platform authenticator
 * (Req 4.6–4.8).
 *
 * A thin button over the AuthContext WebAuthn actions:
 *  - `action="register"` — register a WebAuthn_Credential as an alternative to
 *    the PIN (Req 4.6).
 *  - `action="authenticate"` — unlock as the current user with a registered
 *    credential (Req 4.7); an authentication failure surfaces inline (Req 4.8).
 *
 * The button hides itself when the platform does not expose WebAuthn, so PIN
 * remains the baseline unlock (design §4: "WebAuthn is optional").
 */

import { useState } from "react";
import { IonButton, IonIcon, IonNote, IonSpinner } from "@ionic/react";
import { fingerPrintOutline } from "ionicons/icons";
import { ApiError } from "../api/endpoints";
import { useAuth } from "./AuthContext";
import { isWebAuthnSupported } from "./webauthn";

export interface WebAuthnButtonProps {
  /** `register` creates a credential; `authenticate` unlocks with one. */
  action: "register" | "authenticate";
  /** Called after a successful registration. */
  onRegistered?: () => void;
  /** Called with the authentication result. */
  onAuthenticated?: (ok: boolean) => void;
  /** Override the button label. */
  label?: string;
  /** Ionic expand behaviour; defaults to `block`. */
  expand?: "block" | "full";
}

export function WebAuthnButton({
  action,
  onRegistered,
  onAuthenticated,
  label,
  expand = "block",
}: WebAuthnButtonProps): JSX.Element | null {
  const { registerWebAuthn, authenticateWebAuthn, busy } = useAuth();
  const [error, setError] = useState<string | null>(null);
  const [working, setWorking] = useState<boolean>(false);

  // Hide entirely where WebAuthn is unavailable — PIN stays the baseline.
  if (!isWebAuthnSupported()) return null;

  const handleClick = async (): Promise<void> => {
    setError(null);
    setWorking(true);
    try {
      if (action === "register") {
        await registerWebAuthn();
        onRegistered?.();
      } else {
        const ok = await authenticateWebAuthn();
        if (!ok) setError("Biometric authentication failed.");
        onAuthenticated?.(ok);
      }
    } catch (err) {
      setError(messageFor(err, "Biometric authentication failed. Try your PIN instead."));
      if (action === "authenticate") onAuthenticated?.(false);
    } finally {
      setWorking(false);
    }
  };

  const text =
    label ?? (action === "register" ? "Set up biometric unlock" : "Unlock with biometrics");

  return (
    <>
      <IonButton expand={expand} fill="outline" disabled={busy || working} onClick={handleClick}>
        {working ? <IonSpinner name="dots" /> : <IonIcon slot="start" icon={fingerPrintOutline} />}
        {text}
      </IonButton>
      {error ? (
        <IonNote color="danger" style={{ display: "block", textAlign: "center", marginTop: 8 }}>
          {error}
        </IonNote>
      ) : null}
    </>
  );
}

/** Extract a user-facing message from an unknown thrown value. */
function messageFor(err: unknown, fallback: string): string {
  if (err instanceof ApiError) return err.message;
  if (err instanceof Error && err.message) return err.message;
  return fallback;
}
