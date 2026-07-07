# Requirements Document

## Introduction

KitchenOS is an existing multi-tenant SaaS for home bakers and cloud kitchens in India. Today every operation (orders, inventory, recipes, invoices, expenses) is performed through a conversational Telegram/WhatsApp bot that routes intent through an LLM agent. A zero-LLM "Register/Booth" web app already exists at `/register/{tenant_id}` for point-of-sale, backed by a UI-agnostic service layer.

This feature is a strategic product pivot to an **app-first** experience, guided by the principle: **"UI for deterministic actions, AI for reasoning."** Deterministic CRUD operations (create order, add inventory, add recipe, generate invoice, sell/checkout) move out of chat into a fast, installable Progressive Web App (PWA). Conversational AI is reserved for two jobs: (1) analysis and insights questions, and (2) image ingestion where extracted data is proposed to the user for confirmation/editing in a UI form. Telegram/WhatsApp is retained but demoted to a notification and asynchronous-entry channel.

The pivot expands the existing Register/Booth web app into a full tabbed application covering Sell, Orders, Inventory, Recipes, Customers, Invoices, Expenses, and an Ask/Insights tab. It adds authentication and multi-user roles. It preserves all existing business logic in the service layer and the existing multi-tenant data isolation. This is primarily a new front-end surface plus authentication and role management, not a rewrite of business logic.

**Explicitly out of scope for this feature:** native app-store wrapping (Android TWA / iOS wrapper), barcode scanning, and a desktop application. These are planned for a later phase.

## Glossary

- **KitchenOS**: The overall multi-tenant SaaS platform for home bakers and cloud kitchens.
- **App**: The Progressive Web App that becomes the primary user surface of KitchenOS.
- **App_Shell**: The installable PWA container that provides the tabbed navigation, service worker, and offline behavior.
- **Backend**: The existing FastAPI server that serves the App and exposes the service-layer operations over HTTP.
- **Service_Layer**: The existing UI-agnostic business logic modules (order_service, inventory_service, recipe_service, payment_service, product_service, customer_service, invoice_service, reporting_service, and related modules).
- **Auth_Service**: The component that authenticates users, issues device session tokens, and verifies PINs and biometric credentials.
- **OTP**: A one-time passcode used to verify a phone number when a device is first trusted.
- **Device_Session_Token**: A long-lived credential (valid 30 to 90 days) stored on a trusted device after successful OTP verification.
- **PIN**: A per-user numeric passcode used to unlock the App and to switch between users on a shared device.
- **WebAuthn_Credential**: A biometric or platform authenticator credential registered per user via the WebAuthn standard, used as an alternative to a PIN.
- **Owner**: A user role with full access, including financials, cost data, and delete operations.
- **Staff**: A user role limited to selling and a restricted set of screens, with no access to financials, cost data, or delete operations. Also referred to as Cashier.
- **User**: An Owner or Staff member who belongs to a single Tenant.
- **Tenant**: An independent business account whose data is isolated from all other businesses.
- **Sell_Mode**: A locked-down operating state of the App intended for Staff, exposing selling and limited screens.
- **Manage_Mode**: An operating state of the App intended for the Owner, exposing all screens including financials.
- **Sell_Surface**: The point-of-sale screen (the existing Register/Booth sell screen) for tapping products and checking out.
- **Orders_Surface**: The App screen for creating, viewing, and updating made-to-order lifecycle orders.
- **Inventory_Surface**: The App screen for managing ingredients and packaging materials.
- **Recipes_Surface**: The App screen for managing recipes and viewing cost-per-unit.
- **Customers_Surface**: The App screen for managing customers.
- **Invoices_Surface**: The App screen for generating and viewing invoices.
- **Expenses_Surface**: The App screen for recording business expenses.
- **Insights_Surface**: The Ask/Insights App screen where conversational analysis lives.
- **Image_Ingestion**: The workflow where a photographed document (receipt, handwritten recipe, order screenshot, product catalog, payment receipt) is processed by the model to extract structured data.
- **Ingestion_Draft**: The extracted, editable structured data proposed to the User in a UI form before saving.
- **Notification_Channel**: The Telegram or WhatsApp integration used to send alerts and receive optional asynchronous text entry.
- **Sales_Attribution**: The association between a completed sale and the Staff or Owner who recorded it.

## Requirements

### Requirement 1: Progressive Web App Shell and Installability

**User Story:** As a home baker, I want KitchenOS to install to my home screen and open like an app, so that I can run my business without depending on chat.

#### Acceptance Criteria

