/**
 * AuthContext — the single source of truth for the App's authentication and
 * operating-mode state (design §"Frontend structure" → `src/auth/AuthContext`).
 *
 * It tracks:
 *  - **device token presence** — whether this device holds a Device_Session_Token
 *    (minted by OTP verification, Req 2.5); the raw token is registered with the
 *    API client for the Bearer fallback and persisted for installed-PWA reloads.
 *  - **current user** — the signed-in / active {@link AuthedUserResponse}.
 *  - **role** — `owner` | `staff`, derived from the current user (Req 5.1).
 *  - **app mode** — {@link AppMode} `Sell_Mode` | `Manage_Mode`.
 *
 * And exposes actions that wrap the `/api/v1/auth/*` endpoints (task 6.4):
 * OTP request/verify, PIN set/verify, WebAuthn register/authenticate, user
 * switching on a shared device (Req 4.9), and Manage_Mode elevation (Req 6.3).
 *
 * Mode-on-sign-in policy (Req 6.4, 6.5): a Staff sign-in opens the App in
 * Sell_Mode; an Owner sign-in opens it in Manage_Mode.
 *
 * Server-side authorization remains authoritative (Req 5.6); the role/mode held
 * here only drives UX affordances (tab hiding, elevation prompts).
 */

import React, {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";
import {
  auth,
  http,
  setDeviceToken,
  type AuthedUserResponse,
  type UserRole,
} from "../api/endpoints";
import {
  performAuthentication,
  performRegistration,
  type AuthenticationOptionsJSON,
  type AuthenticationResultJSON,
  type RegistrationOptionsJSON,
} from "./webauthn";

/**
 * The App's two operating states (Req 6.1, 6.2). Values are the domain terms
 * used throughout the requirements so they read clearly in the UI and logs.
 */
export type AppMode = "Sell_Mode" | "Manage_Mode";

/** localStorage key for the Bearer-fallback device token (installed PWA reloads). */
const DEVICE_TOKEN_STORAGE_KEY = "kitchenos.device_token";

/** Read the persisted device token (Bearer fallback), tolerating storage errors. */
function readPersistedToken(): string | null {
  try {
    return window.localStorage.getItem(DEVICE_TOKEN_STORAGE_KEY);
  } catch {
    return null;
  }
}

/** Persist (or clear) the device token, tolerating storage errors. */
function persistToken(token: string | null): void {
  try {
    if (token) window.localStorage.setItem(DEVICE_TOKEN_STORAGE_KEY, token);
    else window.localStorage.removeItem(DEVICE_TOKEN_STORAGE_KEY);
  } catch {
    /* storage unavailable (private mode) — cookie path still works */
  }
}

/** Pick the opening mode for a freshly signed-in user (Req 6.4, 6.5). */
export function modeForRole(role: UserRole): AppMode {
  return role === "owner" ? "Manage_Mode" : "Sell_Mode";
}

/** A credential used to switch users or elevate to Manage_Mode without OTP. */
export type UnlockCredential =
  | { kind: "pin"; pin: string }
  | { kind: "webauthn" };

export interface AuthContextValue {
  // ── State ──
  /** True once a device session token is present on this device (Req 2.5, 2.10). */
  hasDeviceToken: boolean;
  /** The signed-in / active user, or null when signed out. */
  currentUser: AuthedUserResponse | null;
  /** The active user's role, or null when signed out (Req 5.1). */
  role: UserRole | null;
  /** The current operating mode (Req 6.1, 6.2). */
  mode: AppMode;
  /** True while an auth action is in flight. */
  busy: boolean;
  /** True while the initial session restore from a persisted token is in flight. */
  restoring: boolean;
  /** Users registered on this device/tenant, for the switcher (Req 4.9). */
  knownUsers: AuthedUserResponse[];

  // ── OTP device verification (Req 2) ──
  requestOtp: (phone: string) => Promise<{ delivered: boolean; codeLength?: number }>;
  verifyOtp: (
    phone: string,
    code: string,
    deviceInfo?: Record<string, unknown>
  ) => Promise<AuthedUserResponse>;

  // ── PIN (Req 4.1–4.5) ──
  setPin: (pin: string) => Promise<void>;
  verifyPin: (pin: string) => Promise<boolean>;

  // ── WebAuthn (Req 4.6–4.8) ──
  registerWebAuthn: () => Promise<void>;
  authenticateWebAuthn: () => Promise<boolean>;

  // ── User management + switching (Req 4.9, 5.5) ──
  loadUsers: () => Promise<AuthedUserResponse[]>;
  switchUser: (user: AuthedUserResponse, credential: UnlockCredential) => Promise<void>;

  // ── Mode transitions (Req 6.3–6.5) ──
  elevateToManage: (credential: UnlockCredential) => Promise<boolean>;
  returnToSellMode: () => void;

  // ── Session lifecycle ──
  signOut: () => void;
}

const AuthContext = createContext<AuthContextValue | null>(null);

/**
 * Consume the auth context. Throws if used outside {@link AuthProvider} so the
 * mistake surfaces immediately during development.
 */
export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) {
    throw new Error("useAuth must be used within an <AuthProvider>.");
  }
  return ctx;
}

