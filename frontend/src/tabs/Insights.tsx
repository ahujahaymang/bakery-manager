/**
 * Insights tab — the Ask/Insights chat surface (design §8 "Insights").
 *
 * A real chat interface backed by the shared LLM agent via
 * `POST /api/v1/insights/chat` (`insights.chat` → {@link InsightsChatResponse}).
 * The agent has the full tool set and DB access — the same agent the Telegram
 * bot uses — so the User can ask anything about their business in plain
 * language and the model infers what it needs (no date/order pickers).
 *
 * Behaviour:
 *  - A scrollable list of user + assistant message bubbles.
 *  - A text input + Send button pinned at the bottom. The only required field
 *    is the message.
 *  - On send: append the user's message, call `insights.chat({ message,
 *    history })` with the running conversation, then append the assistant's
 *    answer. A "thinking…" spinner shows while the request is in flight.
 *  - Errors (e.g. agent unavailable) are surfaced inline as an assistant-side
 *    notice without losing the typed conversation.
 *
 * Tenant scope is enforced entirely on the Backend from the device session
 * (Req 16.1). Available to all roles — full tools for Owner and Staff alike.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import {
  IonButton,
  IonContent,
  IonFooter,
  IonHeader,
  IonIcon,
  IonPage,
  IonSpinner,
  IonText,
  IonTextarea,
  IonTitle,
  IonToolbar,
} from "@ionic/react";
import { TabTour } from "../components/TabTour";
import { send as sendIcon } from "ionicons/icons";
import {
  ApiError,
  insights as insightsApi,
  type ChatMessage,
} from "../api/endpoints";

/** A message rendered in the chat, including transient error notices. */
interface ChatBubble {
  role: "user" | "assistant";
  content: string;
  /** True when this bubble is an inline error notice (styled differently). */
  isError?: boolean;
}

export function Insights(): JSX.Element {
  const [messages, setMessages] = useState<ChatBubble[]>([]);
  const [draft, setDraft] = useState<string>("");
  const [thinking, setThinking] = useState<boolean>(false);

  const contentRef = useRef<HTMLIonContentElement | null>(null);

  // Keep the latest message in view as the conversation grows.
  useEffect(() => {
    contentRef.current?.scrollToBottom(300);
  }, [messages, thinking]);

  const send = useCallback(async () => {
    const text = draft.trim();
    if (!text || thinking) return;

    // History for the agent = the real conversation so far (user + assistant),
    // excluding any inline error notices we injected for display only.
    const history: ChatMessage[] = messages
      .filter((m) => !m.isError)
      .map((m) => ({ role: m.role, content: m.content }));

    // Optimistically show the user's message and clear the input.
    setMessages((prev) => [...prev, { role: "user", content: text }]);
    setDraft("");
    setThinking(true);

    try {
      const result = await insightsApi.chat({ message: text, history });
      setMessages((prev) => [
        ...prev,
        { role: "assistant", content: result.answer },
      ]);
    } catch (err) {
      // Surface the failure inline without losing the conversation the user
      // has built up. The typed message is already sitting in the list.
      setMessages((prev) => [
        ...prev,
        { role: "assistant", content: errorMessage(err), isError: true },
      ]);
    } finally {
      setThinking(false);
    }
  }, [draft, thinking, messages]);

  const onKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLIonTextareaElement>) => {
      // Enter sends; Shift+Enter inserts a newline.
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        void send();
      }
    },
    [send]
  );

  return (
    <IonPage>
      <TabTour
        tabKey="insights"
        title="Ask"
        intro="Ask anything about your business."
        points={[
          "Type a question in plain language — revenue, dues, top products, trends.",
          "The assistant reads your live data to answer.",
          "Great for quick checks without digging through screens.",
        ]}
      />
      <IonHeader>
        <IonToolbar>
          <IonTitle>Ask/Insights</IonTitle>
        </IonToolbar>
      </IonHeader>

      <IonContent ref={contentRef} className="ion-padding">
        {messages.length === 0 && !thinking ? (
          <div style={{ textAlign: "center", marginTop: 48, padding: "0 16px" }}>
            <IonText color="medium">
              <p>Ask anything about your business.</p>
              <p style={{ fontSize: 14 }}>
                e.g. “What were my sales this week?”, “Which orders are due
                tomorrow?”, “How much flour do I have left?”
              </p>
            </IonText>
          </div>
        ) : null}

        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          {messages.map((m, i) => (
            <MessageBubble key={i} bubble={m} />
          ))}

          {thinking ? (
            <div style={bubbleWrapStyle("assistant")}>
              <div style={bubbleStyle("assistant")}>
                <IonSpinner name="dots" /> <span>thinking…</span>
              </div>
            </div>
          ) : null}
        </div>
      </IonContent>

      <IonFooter>
        <IonToolbar>
          <div
            style={{
              display: "flex",
              alignItems: "flex-end",
              gap: 8,
              padding: "4px 8px",
            }}
          >
            <IonTextarea
              aria-label="Your message"
              placeholder="Ask a question…"
              autoGrow
              rows={1}
              value={draft}
              disabled={thinking}
              onIonInput={(e) => setDraft(e.detail.value ?? "")}
              onKeyDown={onKeyDown}
              style={{ flex: 1 }}
            />
            <IonButton
              aria-label="Send"
              onClick={() => void send()}
              disabled={thinking || !draft.trim()}
            >
              <IonIcon slot="icon-only" icon={sendIcon} />
            </IonButton>
          </div>
        </IonToolbar>
      </IonFooter>
    </IonPage>
  );
}

