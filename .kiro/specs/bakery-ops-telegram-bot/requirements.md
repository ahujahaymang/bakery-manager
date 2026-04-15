# Requirements Document

## Introduction
The BakeryOpsPlatform Telegram Bot (Phase 1 MVP) enables home bakery owners to manage their operations through Telegram chat. The system provides inventory tracking, recipe cost calculation, order management, payment tracking, and weekly profit reporting. This phase focuses exclusively on owner-facing functionality with a single-service architecture using FastAPI, PostgreSQL, and LLM-assisted natural language processing.

## Glossary
- **Bot**: The Telegram bot interface that receives messages from users and sends responses
- **Backend**: The FastAPI service running on AWS EC2 that processes commands and manages business logic
- **Database**: The RDS PostgreSQL instance storing all bakery operational data
- **LLM**: Large Language Model used exclusively for intent detection and entity extraction
- **Tenant**: A single bakery organization with isolated data
- **Chat_ID**: Telegram's unique identifier for a user chat session
- **Owner**: The home bakery operator using the bot
- **Customer**: A bakery customer whose information is tracked in the system
- **Inventory_Item**: An ingredient or packaging material tracked in stock
- **Recipe**: A formula defining ingredients, packaging, and yield for a bakery product
- **Order**: A customer purchase request with items, quantities, prices, and delivery date
- **Payment**: A recorded transaction for order settlement
- **Unit_Cost**: The calculated cost per unit of a recipe based on ingredients and packaging
- **Gross_Profit**: Revenue minus ingredient costs and packaging costs

## Requirements

### Requirement 1: Tenant Onboarding
**User Story:** As a bakery owner, I want to start using the bot immediately when I send my first message, so that I can begin managing my bakery operations without complex setup.

#### Acceptance Criteria
1. WHEN a message is received from an unknown Chat_ID, THE Backend SHALL create a new Tenant record
2. WHEN creating a Tenant, THE Backend SHALL link the Chat_ID to the Tenant_ID
3. THE Backend SHALL store the Tenant_ID as a UUID
4. THE Backend SHALL store created_at and updated_at timestamps for the Tenant
5. WHEN Tenant creation completes, THE Bot SHALL send a welcome message to the Owner

### Requirement 2: Customer Creation
**User Story:** As a bakery owner, I want to add new customers with their name and phone number, so that I can track who my customers are and link orders to them.

#### Acceptance Criteria
1. WHEN the Owner requests to create a customer, THE LLM SHALL extract the customer name and phone number from the message
2. THE Backend SHALL validate that both name and phone number are provided
3. IF name or phone number is missing, THEN THE Bot SHALL request the missing information
4. THE Backend SHALL check if the phone number already exists for the Tenant
5. IF the phone number exists, THEN THE Bot SHALL return an error message indicating duplicate customer
6. THE Backend SHALL create a Customer record with name, phone, Tenant_ID, created_at, and updated_at
7. WHEN Customer creation succeeds, THE Bot SHALL confirm the customer was added

### Requirement 3: Customer Retrieval and Disambiguation
**User Story:** As a bakery owner, I want to retrieve customer information by name or phone, so that I can verify customer details and handle cases where multiple customers share the same name.

#### Acceptance Criteria
1. WHEN the Owner requests customer information, THE LLM SHALL extract the search criteria (name or phone)
2. THE Backend SHALL query Customers filtered by Tenant_ID and the search criteria
3. IF exactly one Customer matches, THEN THE Bot SHALL display the customer details
4. IF multiple Customers match by name, THEN THE Bot SHALL display all matching customers with their phone numbers for disambiguation
5. IF no Customer matches, THEN THE Bot SHALL return a message indicating customer not found
6. THE Backend SHALL treat phone number as the unique identifier per Tenant

### Requirement 4: Customer Listing
**User Story:** As a bakery owner, I want to see a list of all my customers, so that I can review who I'm serving.

#### Acceptance Criteria
1. WHEN the Owner requests to list customers, THE Backend SHALL retrieve all Customers for the Tenant_ID
2. THE Bot SHALL display customer names and phone numbers
3. IF no Customers exist, THEN THE Bot SHALL return a message indicating no customers found

### Requirement 5: Inventory Item Creation
**User Story:** As a bakery owner, I want to add inventory items with category, quantity, unit, and cost per unit, so that I can track what materials I have and their costs.

