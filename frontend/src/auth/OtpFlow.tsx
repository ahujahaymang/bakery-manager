/**
 * OtpFlow — phone entry + OTP verification UI (Req 2.1, 2.2, 2.5).
 *
 * Two-step flow:
 *  1. **Phone step** — collect the phone number and request an OTP
 *     (`AuthContext.requestOtp` → `/api/v1/auth/otp`). Format validation and
 *     delivery failures surface as inline errors; the user can re-request
 *     (Req 2.3, 2.4).
 *  2. **Code step** — collect the delivered code and verify it
 *     (`AuthContext.verifyOtp` → `/api/v1/auth/verify`). On success a device
 *     session is minted and the App opens in the mode for the user's role
 *     (Req 6.4, 6.5); the parent is notified via {@link onVerified}.
 *
 * This component only renders the OTP surface; the AuthContext owns state and
 * transport. Server-side validation is authoritative — client checks here are
 * for fast feedback only.
 */

import { useState } from "react";
import {
  IonButton,
  IonInput,
  IonItem,
  IonLabel,
  IonList,
  IonNote,
  IonSpinner,
  IonText,
} from "@ionic/react";
import { ApiError } from "../api/endpoints";
import { useAuth } from "./AuthContext";

/** A permissive E.164-ish check for fast client feedback (Req 2.3). */
function looksLikePhone(value: string): boolean {
  return /^\+?[0-9]{8,15}$/.test(value.trim());
}

export interface OtpFlowProps {
  /** Called after a Device_Session_Token is minted and the user is signed in. */
  onVerified?: () => void;
}

type Step = "phone" | "code";

export function OtpFlow({ onVerified }: OtpFlowProps): JSX.Element {
  const { requestOtp, verifyOtp, busy } = useAuth();

  const [step, setStep] = useState<Step>("phone");
  const [phone, setPhone] = useState<string>("");
  const [code, setCode] = useState<string>("");
  const [codeLength, setCodeLength] = useState<number | undefined>(undefined);
  const [error, setError] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);

  const handleRequestOtp = async (): Promise<void> => {
    setError(null);
    setInfo(null);
    if (!looksLikePhone(phone)) {
      setError("Enter a valid phone number (8–15 digits, optional +).");
      return;
    }
    try {
      const { delivered, codeLength: len } = await requestOtp(phone.trim());
      setCodeLength(len);
      setStep("code");
      setInfo(
        delivered
          ? "We sent you a code. Enter it below."
          : "Code sent. If it doesn't arrive, you can request a new one."
      );
    } catch (err) {
      setError(messageFor(err, "We couldn't send the code. Please try again."));
    }
  };

  const handleVerify = async (): Promise<void> => {
    setError(null);
    if (!code.trim()) {
      setError("Enter the code we sent you.");
      return;
    }
    try {
      await verifyOtp(phone.trim(), code.trim(), collectDeviceInfo());
      onVerified?.();
    } catch (err) {
      setError(messageFor(err, "That code didn't work. Please try again."));
    }
  };

  const handleResend = async (): Promise<void> => {
    setCode("");
    await handleRequestOtp();
  };

  return (
    <IonList>
      {step === "phone" ? (
        <>
          <IonItem>
            <IonLabel position="stacked">Phone number</IonLabel>
            <IonInput
              type="tel"
              inputmode="tel"
              autocomplete="tel"
              placeholder="+91 90000 00000"
              value={phone}
              onIonInput={(e) => setPhone(e.detail.value ?? "")}
            />
          </IonItem>
          <IonButton expand="block" disabled={busy} onClick={handleRequestOtp}>
            {busy ? <IonSpinner name="dots" /> : "Send code"}
          </IonButton>
        </>
      ) : (
        <>
          <IonItem>
            <IonLabel position="stacked">
              Verification code{codeLength ? ` (${codeLength} digits)` : ""}
            </IonLabel>
            <IonInput
              type="number"
              inputmode="numeric"
              autocomplete="one-time-code"
              maxlength={codeLength ?? 8}
              placeholder="••••"
              value={code}
              onIonInput={(e) => setCode(e.detail.value ?? "")}
            />
          </IonItem>
          <IonButton expand="block" disabled={busy} onClick={handleVerify}>
            {busy ? <IonSpinner name="dots" /> : "Verify"}
          </IonButton>
          <IonButton expand="block" fill="clear" disabled={busy} onClick={handleResend}>
            Resend code
          </IonButton>
          <IonButton
            expand="block"
            fill="clear"
            disabled={busy}
            onClick={() => {
              setStep("phone");
              setInfo(null);
              setError(null);
            }}
          >
            Use a different number
          </IonButton>
        </>
      )}

      {info && (
        <IonItem lines="none">
          <IonNote color="medium">{info}</IonNote>
        </IonItem>
      )}
      {error && (
        <IonItem lines="none">
          <IonText color="danger">{error}</IonText>
        </IonItem>
      )}
    </IonList>
  );
}

/** Best-effort device metadata recorded with the session (device label). */
function collectDeviceInfo(): Record<string, unknown> {
  if (typeof navigator === "undefined") return {};
  return {
    label: navigator.userAgent,
    platform: (navigator as Navigator & { platform?: string }).platform ?? null,
  };
}

/** Extract a user-facing message from an unknown thrown value. */
function messageFor(err: unknown, fallback: string): string {
  if (err instanceof ApiError) return err.message;
  if (err instanceof Error && err.message) return err.message;
  return fallback;
}