interface MessageBubbleProps {
  bubble: ChatBubble;
}

/** Render a single chat bubble aligned by role. */
function MessageBubble({ bubble }: MessageBubbleProps): JSX.Element {
  return (
    <div style={bubbleWrapStyle(bubble.role)}>
      <div style={bubbleStyle(bubble.role, bubble.isError)}>
        <IonText>
          <p style={{ margin: 0, whiteSpace: "pre-wrap" }}>{bubble.content}</p>
        </IonText>
      </div>
    </div>
  );
}

/** Alignment wrapper: user bubbles right, assistant bubbles left. */
function bubbleWrapStyle(role: "user" | "assistant"): React.CSSProperties {
  return {
    display: "flex",
    justifyContent: role === "user" ? "flex-end" : "flex-start",
  };
}

/** Bubble appearance by role, with an error variant for failure notices. */
function bubbleStyle(
  role: "user" | "assistant",
  isError?: boolean
): React.CSSProperties {
  const base: React.CSSProperties = {
    maxWidth: "80%",
    padding: "8px 12px",
    borderRadius: 16,
    fontSize: 15,
    lineHeight: 1.35,
  };
  if (isError) {
    return {
      ...base,
      background: "var(--ion-color-danger, #eb445a)",
      color: "var(--ion-color-danger-contrast, #fff)",
      borderBottomLeftRadius: 4,
    };
  }
  if (role === "user") {
    return {
      ...base,
      background: "var(--ion-color-primary, #3880ff)",
      color: "var(--ion-color-primary-contrast, #fff)",
      borderBottomRightRadius: 4,
    };
  }
  return {
    ...base,
    background: "var(--ion-color-light, #f4f5f8)",
    color: "var(--ion-color-light-contrast, #000)",
    borderBottomLeftRadius: 4,
  };
}

/** Extract a user-facing message from an unknown thrown value. */
function errorMessage(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.status === 502 || err.code === "agent_unavailable") {
      return "The assistant is unavailable right now. Please try again in a moment.";
    }
    return err.message;
  }
  if (err instanceof Error && err.message) return err.message;
  return "Something went wrong. Please try again.";
}

export default Insights;