1. THE App_Shell SHALL serve a web app manifest that declares the App name, at least one icon of 192x192 pixels and one icon of 512x512 pixels, a start URL, and the standalone display mode.
2. WHEN a User loads the App for the first time, THE App_Shell SHALL register a service worker and cache the App's static assets (HTML, CSS, JavaScript, and icon files) within 10 seconds of the load completing.
3. IF service worker registration or static asset caching fails, THEN THE App_Shell SHALL continue serving the App over the network and SHALL present an indication that offline capability is unavailable, without blocking access to App functionality.
4. WHILE the device has no network connectivity, THE App_Shell SHALL serve the previously cached static assets so that the App shell renders.
5. WHEN a User opens the App on a browser that supports home-screen installation and the manifest and registered service worker are both present, THE App_Shell SHALL present a control to install the App to the home screen.
6. WHEN the App is launched from the home screen, THE App_Shell SHALL open in standalone display mode without browser navigation chrome.
7. THE App_Shell SHALL be served by the Backend over HTTPS.
8. IF a User requests the App over HTTP, THEN THE App_Shell SHALL redirect the request to the HTTPS equivalent of the same URL.
9. THE App_Shell SHALL present tabbed navigation containing exactly the following tabs: Sell, Orders, Inventory, Recipes, Customers, Invoices, Expenses, and Ask/Insights.
10. WHERE the signed-in User holds the Staff role, THE App_Shell SHALL hide each navigation tab whose associated feature the Staff role is not permitted to access, and SHALL display only the permitted tabs.

### Requirement 2: Phone Number and OTP Device Verification

**User Story:** As a business owner, I want to verify my phone number once per device, so that my business data is protected without me re-authenticating on every open.

#### Acceptance Criteria

1. WHEN a User initiates sign-in on a device that has no valid Device_Session_Token, THE Auth_Service SHALL request the User's phone number.
2. WHEN a User submits a phone number that passes format validation, THE Auth_Service SHALL generate a one-time password of 4 to 8 digits and deliver it to that phone number within 30 seconds.
3. IF a User submits a phone number that fails format validation, THEN THE Auth_Service SHALL reject the submission, report an invalid-phone-number error, and SHALL NOT generate a one-time password.
4. IF delivery of a one-time password to the submitted phone number fails, THEN THE Auth_Service SHALL report a delivery-failure error and SHALL allow the User to request the one-time password again.
5. WHEN a User submits a one-time password that matches the generated one-time password before its validity period of 5 minutes has elapsed, THE Auth_Service SHALL issue a Device_Session_Token to the device.
6. THE Auth_Service SHALL set the Device_Session_Token validity to a configured, fixed duration between 30 and 90 days inclusive.
7. IF a User submits a one-time password that does not match the generated one-time password, THEN THE Auth_Service SHALL reject the verification and report a verification failure.
8. IF a User submits an incorrect one-time password 5 times for the same generated one-time password, THEN THE Auth_Service SHALL invalidate that one-time password and require the User to request a new one-time password.
9. IF a User submits a one-time password after its validity period of 5 minutes has elapsed, THEN THE Auth_Service SHALL reject the verification and report that the one-time password has expired.
10. WHILE a device holds a valid Device_Session_Token, THE Auth_Service SHALL NOT require one-time password verification to reopen the App on that device.
11. WHEN a Device_Session_Token reaches the end of its validity period, THE Auth_Service SHALL require one-time password verification before granting further access on that device.

### Requirement 3: Tiered OTP Delivery

**User Story:** As the platform operator, I want OTP delivery to prefer low-cost channels for known users, so that verification costs stay low while still reaching new users.

#### Acceptance Criteria

1. WHEN an OTP must be delivered to a phone number already associated with a Notification_Channel, THE Auth_Service SHALL send the OTP over the Notification_Channel within 5 seconds of receiving the delivery request.
2. WHERE a phone number has no associated Notification_Channel, THE Auth_Service SHALL send the OTP by SMS within 5 seconds of receiving the delivery request.
3. IF the Auth_Service does not receive a delivery confirmation for the Notification_Channel within 30 seconds of sending, THEN THE Auth_Service SHALL record the Notification_Channel delivery attempt as failed and SHALL send the OTP by SMS.
4. IF the Auth_Service does not receive a delivery confirmation for the SMS delivery within 30 seconds of sending, THEN THE Auth_Service SHALL return a delivery-failure indication to the caller and SHALL NOT record the OTP as delivered.

### Requirement 4: Per-User PIN and Biometric Unlock

**User Story:** As a shop owner sharing a tablet with staff, I want each person to unlock the app with a PIN or fingerprint, so that we can switch users quickly on one device.

#### Acceptance Criteria

