/**
 * PinPad — numeric PIN entry / set UI (Req 4.1–4.5).
 *
 * A single reusable keypad used in two modes:
 *  - `mode="set"` — first-run PIN creation on a trusted device. Enforces the
 *    4–8 numeric-digit rule client-side (Req 4.1, 4.2) with a confirm step, then
 *    calls `AuthContext.setPin`.
 *  - `mode="verify"` — unlock/verify against the stored PIN (Req 4.3, 4.4). On an
 *    incorrect PIN the parent decides what to do with the `false` result; the
 *    backend enforces the 5-attempt / 300s lockout and returns a `locked_out`
 *    error which surfaces inline (Req 4.5).
 *  - `mode="capture"` — collect a format-valid PIN and hand it back via
 *    {@link PinPadProps.onCapture} without performing any verification. Used by
 *    the user-switcher / Manage_Mode elevation (Req 4.9, 6.3), where the raw PIN
 *    must be verified against the *target* identity by the AuthContext action
 *    rather than against the current user here.
 *
 * The keypad itself is presentational; all persistence and verification go
 * through the AuthContext, which is authoritative alongside the server.
 */

import { useState } from "react";
import {
  IonButton,
  IonIcon,
  IonNote,
  IonSpinner,
  IonText,
} from "@ionic/react";
import { backspaceOutline } from "ionicons/icons";
import { ApiError } from "../api/endpoints";
import { useAuth } from "./AuthContext";

/** PIN length bounds (Req 4.1, 4.2). */
export const MIN_PIN_LENGTH = 4;
export const MAX_PIN_LENGTH = 8;

/** True when `value` is 4–8 numeric digits (Req 4.1, 4.2). */
export function isValidPin(value: string): boolean {
  return /^[0-9]{4,8}$/.test(value);
}

export interface PinPadProps {
  /**
   * `set` creates a new PIN (with confirm); `verify` checks the stored PIN;
   * `capture` returns a format-valid PIN without verifying it.
   */
  mode: "set" | "verify" | "capture";
  /** Called after a PIN is successfully set (`set` mode). */
  onPinSet?: () => void;
  /** Called with the verification result (`verify` mode). */
  onVerified?: (ok: boolean) => void;
  /** Called with the entered PIN (`capture` mode) — no verification performed. */
  onCapture?: (pin: string) => void;
  /** Optional heading shown above the dots. */
  title?: string;
}

const KEYS = ["1", "2", "3", "4", "5", "6", "7", "8", "9"];

export function PinPad({ mode, onPinSet, onVerified, onCapture, title }: PinPadProps): JSX.Element {
  const { setPin, verifyPin, busy } = useAuth();

  const [entry, setEntry] = useState<string>("");
  const [firstEntry, setFirstEntry] = useState<string | null>(null); // set-mode confirm
  const [error, setError] = useState<string | null>(null);

  const confirming = mode === "set" && firstEntry !== null;

  const append = (digit: string): void => {
    setError(null);
    setEntry((prev) => (prev.length >= MAX_PIN_LENGTH ? prev : prev + digit));
  };

  const backspace = (): void => {
    setError(null);
    setEntry((prev) => prev.slice(0, -1));
  };

  const submit = async (): Promise<void> => {
    if (!isValidPin(entry)) {
      setError(`PIN must be ${MIN_PIN_LENGTH}–${MAX_PIN_LENGTH} digits.`);
      return;
    }

    if (mode === "capture") {
      // Hand the raw, format-valid PIN back; verification happens elsewhere.
      onCapture?.(entry);
      setEntry("");
      return;
    }

    if (mode === "set") {
      if (!confirming) {
        // Capture the first entry and ask the user to confirm it.
        setFirstEntry(entry);
        setEntry("");
        return;
      }
      if (entry !== firstEntry) {
        setError("The PINs didn't match. Start again.");
        setFirstEntry(null);
        setEntry("");
        return;
      }
      try {
        await setPin(entry);
        onPinSet?.();
      } catch (err) {
        setError(messageFor(err, "We couldn't save your PIN. Please try again."));
        setFirstEntry(null);
        setEntry("");
      }
      return;
    }

    // verify mode
    try {
      const ok = await verifyPin(entry);
      setEntry("");
      if (!ok) setError("Incorrect PIN.");
      onVerified?.(ok);
    } catch (err) {
      setEntry("");
      setError(messageFor(err, "We couldn't verify your PIN. Please try again."));
    }
  };

  const heading =
    title ??
    (mode === "set"
      ? confirming
        ? "Confirm your PIN"
        : "Set a PIN"
      : "Enter your PIN");

  return (
    <div className="pinpad">
      <IonText>
        <h2 style={{ textAlign: "center" }}>{heading}</h2>
      </IonText>

      {/* PIN dots */}
      <div
        style={{
          display: "flex",
          justifyContent: "center",
          gap: 12,
          margin: "12px 0",
        }}
        aria-label={`${entry.length} of up to ${MAX_PIN_LENGTH} digits entered`}
      >
        {Array.from({ length: MAX_PIN_LENGTH }).map((_, i) => (
          <span
            key={i}
            style={{
              width: 14,
              height: 14,
              borderRadius: "50%",
              background: i < entry.length ? "var(--ion-color-primary)" : "var(--ion-color-medium)",
              opacity: i < entry.length ? 1 : 0.3,
            }}
          />
        ))}
      </div>

      {/* Keypad */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(3, 1fr)",
          gap: 8,
          maxWidth: 320,
          margin: "0 auto",
        }}
      >
        {KEYS.map((k) => (
          <IonButton key={k} fill="outline" onClick={() => append(k)} disabled={busy}>
            {k}
          </IonButton>
        ))}
        <IonButton fill="clear" disabled>
          {/* spacer */}
        </IonButton>
        <IonButton fill="outline" onClick={() => append("0")} disabled={busy}>
          0
        </IonButton>
        <IonButton fill="clear" onClick={backspace} disabled={busy || entry.length === 0}>
          <IonIcon icon={backspaceOutline} />
        </IonButton>
      </div>

      <IonButton
        expand="block"
        style={{ maxWidth: 320, margin: "12px auto 0" }}
        disabled={busy || entry.length < MIN_PIN_LENGTH}
        onClick={submit}
      >
        {busy ? (
          <IonSpinner name="dots" />
        ) : confirming ? (
          "Confirm"
        ) : mode === "set" ? (
          "Next"
        ) : mode === "capture" ? (
          "Continue"
        ) : (
          "Unlock"
        )}
      </IonButton>

      {error ? (
        <IonNote color="danger" style={{ display: "block", textAlign: "center", marginTop: 8 }}>
          {error}
        </IonNote>
      ) : null}
    </div>
  );
}

/** Extract a user-facing message from an unknown thrown value. */
function messageFor(err: unknown, fallback: string): string {
  if (err instanceof ApiError) return err.message;
  if (err instanceof Error && err.message) return err.message;
  return fallback;
}
