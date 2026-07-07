/**
 * WebAuthn browser-ceremony helpers (Req 4.6–4.8).
 *
 * These wrap the platform `navigator.credentials` API and the base64url
 * encoding the WebAuthn wire format requires. The backend
 * (`app/auth/webauthn_service.py`, exposed at `/api/v1/auth/webauthn/*`) drives
 * the ceremony: it issues the options (challenge, rp, allowCredentials, ...) and
 * verifies the browser's attestation/assertion response. This module only
 * performs the client half: decode server options → call the authenticator →
 * encode the result for transport.
 *
 * Nothing here talks to the network; the AuthContext feeds server options in and
 * posts the encoded result back through the shared API client.
 */

/** True when the current context exposes the WebAuthn platform API (Req 4.6). */
export function isWebAuthnSupported(): boolean {
  return (
    typeof window !== "undefined" &&
    typeof window.PublicKeyCredential !== "undefined" &&
    typeof navigator !== "undefined" &&
    !!navigator.credentials
  );
}

/** Encode an ArrayBuffer as a base64url string (WebAuthn wire format). */
export function bufferToBase64url(buffer: ArrayBuffer): string {
  const bytes = new Uint8Array(buffer);
  let binary = "";
  for (const b of bytes) binary += String.fromCharCode(b);
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

/** Decode a base64url string into an ArrayBuffer (WebAuthn wire format). */
export function base64urlToBuffer(value: string): ArrayBuffer {
  const padded = value.replace(/-/g, "+").replace(/_/g, "/");
  const pad = padded.length % 4 === 0 ? "" : "=".repeat(4 - (padded.length % 4));
  const binary = atob(padded + pad);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
  return bytes.buffer;
}

/**
 * Server-issued registration options, with the challenge/user.id/credential ids
 * still base64url-encoded as they arrive over JSON.
 */
export interface RegistrationOptionsJSON {
  challenge: string;
  rp: { id?: string; name: string };
  user: { id: string; name: string; displayName: string };
  pubKeyCredParams: Array<{ type: "public-key"; alg: number }>;
  timeout?: number;
  attestation?: AttestationConveyancePreference;
  authenticatorSelection?: AuthenticatorSelectionCriteria;
  excludeCredentials?: Array<{ id: string; type: "public-key"; transports?: AuthenticatorTransport[] }>;
}

/** Server-issued authentication options as they arrive over JSON. */
export interface AuthenticationOptionsJSON {
  challenge: string;
  rpId?: string;
  timeout?: number;
  userVerification?: UserVerificationRequirement;
  allowCredentials?: Array<{ id: string; type: "public-key"; transports?: AuthenticatorTransport[] }>;
}

/** The encoded attestation the server verifies to complete registration. */
export interface RegistrationResultJSON {
  id: string;
  rawId: string;
  type: string;
  response: {
    clientDataJSON: string;
    attestationObject: string;
  };
}

/** The encoded assertion the server verifies to complete authentication. */
export interface AuthenticationResultJSON {
  id: string;
  rawId: string;
  type: string;
  response: {
    clientDataJSON: string;
    authenticatorData: string;
    signature: string;
    userHandle: string | null;
  };
}

/**
 * Run the registration (attestation) ceremony with the authenticator and encode
 * the result for the server (Req 4.6).
 */
export async function performRegistration(
  options: RegistrationOptionsJSON
): Promise<RegistrationResultJSON> {
  if (!isWebAuthnSupported()) {
    throw new Error("This device does not support WebAuthn.");
  }

  const publicKey: PublicKeyCredentialCreationOptions = {
    challenge: base64urlToBuffer(options.challenge),
    rp: options.rp,
    user: {
      id: base64urlToBuffer(options.user.id),
      name: options.user.name,
      displayName: options.user.displayName,
    },
    pubKeyCredParams: options.pubKeyCredParams,
    timeout: options.timeout,
    attestation: options.attestation,
    authenticatorSelection: options.authenticatorSelection,
    excludeCredentials: options.excludeCredentials?.map((c) => ({
      id: base64urlToBuffer(c.id),
      type: c.type,
      transports: c.transports,
    })),
  };

  const credential = (await navigator.credentials.create({ publicKey })) as PublicKeyCredential | null;
  if (!credential) {
    throw new Error("WebAuthn registration was cancelled.");
  }

  const response = credential.response as AuthenticatorAttestationResponse;
  return {
    id: credential.id,
    rawId: bufferToBase64url(credential.rawId),
    type: credential.type,
    response: {
      clientDataJSON: bufferToBase64url(response.clientDataJSON),
      attestationObject: bufferToBase64url(response.attestationObject),
    },
  };
}

/**
 * Run the authentication (assertion) ceremony with the authenticator and encode
 * the result for the server (Req 4.7, 4.8).
 */
export async function performAuthentication(
  options: AuthenticationOptionsJSON
): Promise<AuthenticationResultJSON> {
  if (!isWebAuthnSupported()) {
    throw new Error("This device does not support WebAuthn.");
  }

  const publicKey: PublicKeyCredentialRequestOptions = {
    challenge: base64urlToBuffer(options.challenge),
    rpId: options.rpId,
    timeout: options.timeout,
    userVerification: options.userVerification,
    allowCredentials: options.allowCredentials?.map((c) => ({
      id: base64urlToBuffer(c.id),
      type: c.type,
      transports: c.transports,
    })),
  };

  const assertion = (await navigator.credentials.get({ publicKey })) as PublicKeyCredential | null;
  if (!assertion) {
    throw new Error("WebAuthn authentication was cancelled.");
  }

  const response = assertion.response as AuthenticatorAssertionResponse;
  return {
    id: assertion.id,
    rawId: bufferToBase64url(assertion.rawId),
    type: assertion.type,
    response: {
      clientDataJSON: bufferToBase64url(response.clientDataJSON),
      authenticatorData: bufferToBase64url(response.authenticatorData),
      signature: bufferToBase64url(response.signature),
      userHandle: response.userHandle ? bufferToBase64url(response.userHandle) : null,
    },
  };
}