1. WHEN a User first gains access on a trusted device, THE Auth_Service SHALL require the User to set a PIN of 4 to 8 numeric digits.
2. IF a User sets a PIN that is not 4 to 8 numeric digits, THEN THE Auth_Service SHALL reject the PIN, SHALL NOT store the PIN, and SHALL report an invalid-PIN error.
3. WHEN a User enters a PIN that matches the stored PIN for that User and that User is not currently locked out, THE Auth_Service SHALL grant access as that User.
4. IF a User enters a PIN that does not match the stored PIN for that User, THEN THE Auth_Service SHALL deny access, SHALL leave the stored PIN unchanged, and SHALL report an incorrect-PIN error.
5. IF a User enters an incorrect PIN 5 consecutive times, THEN THE Auth_Service SHALL lock out PIN entry for that User for 300 seconds.
6. WHERE a device supports WebAuthn, THE Auth_Service SHALL allow a User to register a WebAuthn_Credential as an alternative to the PIN.
7. WHEN a User authenticates with a registered WebAuthn_Credential, THE Auth_Service SHALL grant access as that User.
8. IF authentication with a WebAuthn_Credential fails, THEN THE Auth_Service SHALL deny access and report a WebAuthn authentication failure.
9. WHILE more than one User is registered on a trusted device, THE App SHALL provide a control to switch the active User by PIN or WebAuthn_Credential without OTP verification, and SHALL complete the switch within 2 seconds of successful authentication.

### Requirement 5: User Roles and Permissions

**User Story:** As an owner, I want distinct owner and staff roles, so that staff can sell without seeing my financials or deleting records.

#### Acceptance Criteria

1. THE Auth_Service SHALL assign each User exactly one role, either Owner or Staff, at the time the User is created.
2. WHERE the signed-in User holds the Owner role, THE App SHALL grant access to all surfaces including financial data, cost data, and delete operations.
3. WHERE the signed-in User holds the Staff role, THE App SHALL grant access to the Sell_Surface and the surfaces designated for Staff.
4. WHERE the signed-in User holds the Staff role, WHEN the Backend receives a request to read financial data, read cost data, or perform a delete operation, THE Backend SHALL reject the request before executing it, SHALL NOT return any financial or cost data, SHALL leave all records unchanged, and SHALL return an authorization error indicating insufficient permissions.
5. WHEN an Owner creates a Staff User, THE Auth_Service SHALL associate the new Staff User with the Owner's Tenant.
6. THE Backend SHALL enforce role permissions on every request independently of navigation controls hidden in the App.
7. WHERE the signed-in User holds the Staff role, WHEN the Backend receives a request to create, modify, or delete any User, THE Backend SHALL reject the request before executing it, SHALL leave all User records unchanged, and SHALL return an authorization error indicating insufficient permissions.
8. IF the Backend receives a request from a requester that is not authenticated or whose resolved role is neither Owner nor Staff, THEN THE Backend SHALL reject the request before executing it, SHALL NOT return protected data, and SHALL return an authorization error.

### Requirement 6: Sell Mode and Manage Mode Separation

**User Story:** As an owner, I want a locked-down sell mode for the counter and a full manage mode for myself, so that the shared device stays safe during service.

#### Acceptance Criteria

1. THE App SHALL provide a Sell_Mode that exposes the Sell_Surface and the surfaces designated for Staff, and SHALL hide all other surfaces.
2. THE App SHALL provide a Manage_Mode that exposes all surfaces to the Owner.
3. WHEN a User requests entry to Manage_Mode while the App is in Sell_Mode, THE App SHALL require successful PIN or WebAuthn_Credential verification of a User holding the Owner role before entering Manage_Mode.
4. WHEN a Staff User signs in, THE App SHALL open in Sell_Mode.
5. WHEN an Owner signs in, THE App SHALL open in Manage_Mode.
6. IF verification of an Owner to enter Manage_Mode fails, THEN THE App SHALL remain in Sell_Mode and report a verification failure.
7. IF verification to enter Manage_Mode fails 5 consecutive times, THEN THE App SHALL lock out Manage_Mode entry for 30 seconds.

### Requirement 7: Sales Attribution

**User Story:** As an owner, I want each sale attributed to the staff member who made it, so that I can track individual performance.

#### Acceptance Criteria

1. WHEN a User completes a sale on the Sell_Surface, THE Service_Layer SHALL record the authenticated User's identity as the Sales_Attribution within that sale record before the sale record is persisted.
2. WHEN the Owner views a sale record, THE App SHALL display the identity of the User recorded as the Sales_Attribution for that sale.
3. THE Service_Layer SHALL retain the Sales_Attribution of a sale record unchanged for the life of that record.
4. WHEN a sale recorded while the device was offline is submitted to the Backend, THE Service_Layer SHALL record the identity of the User who completed that sale as its Sales_Attribution.
5. IF a sale is completed while no authenticated User identity is available, THEN THE Service_Layer SHALL reject the sale, persist no sale record, and report that a sale cannot be recorded without an attributed User.