#### Acceptance Criteria
1. WHEN the Owner requests to add inventory, THE LLM SHALL extract item name, category, quantity, unit, and cost per unit
2. THE Backend SHALL validate that category is either "ingredient" or "packaging"
3. THE Backend SHALL validate that unit is one of: kg, g, litre, ml, pcs
4. IF required fields are missing, THEN THE Bot SHALL request the missing information
5. THE Backend SHALL create an Inventory_Item record with name, category, quantity, unit, cost_per_unit, Tenant_ID, created_at, and updated_at
6. WHEN Inventory_Item creation succeeds, THE Bot SHALL confirm the item was added

### Requirement 6: Inventory Update
**User Story:** As a bakery owner, I want to update the quantity and cost of inventory items, so that I can keep my stock levels and costs accurate.

#### Acceptance Criteria
1. WHEN the Owner requests to update inventory, THE LLM SHALL extract the item name and the fields to update (quantity or cost_per_unit)
2. THE Backend SHALL retrieve the Inventory_Item by name and Tenant_ID
3. IF the item does not exist, THEN THE Bot SHALL return an error message
4. THE Backend SHALL update the specified fields and set updated_at timestamp
5. THE Backend SHALL log the update in audit_logs with old and new values
6. WHEN the update succeeds, THE Bot SHALL confirm the changes

### Requirement 7: Inventory Stock Check
**User Story:** As a bakery owner, I want to check the current stock level of an item, so that I know if I need to reorder supplies.

#### Acceptance Criteria
1. WHEN the Owner requests stock information, THE LLM SHALL extract the item name
2. THE Backend SHALL retrieve the Inventory_Item by name and Tenant_ID
3. IF the item exists, THEN THE Bot SHALL display name, quantity, unit, and cost_per_unit
4. IF the item does not exist, THEN THE Bot SHALL return a message indicating item not found

### Requirement 8: Inventory Listing
**User Story:** As a bakery owner, I want to see my complete inventory, so that I can review all materials I have in stock.

#### Acceptance Criteria
1. WHEN the Owner requests to list inventory, THE Backend SHALL retrieve all Inventory_Items for the Tenant_ID
2. THE Bot SHALL display items grouped by category (ingredients and packaging)
3. THE Bot SHALL show name, quantity, unit, and cost_per_unit for each item
4. IF no items exist, THEN THE Bot SHALL return a message indicating empty inventory

### Requirement 9: Recipe Creation
**User Story:** As a bakery owner, I want to create recipes with ingredients and packaging items, so that I can define what goes into my products.

#### Acceptance Criteria
1. WHEN the Owner requests to create a recipe, THE LLM SHALL extract recipe name and yield per batch
2. THE Backend SHALL create a Recipe record with name, yield_per_batch, Tenant_ID, created_at, and updated_at
3. WHEN Recipe creation succeeds, THE Bot SHALL confirm the recipe was created and prompt for ingredients

### Requirement 10: Recipe Component Addition
**User Story:** As a bakery owner, I want to add ingredients and packaging to recipes with quantities, so that I can track what materials each product requires.

#### Acceptance Criteria
1. WHEN the Owner adds components to a recipe, THE LLM SHALL extract recipe name, item name, quantity, and component type
2. THE Backend SHALL validate that the Inventory_Item exists for the Tenant_ID
3. THE Backend SHALL validate that component type is either "ingredient" or "packaging"
4. THE Backend SHALL create a Recipe_Component record linking Recipe_ID, Inventory_Item_ID, quantity, and type
5. WHEN component addition succeeds, THE Bot SHALL confirm the component was added

### Requirement 11: Recipe Cost Calculation
**User Story:** As a bakery owner, I want to see the cost per unit of my recipes, so that I can price my products appropriately.

#### Acceptance Criteria
1. WHEN the Owner requests recipe cost, THE LLM SHALL extract the recipe name
2. THE Backend SHALL retrieve all Recipe_Components for the Recipe filtered by Tenant_ID
3. THE Backend SHALL calculate ingredient_cost as SUM(component.quantity × inventory_item.cost_per_unit) for all ingredient components
4. THE Backend SHALL calculate packaging_cost as SUM(component.quantity × inventory_item.cost_per_unit) for all packaging components
5. THE Backend SHALL calculate unit_cost as (ingredient_cost + packaging_cost) / recipe.yield_per_batch
6. THE Bot SHALL display ingredient_cost, packaging_cost, and unit_cost
7. THE Backend SHALL NOT use LLM for cost calculations