export interface AuthProviderProps {
  children: React.ReactNode;
}

export function AuthProvider({ children }: AuthProviderProps): JSX.Element {
  const [hasDeviceToken, setHasDeviceToken] = useState<boolean>(false);
  const [currentUser, setCurrentUser] = useState<AuthedUserResponse | null>(null);
  const [mode, setMode] = useState<AppMode>("Sell_Mode");
  const [busy, setBusy] = useState<boolean>(false);
  // Start in the "restoring" state only when a persisted token exists — the
  // Gate shows a loader until we know whether the token still resolves a user,
  // so a valid device never flashes the OTP screen on reload (Req 2.10).
  const [restoring, setRestoring] = useState<boolean>(() => readPersistedToken() !== null);
  const [knownUsers, setKnownUsers] = useState<AuthedUserResponse[]>([]);

  // On mount, rehydrate a persisted Bearer-fallback token and, if present,
  // resolve the current user from it so the App reopens without re-verifying an
  // OTP (Req 2.10). An expired/invalid token (401) clears the persisted token
  // and falls through to the OTP sign-in screen.
  useEffect(() => {
    const persisted = readPersistedToken();
    if (!persisted) {
      setRestoring(false);
      return;
    }
    setDeviceToken(persisted);
    setHasDeviceToken(true);

    let cancelled = false;
    void (async () => {
      try {
        const { user } = await auth.session();
        if (cancelled) return;
        setCurrentUser(user);
        setMode(modeForRole(user.role));
      } catch {
        // Token no longer valid — clear it so the OTP flow is shown.
        if (cancelled) return;
        setDeviceToken(null);
        persistToken(null);
        setHasDeviceToken(false);
      } finally {
        if (!cancelled) setRestoring(false);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, []);

  /** Record a freshly minted device token everywhere it needs to live. */
  const adoptToken = useCallback((token: string) => {
    setDeviceToken(token);
    persistToken(token);
    setHasDeviceToken(true);
  }, []);

  const requestOtp = useCallback<AuthContextValue["requestOtp"]>(async (phone) => {
    setBusy(true);
    try {
      await auth.requestOtp({ phone });
      // Reaching here without throwing means the Backend accepted + delivered
      // the OTP (a delivery failure surfaces as an error). The code length is
      // not disclosed by the API, so the UI sizes the input generously.
      return { delivered: true, codeLength: undefined };
    } finally {
      setBusy(false);
    }
  }, []);

  const verifyOtp = useCallback<AuthContextValue["verifyOtp"]>(
    async (phone, code, deviceInfo) => {
      setBusy(true);
      try {
        const session = await auth.verifyOtp({ phone, code, device_info: deviceInfo });
        adoptToken(session.token);
        setCurrentUser(session.user);
        // Open Sell_Mode for Staff, Manage_Mode for Owner on sign-in (Req 6.4, 6.5).
        setMode(modeForRole(session.user.role));
        return session.user;
      } finally {
        setBusy(false);
      }
    },
    [adoptToken]
  );

  const setPin = useCallback<AuthContextValue["setPin"]>(async (pin) => {
    setBusy(true);
    try {
      await auth.setPin({ pin });
    } finally {
      setBusy(false);
    }
  }, []);

  const verifyPin = useCallback<AuthContextValue["verifyPin"]>(async (pin) => {
    setBusy(true);
    try {
      const res = await auth.verifyPin({ pin });
      return res.verified;
    } finally {
      setBusy(false);
    }
  }, []);

  const registerWebAuthn = useCallback<AuthContextValue["registerWebAuthn"]>(async () => {
    setBusy(true);
    try {
      // The backend issues the attestation options, then verifies the result.
      const options = await http.post<RegistrationOptionsJSON>("/auth/webauthn/register/options");
      const attestation = await performRegistration(options);
      await http.post<void>("/auth/webauthn/register", {
        json: attestation,
        responseType: "void",
      });
    } finally {
      setBusy(false);
    }
  }, []);

  /** Drive the assertion ceremony and return the encoded result for the server. */
  const runWebAuthnAssertion = useCallback(async (): Promise<AuthenticationResultJSON> => {
    const options = await http.post<AuthenticationOptionsJSON>("/auth/webauthn/verify/options");
    return performAuthentication(options);
  }, []);

  const authenticateWebAuthn = useCallback<AuthContextValue["authenticateWebAuthn"]>(async () => {
    setBusy(true);
    try {
      const assertion = await runWebAuthnAssertion();
      const res = await http.post<{ verified: boolean }>("/auth/webauthn/verify", {
        json: assertion,
      });
      return res.verified;
    } finally {
      setBusy(false);
    }
  }, [runWebAuthnAssertion]);

  const loadUsers = useCallback<AuthContextValue["loadUsers"]>(async () => {
    setBusy(true);
    try {
      const users = await auth.listUsers();
      setKnownUsers(users);
      return users;
    } finally {
      setBusy(false);
    }
  }, []);

  const switchUser = useCallback<AuthContextValue["switchUser"]>(
    async (user, credential) => {
      setBusy(true);
      try {
        // Switching the active user on a shared device never requires OTP
        // (Req 4.9): re-verify with the target user's PIN or WebAuthn credential.
        let ok = false;
        if (credential.kind === "pin") {
          ok = (await auth.verifyPin({ pin: credential.pin })).verified;
        } else {
          ok = (await http.post<{ verified: boolean }>("/auth/webauthn/verify", {
            json: await runWebAuthnAssertion(),
          })).verified;
        }
        if (!ok) {
          throw new Error("Authentication failed. The active user was not switched.");
        }
        setCurrentUser(user);
        // A switch re-applies the opening-mode policy for the new user.
        setMode(modeForRole(user.role));
      } finally {
        setBusy(false);
      }
    },
    [runWebAuthnAssertion]
  );

  const elevateToManage = useCallback<AuthContextValue["elevateToManage"]>(
    async (credential) => {
      setBusy(true);
      try {
        // Manage_Mode requires an Owner-role PIN or WebAuthn credential (Req 6.3).
        // The backend enforces the Owner role and the 5-attempt / 30s lockout
        // (Req 6.7), returning a `locked_out` error the caller surfaces.
        const base = { user_id: currentUser?.user_id, purpose: "manage" };
        const body =
          credential.kind === "pin"
            ? { ...base, pin: credential.pin }
            : { ...base, webauthn: (await runWebAuthnAssertion()) as unknown as Record<string, unknown> };
        const res = await auth.elevate(body);
        if (res.elevated) {
          setMode("Manage_Mode");
        }
        return res.elevated;
      } finally {
        setBusy(false);
      }
    },
    [runWebAuthnAssertion, currentUser]
  );

  const returnToSellMode = useCallback<AuthContextValue["returnToSellMode"]>(() => {
    setMode("Sell_Mode");
  }, []);

  const signOut = useCallback<AuthContextValue["signOut"]>(() => {
    setDeviceToken(null);
    persistToken(null);
    setHasDeviceToken(false);
    setCurrentUser(null);
    setKnownUsers([]);
    setMode("Sell_Mode");
  }, []);

  const value = useMemo<AuthContextValue>(
    () => ({
      hasDeviceToken,
      currentUser,
      role: currentUser?.role ?? null,
      mode,
      busy,
      restoring,
      knownUsers,
      requestOtp,
      verifyOtp,
      setPin,
      verifyPin,
      registerWebAuthn,
      authenticateWebAuthn,
      loadUsers,
      switchUser,
      elevateToManage,
      returnToSellMode,
      signOut,
    }),
    [
      hasDeviceToken,
      currentUser,
      mode,
      busy,
      restoring,
      knownUsers,
      requestOtp,
      verifyOtp,
      setPin,
      verifyPin,
      registerWebAuthn,
      authenticateWebAuthn,
      loadUsers,
      switchUser,
      elevateToManage,
      returnToSellMode,
      signOut,
    ]
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}