### Requirement 8: Sell / Point-of-Sale Surface

**User Story:** As a cashier, I want a fast tap-to-sell screen, so that I can check customers out in seconds.

#### Acceptance Criteria

1. WHEN a User opens the Sell_Surface, THE Sell_Surface SHALL display the products marked available for sale, each with its unit price, for the current Tenant.
2. WHEN a User adds a product to the cart, THE Sell_Surface SHALL update the cart total within 1 second.
3. WHEN a User completes checkout, THE Service_Layer SHALL create exactly one order record and exactly one payment record for the sale, each scoped to the current Tenant.
4. WHEN a User selects a payment method of Cash or UPI at checkout, THE Service_Layer SHALL record the selected payment method on the payment record.
5. WHEN a sale is completed, THE Sell_Surface SHALL make available a printable receipt and a downloadable invoice for that sale.
6. THE Sell_Surface SHALL compute cart totals and the payable amount using the existing Register/Booth Service_Layer operations without altering their calculated results.
7. IF checkout fails, THEN THE Service_Layer SHALL create no order record and no payment record for that sale, and THE Sell_Surface SHALL report the checkout failure.
8. IF a User attempts checkout with an empty cart, THEN THE Sell_Surface SHALL reject the checkout and SHALL create no order or payment record.
9. WHEN a User opens the Sell_Surface and no sell session is active, THE Service_Layer SHALL start an always-on sell session for the current Tenant and SHALL synchronize every catalog product variant into it with unlimited stock, so the tap-to-sell grid reflects the current catalog without requiring the User to open a session manually.
10. WHERE the signed-in User holds the Owner role, THE Sell_Surface SHALL allow the User to add a product (name, optional category, size label, price) directly from the Sell_Surface, and THE Service_Layer SHALL make the new product available for sale in the same session immediately.
11. WHERE the signed-in User holds the Owner role, THE Sell_Surface SHALL allow the User to upload or scan a catalog image, propose the extracted products for confirmation/editing, and on confirmation create the confirmed products and make them available for sale.
12. WHERE the signed-in User holds the Staff role, THE Sell_Surface SHALL NOT offer the add-product or upload-catalog actions.

### Requirement 9: Orders Surface

**User Story:** As a baker, I want to create and manage made-to-order orders with delivery dates, so that I can plan my production.

#### Acceptance Criteria

1. WHEN a User submits a new order with a customer, items, quantities, prices, and a delivery date, THE Service_Layer SHALL create the order with an initial status of pending.
2. THE Orders_Surface SHALL display orders with their status of pending, delivered, or cancelled.
3. WHEN a User marks an order that is in the pending status as delivered, THE Service_Layer SHALL update the order status to delivered.
4. WHEN a User cancels an order that is in the pending status, THE Service_Layer SHALL update the order status to cancelled and retain the order record.
5. THE Orders_Surface SHALL allow a User to filter orders by delivery date and by status.
6. WHERE the signed-in User holds the Owner role, THE Orders_Surface SHALL allow deletion of an order created in error.
7. IF a User submits a new order that omits a customer, contains no line items, or omits a delivery date, THEN THE Service_Layer SHALL reject the submission, create no order record, and return a validation error indicating the missing field.
8. IF a User submits an order line item with a quantity outside the range 1 to 999,999 or a unit price outside the range 0.00 to 9,999,999.99, THEN THE Service_Layer SHALL reject the submission, create no order record, and return a validation error.
9. IF a User attempts to mark a cancelled order as delivered or to cancel a delivered order, THEN THE Service_Layer SHALL reject the transition and retain the current order status unchanged.

### Requirement 10: Inventory Surface

**User Story:** As a baker, I want to manage ingredients and packaging, so that I always know my stock and costs.

#### Acceptance Criteria