### Requirement 12: Order Creation
**User Story:** As a bakery owner, I want to create orders with customer, items, quantities, prices, and delivery date, so that I can track what needs to be fulfilled.

#### Acceptance Criteria
1. WHEN the Owner creates an order, THE LLM SHALL extract customer identifier, delivery date, and order items
2. THE Backend SHALL resolve the Customer by name or phone for the Tenant_ID
3. IF multiple Customers match by name, THEN THE Bot SHALL request disambiguation via phone number
4. THE Backend SHALL create an Order record with Customer_ID, delivery_date, status "pending", Tenant_ID, created_at, and updated_at
5. FOR EACH order item, THE Backend SHALL create an Order_Item record with Recipe_ID, quantity, and selling_price
6. WHEN Order creation succeeds, THE Bot SHALL confirm the order with order details

### Requirement 13: Order Delivery Marking
**User Story:** As a bakery owner, I want to mark orders as delivered, so that I can track which orders are complete and calculate revenue.

#### Acceptance Criteria
1. WHEN the Owner marks an order delivered, THE LLM SHALL extract the order identifier
2. THE Backend SHALL retrieve the Order by identifier and Tenant_ID
3. THE Backend SHALL update the Order status to "delivered" and set updated_at timestamp
4. THE Backend SHALL log the status change in audit_logs
5. WHEN the update succeeds, THE Bot SHALL confirm the order was marked delivered

### Requirement 14: Upcoming Orders View
**User Story:** As a bakery owner, I want to see orders scheduled for upcoming delivery, so that I can plan my baking schedule.

#### Acceptance Criteria
1. WHEN the Owner requests upcoming orders, THE Backend SHALL retrieve all Orders with status "pending" for the Tenant_ID
2. THE Backend SHALL sort Orders by delivery_date ascending
3. THE Bot SHALL display order details including customer name, items, quantities, delivery date, and total price
4. IF no upcoming orders exist, THEN THE Bot SHALL return a message indicating no pending orders

### Requirement 15: Payment Recording
**User Story:** As a bakery owner, I want to record payments received from customers, so that I can track which orders are paid.

#### Acceptance Criteria
1. WHEN the Owner records a payment, THE LLM SHALL extract order identifier, amount, and payment method
2. THE Backend SHALL validate that payment method is one of: Cash, Paytm, Bank Transfer
3. THE Backend SHALL retrieve the Order by identifier and Tenant_ID
4. THE Backend SHALL create a Payment record with Order_ID, amount, method, Tenant_ID, created_at, and updated_at
5. WHEN Payment creation succeeds, THE Bot SHALL confirm the payment was recorded

### Requirement 16: Unpaid Orders View
**User Story:** As a bakery owner, I want to see which orders haven't been paid, so that I can follow up with customers.

#### Acceptance Criteria
1. WHEN the Owner requests unpaid orders, THE Backend SHALL retrieve all Orders for the Tenant_ID
2. THE Backend SHALL LEFT JOIN with Payments to identify Orders with no associated Payment or partial payment
3. THE Bot SHALL display order details including customer name, total amount, and amount paid
4. IF all orders are paid, THEN THE Bot SHALL return a message indicating no unpaid orders

### Requirement 17: Payment History View
**User Story:** As a bakery owner, I want to see payment history, so that I can review transaction records.

#### Acceptance Criteria
1. WHEN the Owner requests payment history, THE Backend SHALL retrieve all Payments for the Tenant_ID
2. THE Backend SHALL sort Payments by created_at descending
3. THE Bot SHALL display payment details including customer name, order reference, amount, method, and date
4. WHERE a date range is specified, THE Backend SHALL filter Payments by created_at within the range

### Requirement 18: Weekly Profit Calculation
**User Story:** As a bakery owner, I want to see my gross profit for the current week, so that I can understand my business performance.

