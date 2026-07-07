/**
 * App composition root — assembles the App_Shell: Ionic setup, the auth gate
 * (OTP sign-in when there is no active user), and the tabbed router wiring the
 * eight surfaces (Req 1.9) plus the image-ingestion surface.
 *
 * Routing note: the SPA is served under /app (Vite base "/app/"), so routes use
 * absolute "/app/..." paths that match the hrefs in TabBar (Req 1.10, 6.1). Tab
 * visibility is filtered by role/mode inside TabBar via AuthContext.
 */

import { Redirect, Route } from "react-router-dom";
import {
  IonApp,
  IonContent,
  IonHeader,
  IonPage,
  IonRouterOutlet,
  IonSpinner,
  IonTabs,
  IonTitle,
  IonToolbar,
  setupIonicReact,
} from "@ionic/react";
import { IonReactRouter } from "@ionic/react-router";

/* Ionic core styling (required for components to render correctly). */
import "@ionic/react/css/core.css";
import "@ionic/react/css/normalize.css";
import "@ionic/react/css/structure.css";
import "@ionic/react/css/typography.css";
import "@ionic/react/css/padding.css";
import "@ionic/react/css/flex-utils.css";
import "@ionic/react/css/text-alignment.css";

import { AuthProvider, useAuth } from "./auth/AuthContext";
import { OtpFlow } from "./auth/OtpFlow";
import { TabBar } from "./tabs/TabBar";
import Sell from "./tabs/Sell";
import Orders from "./tabs/Orders";
import Inventory from "./tabs/Inventory";
import Recipes from "./tabs/Recipes";
import Customers from "./tabs/Customers";
import Invoices from "./tabs/Invoices";
import Expenses from "./tabs/Expenses";
import Insights from "./tabs/Insights";
import { Ingestion } from "./tabs/Ingestion";

setupIonicReact();

/** Sign-in screen shown until an active user is established (Req 2.1). */
function LoginScreen(): JSX.Element {
  return (
    <IonPage>
      <IonHeader>
        <IonToolbar>
          <IonTitle>KitchenOS</IonTitle>
        </IonToolbar>
      </IonHeader>
      <IonContent className="ion-padding">
        <OtpFlow />
      </IonContent>
    </IonPage>
  );
}

/** The authenticated app: tab bar + routed surfaces. */
function AppShell(): JSX.Element {
  return (
    <IonReactRouter>
      <IonTabs>
        <IonRouterOutlet>
          <Route exact path="/app/sell" component={Sell} />
          <Route exact path="/app/orders" component={Orders} />
          <Route exact path="/app/inventory" component={Inventory} />
          <Route exact path="/app/recipes" component={Recipes} />
          <Route exact path="/app/customers" component={Customers} />
          <Route exact path="/app/invoices" component={Invoices} />
          <Route exact path="/app/expenses" component={Expenses} />
          <Route exact path="/app/insights" component={Insights} />
          <Route exact path="/app/scan" component={Ingestion} />
          <Route exact path="/app">
            <Redirect to="/app/sell" />
          </Route>
          <Route exact path="/">
            <Redirect to="/app/sell" />
          </Route>
        </IonRouterOutlet>
        <TabBar />
      </IonTabs>
    </IonReactRouter>
  );
}

/** Full-screen loader shown while restoring a session from a persisted token. */
function RestoringScreen(): JSX.Element {
  return (
    <IonPage>
      <IonContent className="ion-padding">
        <div style={{ display: "grid", placeItems: "center", height: "100%" }}>
          <IonSpinner name="crescent" />
        </div>
      </IonContent>
    </IonPage>
  );
}

/** Gate the app on an active user; otherwise show sign-in. */
function Gate(): JSX.Element {
  const { currentUser, restoring } = useAuth();
  // While a persisted token is being validated, hold the shell so a valid
  // device never flashes the OTP screen on reload (Req 2.10).
  const screen = restoring ? <RestoringScreen /> : currentUser ? <AppShell /> : <LoginScreen />;
  return <IonApp>{screen}</IonApp>;
}

export default function App(): JSX.Element {
  return (
    <AuthProvider>
      <Gate />
    </AuthProvider>
  );
}