1. WHEN a User submits a new inventory item with a name of 1 to 100 characters, a category of 1 to 50 characters, a quantity in the range 0.00 to 999,999.99, a unit of 1 to 20 characters, and a cost in the range 0.00 to 999,999.99, THE Service_Layer SHALL create the inventory item and return the persisted item with its assigned identifier.
2. IF a User submits a new inventory item in which any required field (name, category, quantity, unit, or cost) is empty, or in which the quantity or cost falls outside the range 0.00 to 999,999.99, THEN THE Service_Layer SHALL reject the submission, return an error response indicating which field is invalid, and SHALL NOT create any inventory item.
3. WHEN a User submits an updated quantity in the range 0.00 to 999,999.99 or an updated cost in the range 0.00 to 999,999.99 for an existing inventory item, THE Service_Layer SHALL persist the updated value and return the updated item.
4. IF a User submits an updated quantity or cost that falls outside the range 0.00 to 999,999.99, or that references an inventory item that does not exist, THEN THE Service_Layer SHALL reject the update, return an error response indicating the reason, and SHALL retain the previously persisted value unchanged.
5. WHEN the Inventory_Surface loads inventory items, THE Inventory_Surface SHALL display the items grouped by category, with categories ordered alphabetically ascending and items within each category ordered alphabetically ascending by name.
6. WHILE no inventory items exist, THE Inventory_Surface SHALL display an empty-state indication and SHALL NOT display any category group.
7. WHERE the signed-in User holds the Staff role, THE Inventory_Surface SHALL hide the cost value of every inventory item from display.
8. WHEN a User selects the Scan receipt action on the Inventory_Surface, THE Inventory_Surface SHALL allow the User to capture an image with the device camera or upload an image file, and SHALL launch Image_Ingestion with the document type fixed to inventory.
9. WHEN Image_Ingestion returns an Ingestion_Draft for a purchase receipt launched from the Inventory_Surface, THE Inventory_Surface SHALL present the extracted line items in an editable confirm form, and WHEN the User confirms that form, THE Service_Layer SHALL create the corresponding inventory items.

### Requirement 11: Recipes Surface and Cost-per-Unit

**User Story:** As a baker, I want recipes that compute cost per unit from ingredient prices, so that I know my true margins.

#### Acceptance Criteria

1. WHEN a User submits a new recipe with a name of 1 to 100 characters and a yield per batch greater than 0 and at most 999,999, THE Service_Layer SHALL create the recipe.
2. WHEN a User adds an inventory item as a recipe component with a quantity greater than 0 and at most 999,999 in the inventory item's unit, THE Service_Layer SHALL associate the component with the recipe.
3. WHEN every component of a recipe has a defined quantity and a resolvable inventory cost, THE Service_Layer SHALL calculate the recipe's cost per unit as the sum of each component's quantity multiplied by its inventory item's unit cost, divided by the recipe's yield per batch.
4. WHEN an inventory item's cost changes, THE Service_Layer SHALL recalculate the cost per unit for every recipe that uses that inventory item, and THE Recipes_Surface SHALL display the recalculated cost per unit on its next load.
5. IF a User submits a recipe whose name is empty or exceeds 100 characters, or whose yield per batch is not greater than 0 and at most 999,999, THEN THE Service_Layer SHALL reject the submission, create no recipe, and return a validation error indicating the invalid field.
6. IF a User adds a recipe component with a quantity that is not greater than 0 and at most 999,999, THEN THE Service_Layer SHALL reject the component, create no component association, and return a validation error.
7. WHERE the signed-in User holds the Staff role, WHEN the Backend receives a request to read recipe cost-per-unit data, THE Backend SHALL reject the request, SHALL NOT return any cost-per-unit value, and SHALL return an authorization error.
8. WHEN a User selects the Scan recipe action on the Recipes_Surface, THE Recipes_Surface SHALL allow the User to capture an image with the device camera or upload an image file, and SHALL launch Image_Ingestion with the document type fixed to recipe.
9. WHEN Image_Ingestion returns an Ingestion_Draft for a recipe launched from the Recipes_Surface, THE Recipes_Surface SHALL present the extracted recipe and its components in an editable confirm form, and WHEN the User confirms that form, THE Service_Layer SHALL create the recipe and its components.

### Requirement 12: Customers Surface

**User Story:** As a baker, I want to manage my customers, so that I can attach orders and invoices to them.

#### Acceptance Criteria

1. WHEN a User submits a new customer with a name of 1 to 100 characters and a phone number of 8 to 15 digits, THE Service_Layer SHALL create the customer for the current Tenant.
2. WHEN a User searches customers by a name or phone number search term, THE Customers_Surface SHALL display, within 2 seconds, all customers of the current Tenant whose name or phone number contains the search term, up to a maximum of 50 results.
3. IF a User submits a customer whose phone number already exists for the Tenant, THEN THE Service_Layer SHALL reject the duplicate, retain the existing customer record unchanged, and report a conflict identifying the duplicate phone number.
4. IF a User submits a customer whose name is empty or exceeds 100 characters, or whose phone number is not 8 to 15 digits, THEN THE Service_Layer SHALL reject the submission, persist no new record, and report a validation error indicating the invalid field.
5. WHEN a User's customer search matches no customers of the current Tenant, THE Customers_Surface SHALL display an empty-result indication.

### Requirement 13: Invoices Surface

**User Story:** As a baker, I want to generate GST invoices for orders, so that I can bill customers professionally.