#### Acceptance Criteria
1. WHEN the Owner requests weekly profit, THE Backend SHALL calculate the date range for the current week (Monday to Sunday)
2. THE Backend SHALL retrieve all Orders with status "delivered" and delivery_date within the week for the Tenant_ID
3. THE Backend SHALL calculate total_revenue as SUM(order_item.quantity × order_item.selling_price) for all delivered orders
4. THE Backend SHALL calculate total_ingredient_cost by summing recipe ingredient costs for all delivered order items
5. THE Backend SHALL calculate total_packaging_cost by summing recipe packaging costs for all delivered order items
6. THE Backend SHALL calculate gross_profit as total_revenue - total_ingredient_cost - total_packaging_cost
7. THE Bot SHALL display total_revenue, total_ingredient_cost, total_packaging_cost, and gross_profit
8. THE Backend SHALL NOT use LLM for profit calculations

### Requirement 19: Tenant Data Isolation
**User Story:** As a bakery owner, I want my data to be completely separate from other bakeries, so that my business information remains private.

#### Acceptance Criteria
1. THE Backend SHALL include Tenant_ID in every database query WHERE clause
2. THE Backend SHALL derive Tenant_ID from the Chat_ID for every request
3. THE Backend SHALL prevent cross-tenant data access
4. THE Backend SHALL validate Tenant_ID exists before processing any command
5. IF Tenant_ID cannot be resolved from Chat_ID, THEN THE Backend SHALL trigger onboarding

### Requirement 20: Input Validation and Security
**User Story:** As a bakery owner, I want the system to validate my inputs, so that I don't accidentally enter incorrect data.

#### Acceptance Criteria
1. THE Backend SHALL validate all numeric inputs are positive numbers
2. THE Backend SHALL validate phone numbers contain only digits and optional country code
3. THE Backend SHALL validate dates are in valid format and not in the past for orders
4. THE Backend SHALL sanitize all text inputs to prevent SQL injection
5. IF validation fails, THEN THE Bot SHALL return a descriptive error message

### Requirement 21: Audit Logging
**User Story:** As a bakery owner, I want the system to log all data changes, so that I can review what happened if something looks wrong.

#### Acceptance Criteria
1. WHEN any UPDATE or DELETE operation occurs, THE Backend SHALL create an audit_log record
2. THE Backend SHALL store Tenant_ID, table_name, record_id, operation_type, old_values, new_values, and timestamp
3. THE Backend SHALL log inventory updates with quantity and cost changes
4. THE Backend SHALL log order status changes
5. THE Backend SHALL NOT log SELECT operations

### Requirement 22: LLM Intent Detection
**User Story:** As a bakery owner, I want to use natural language to interact with the bot, so that I don't need to memorize specific commands.

#### Acceptance Criteria
1. WHEN a message is received, THE LLM SHALL classify the intent (create_customer, add_inventory, create_order, etc.)
2. THE LLM SHALL extract entities from the message (names, quantities, dates, prices)
3. THE Backend SHALL validate extracted entities before processing
4. IF the LLM cannot determine intent with confidence, THEN THE Bot SHALL ask for clarification
5. THE LLM SHALL NOT execute business logic or database operations
6. THE LLM SHALL NOT perform calculations

### Requirement 23: Error Handling and User Feedback
**User Story:** As a bakery owner, I want clear error messages when something goes wrong, so that I know how to fix the issue.

#### Acceptance Criteria
1. WHEN a database error occurs, THE Backend SHALL log the error and return a user-friendly message
2. WHEN validation fails, THE Bot SHALL explain what was invalid and how to correct it
3. WHEN a resource is not found, THE Bot SHALL clearly state what was not found
4. THE Backend SHALL NOT expose internal error details or stack traces to the Owner
5. THE Backend SHALL return error messages in a consistent format

### Requirement 24: Recipe Parsing and Pretty Printing
**User Story:** As a bakery owner, I want to view my recipes in a readable format, so that I can review what goes into each product.

#### Acceptance Criteria
1. WHEN the Owner requests recipe details, THE Backend SHALL retrieve the Recipe and all Recipe_Components
2. THE Pretty_Printer SHALL format the recipe with name, yield, ingredients list, and packaging list
3. THE Pretty_Printer SHALL display quantities with appropriate units
4. THE Pretty_Printer SHALL calculate and display total cost per batch and cost per unit
5. FOR ALL valid Recipe objects, retrieving then formatting then parsing SHALL produce an equivalent object (round-trip property)