#### Acceptance Criteria

1. WHEN a User requests an invoice for an existing order, THE Service_Layer SHALL generate a PDF invoice containing the business name, customer details, an itemised list of ordered items with quantities and unit prices, a subtotal, applicable taxes, and the total amount due, within 10 seconds of the request.
2. WHERE the Tenant has configured a tax rate and label, THE Service_Layer SHALL include a GST breakdown on the invoice showing the configured tax label, the tax rate as a percentage, and the calculated tax amount.
3. THE Invoices_Surface SHALL display every monetary value on the invoice in the currency configured for the Tenant.
4. WHEN a User requests an invoice for an order, THE Service_Layer SHALL generate the invoice using the order's delivery date regardless of whether that date is in the past, is the current date, or is in the future, so that advance and made-to-order orders can be invoiced at order time.
5. IF a User requests an invoice for an order that does not exist, THEN THE Service_Layer SHALL reject the request without generating an invoice and return an error indication that the order was not found.
6. WHEN the Invoices_Surface lists orders for the User to invoice, THE Invoices_Surface SHALL identify each order by its customer name (rather than by an internal identifier).
7. WHERE the Tenant has not configured a tax rate, THE Service_Layer SHALL generate the invoice without a GST breakdown.

### Requirement 14: Expenses Surface

**User Story:** As a baker, I want to record business expenses, so that I can track my spending and capital assets.

#### Acceptance Criteria

1. WHEN a User submits an expense with an amount between 0.01 and 999,999,999.99, a category selected from the predefined expense category list, and a description of at most 500 characters, THE Service_Layer SHALL create the expense record and SHALL return confirmation identifying the created record.
2. IF a User submits an expense with a missing amount, an amount outside the range 0.01 to 999,999,999.99, a missing or unrecognized category, or a description exceeding 500 characters, THEN THE Service_Layer SHALL reject the submission, SHALL NOT create an expense record, and SHALL return an error indicating the invalid field.
3. WHERE a User marks an expense as a capital asset during submission, THE Service_Layer SHALL store the expense record with its capital asset flag set to true.
4. WHEN a User applies a category filter together with a start date and an end date where the start date is on or before the end date, THE Expenses_Surface SHALL display only the expense records whose category matches the selected category and whose date falls within the inclusive start-date-to-end-date range.
5. IF a User applies a date range whose start date is later than its end date, THEN THE Expenses_Surface SHALL reject the filter, SHALL NOT alter the displayed records, and SHALL return an error indicating the invalid date range.
6. WHERE the signed-in User holds the Staff role, THE App_Shell SHALL hide the Expenses_Surface.

### Requirement 15: AI Image Ingestion with Confirm-and-Edit

**User Story:** As a baker, I want to photograph a receipt, recipe, or order and confirm the extracted data in a form, so that data entry is fast and I stay in control.

#### Acceptance Criteria

1. WHEN a User submits an image for Image_Ingestion, THE Backend SHALL extract structured data from the image and return an Ingestion_Draft within 30 seconds.
2. WHEN an Ingestion_Draft is returned, THE App SHALL present the Ingestion_Draft in an editable UI form.
3. THE App SHALL allow a User to edit any field of the Ingestion_Draft before saving.
4. WHEN a User confirms an Ingestion_Draft, THE Service_Layer SHALL persist the confirmed data through the corresponding domain operation.
5. IF a User discards an Ingestion_Draft, THEN THE Backend SHALL persist no data from that draft.
6. THE App SHALL support Image_Ingestion for purchase receipts into inventory, handwritten recipes into recipes, order screenshots into orders, product catalogs into products, and payment receipts into payments.
7. WHILE an Ingestion_Draft is awaiting confirmation, THE App SHALL present the extracted data in a form rather than a chat reply.
8. IF a User submits an image that exceeds 10 megabytes or is not in a supported image format, THEN THE Backend SHALL reject the submission, return no Ingestion_Draft, and report a validation error.
9. IF the Backend cannot extract any structured data from a submitted image, THEN THE Backend SHALL return no Ingestion_Draft and report an extraction failure.
10. IF persisting a confirmed Ingestion_Draft through its domain operation fails, THEN THE Service_Layer SHALL persist no data from that draft, and THE App SHALL report the failure.
11. WHERE Image_Ingestion is launched in-context from a domain surface, THE App SHALL supply the document type from that launch context, and THE Backend SHALL use the supplied document type without performing document-type classification.
12. WHEN a User provides an image for Image_Ingestion, THE App SHALL accept the image either captured with the device camera or uploaded as an image file.
13. WHEN a purchase receipt is submitted for Image_Ingestion with the document type fixed to inventory, THE Service_Layer SHALL create inventory items from the confirmed Ingestion_Draft; and WHEN a receipt is submitted for Image_Ingestion with the document type fixed to receipt, THE Service_Layer SHALL create an expense from the confirmed Ingestion_Draft.

### Requirement 16: Ask / Insights Conversational Analysis

**User Story:** As a baker, I want to chat with the app about my business in plain language, so that the model reasons over my data and answers without me doing the math myself.

#### Acceptance Criteria

1. WHEN a User submits a natural-language question on the Insights_Surface, THE Backend SHALL answer the question using a conversational agent with tool access over solely the signed-in Tenant's data, and SHALL return a response.
2. THE Insights_Surface SHALL NOT require the User to supply a start date or an end date; WHERE a reporting period is not expressed in the question, THE agent SHALL infer an appropriate range from the question.
3. WHEN a User asks a revenue, cost, or profit question that expresses a reporting period bounded by a start date and an end date where the start date is on or before the end date, THE Insights_Surface SHALL aggregate the revenue, cost, and profit values from the Tenant's Orders and costs over that period, inclusive of both boundary dates.
4. WHEN a User asks for the cost of an Order, THE Insights_Surface SHALL compute the Order cost as the sum of each ingredient's quantity multiplied by its recorded unit price, and SHALL report any ingredients that have no recorded unit price.
5. IF the agent cannot answer a question from the Tenant's data, THEN THE Insights_Surface SHALL return a message indicating that the question could not be answered and SHALL NOT return partial or placeholder values.
6. IF the underlying language model is unavailable or fails while answering, THEN THE Backend SHALL return a service-unavailable error and SHALL NOT return a fabricated answer.
7. THE Backend SHALL scope every tool invocation and data access performed while answering an Insights question to the signed-in Tenant.

> **Note (future consideration):** The Staff financial-data restriction is currently NOT enforced on the conversational chat path — a Staff user may currently reach financial answers through the chat. Restricting financial tool access for Staff on the chat endpoint is flagged as a future consideration and is not yet a satisfied acceptance criterion. The deterministic `/ask` path retains its Staff financial block.

### Requirement 17: Telegram and WhatsApp Demotion to Notifications

**User Story:** As a baker, I want alerts and summaries pushed to Telegram or WhatsApp, so that I stay informed without the chat being my main workspace.

#### Acceptance Criteria

1. WHEN a new order is detected from a connected Notification_Channel, THE Notification_Channel SHALL send an order alert containing the order identifier, customer name, ordered items, and order total to the Owner within 60 seconds of detection.
2. WHEN a Tenant's subscription enters the expiry warning window, defined as the 7 calendar days immediately preceding the subscription expiry date, THE Notification_Channel SHALL send a subscription expiry warning containing the expiry date and the number of days remaining to the Owner once per calendar day until the subscription expires or is renewed.
3. WHEN an Instagram direct message order is detected, THE Notification_Channel SHALL send an order detection alert containing the customer handle and the detected order details to the Owner within 60 seconds of detection.
4. WHEN each 24-hour reporting period completes, THE Notification_Channel SHALL send a daily summary to the Owner once per calendar day, containing the total number of sales, total sales amount, and count of pending orders for the preceding 24-hour period.
5. WHERE a User submits a text command over the Notification_Channel, THE Backend SHALL process the command through the existing agent path and SHALL return a response over the same Notification_Channel within 30 seconds.
6. THE App SHALL be the primary surface for creating orders, inventory items, recipes, invoices, and sales.
7. IF delivery of a notification over the Notification_Channel fails, THEN THE Notification_Channel SHALL retry delivery up to 3 times, and SHALL record a delivery failure indication if all 3 retries are exhausted.
8. IF a text command submitted over the Notification_Channel cannot be processed by the agent path, THEN THE Backend SHALL return an error message indicating that the command was not understood over the same Notification_Channel.

### Requirement 18: Offline Tolerance

**User Story:** As a baker selling at an event with poor connectivity, I want the app to keep working, so that I do not lose sales when the network drops.

#### Acceptance Criteria

1. WHILE the device has no network connectivity, THE App_Shell SHALL load the App from the static assets cached by its service worker without issuing a network request.
2. WHILE the device has no network connectivity, THE Sell_Surface SHALL allow a User to add products to the cart from product data cached on the device.
3. WHILE the device has no network connectivity, WHEN a User completes checkout, THE Sell_Surface SHALL record the completed sale in local device storage.
4. WHEN network connectivity is restored, THE App SHALL submit each sale recorded while offline to the Backend within 30 seconds.
5. THE App SHALL submit each sale recorded while offline to the Backend at most once, so that a restored connection does not create duplicate sale records.
6. IF a sale recorded offline fails to submit after 3 retry attempts following connectivity restoration, THEN THE App SHALL report the failed sale to the User and SHALL retain that sale in local device storage.

### Requirement 19: Multi-Tenant Data Isolation

**User Story:** As a business owner, I want my data isolated from every other business, so that no other tenant can access my records.

#### Acceptance Criteria

1. WHEN the Backend creates or updates a data record, THE Backend SHALL associate that record with exactly one Tenant.
2. WHEN the Backend serves a request from an authenticated User, THE Backend SHALL restrict every read, return, modification, and deletion of data to the Tenant of that authenticated User.
3. IF a request attempts to access data belonging to another Tenant, THEN THE Backend SHALL deny the request, SHALL leave the targeted record unchanged, SHALL NOT return any field values of that record, and SHALL return an authorization error.
4. IF the Backend receives a request that is not authenticated or that has no valid Device_Session_Token, THEN THE Backend SHALL deny the request and return an authorization error.
5. IF a data-modifying request cannot be attributed to a Tenant, THEN THE Backend SHALL reject the request and persist no data.
6. THE Backend SHALL apply Tenant scoping to every Service_Layer domain operation for order, inventory, recipe, payment, product, customer, invoice, and reporting.

### Requirement 20: Front-End Framework and Service-Layer Reuse

**User Story:** As the product team, I want the app built on a component framework reusing the existing backend, so that we get good UX without rewriting business logic.

#### Acceptance Criteria

1. THE App SHALL be implemented with a component-based front-end framework and a mobile UI kit, and SHALL render every user-facing screen through reusable components provided by that framework and UI kit.
2. WHEN the App initiates a domain operation belonging to the order, inventory, recipe, payment, product, customer, invoice, or reporting categories, THE App SHALL communicate with the Backend over HTTP.
3. IF an HTTP request from the App to the Backend does not receive a successful response within 30 seconds, THEN THE App SHALL terminate the request, display an error indication to the user describing the communication failure, and retain any unsaved user input entered before the request.
4. THE Backend SHALL reuse the existing Service_Layer modules for order, inventory, recipe, payment, product, customer, invoice, and reporting operations.
5. WHEN a domain operation is invoked through the App, THE Backend SHALL return results identical to those the corresponding existing Service_Layer module produces for equivalent inputs, without altering that module's logic.

### Requirement 21: Owner Onboarding and App Access

**User Story:** As a new owner, I want to onboard through the Telegram/WhatsApp bot and receive an app link, so that I can start running my business in the App on a free trial without a manual approval step.

#### Acceptance Criteria

1. WHEN an Owner completes onboarding via the Notification_Channel bot by providing a business name, a country, and a phone number, THE Backend SHALL create an Owner User bound to the Tenant with that phone number idempotently, so that repeating onboarding for the same Tenant does not create a duplicate Owner User.
2. WHEN an Owner completes onboarding, THE Backend SHALL auto-start a 7-day free trial for the Tenant.
3. WHEN an Owner completes onboarding, THE Backend SHALL send the Owner the PWA link (the value of `settings.APP_URL`) together with add-to-home-screen guidance and sign-in instructions over the Notification_Channel.
4. WHERE the Tenant is on WhatsApp and the chat identifier is a valid phone number, THE Backend MAY use that chat identifier as the Owner's phone number and MAY skip the phone-number onboarding question.
5. THE admin SHALL be able to start or extend a trial for a specific Tenant using the existing `/trial <chat_id> [days]` command.
6. THE admin SHALL be able to activate a paid subscription for a Tenant using the existing `/approve` command.
7. THE phone number captured during onboarding SHALL be the phone number the Owner uses for PWA OTP sign-in.
8. WHEN a request-OTP is made for a phone number that resolves to a Tenant with an active Telegram/WhatsApp Notification_Channel, THE Backend SHALL deliver the one-time code over that channel, falling back to SMS only when the channel has no usable send path or does not confirm delivery (per Requirement 3). Phone-number matching SHALL treat a stored value with or without a leading `+` as equivalent.
9. WHEN an existing Owner (a Tenant with a business name already set) messages the bot and does not yet have an app sign-in, THE Backend SHALL announce the App, capture the sign-in phone number, create the Owner User idempotently without altering the Tenant's existing subscription state, and send the clickable App link — the Owner's existing products, customers, orders and invoices being already available in the App on sign-in.
10. WHEN a signed-in User opens a tab for the first time, THE App SHALL show a one-time informational popup describing that tab, shown at most once per User account, and dismissible without blocking use of the tab.
11. WHEN the Backend sends the App link over the Notification_Channel, THE link SHALL be rendered as a clickable link.
