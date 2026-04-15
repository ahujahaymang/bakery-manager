# Implementation Plan: Bakery Operations Telegram Bot

## Overview

This implementation plan breaks down the Bakery Operations Telegram Bot into discrete, manageable tasks. The system uses FastAPI for the backend, PostgreSQL for data storage, Telegram Bot API for messaging, and an LLM for natural language intent detection. The implementation follows a bottom-up approach: infrastructure setup, database layer, core services, API layer, LLM integration, testing, and deployment.

## Tasks

- [x] 1. Project setup and infrastructure configuration
  - [x] 1.1 Initialize Python project with FastAPI, SQLAlchemy, and dependencies
    - Create project directory structure (app/, tests/, migrations/)
    - Set up pyproject.toml or requirements.txt with FastAPI, SQLAlchemy, psycopg2, python-telegram-bot, httpx, pytest, hypothesis
    - Configure Python virtual environment
    - _Requirements: All requirements depend on proper project setup_

  - [x] 1.2 Configure PostgreSQL database connection
    - Create database configuration module with connection pooling
    - Set up environment variables for database credentials (DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD)
    - Implement database session management with SQLAlchemy
    - _Requirements: 19.1, 19.2_

  - [x] 1.3 Configure Telegram Bot API integration
    - Set up Telegram bot token configuration
    - Create Telegram client wrapper for sending messages
    - Implement webhook endpoint registration
    - _Requirements: 1.5, 23.1_

  - [x] 1.4 Configure LLM service integration
    - Set up LLM API client (OpenAI, Anthropic, or similar)
    - Create configuration for API keys and endpoints
    - Implement retry logic for LLM service calls
    - _Requirements: 22.1, 22.2_

- [ ] 2. Database schema implementation
  - [x] 2.1 Create database models with SQLAlchemy
    - Implement Tenant model with chat_id unique constraint
    - Implement Customer model with tenant_id foreign key and (tenant_id, phone) unique constraint
    - Implement InventoryItem model with tenant_id foreign key and (tenant_id, name) unique constraint
    - Implement Recipe model with tenant_id foreign key and (tenant_id, name) unique constraint
    - Implement RecipeComponent model with recipe_id and item_id foreign keys
    - Implement Order model with tenant_id and customer_id foreign keys
    - Implement OrderItem model with order_id and recipe_id foreign keys
    - Implement Payment model with tenant_id and order_id foreign keys
    - Implement AuditLog model with tenant_id foreign key
    - _Requirements: 1.1, 2.6, 5.5, 9.2, 12.4, 15.4, 21.1_

  - [x] 2.2 Create database migration scripts
    - Set up Alembic for database migrations
    - Create initial migration with all tables, indexes, and constraints
    - Add indexes on tenant_id columns for query performance
    - Add indexes on foreign key columns
    - _Requirements: 19.1, 19.2_

  - [ ]* 2.3 Write property test for database schema constraints
    - **Property 3: Duplicate Phone Number Rejection**
    - **Validates: Requirements 2.4, 2.5, 3.6**

- [ ] 3. Checkpoint - Verify database setup
  - Run migrations against test database
  - Verify all tables created with correct schema
  - Ensure all tests pass, ask the user if questions arise

- [ ] 4. Implement TenantService
  - [x] 4.1 Implement tenant resolution and creation logic
    - Create get_or_create_tenant(chat_id: str) method
    - Create get_tenant_by_chat_id(chat_id: str) method
    - Ensure tenant_id is generated as UUID
    - Set created_at and updated_at timestamps
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 19.2, 19.4_

  - [ ]* 4.2 Write property test for tenant creation
    - **Property 1: Tenant Creation from Unknown Chat_ID**
    - **Validates: Requirements 1.1, 1.2, 1.3, 1.4, 1.5**

  - [ ]* 4.3 Write property test for tenant resolution
    - **Property 33: Tenant Resolution from Chat_ID**
    - **Validates: Requirements 19.2, 19.4, 19.5**

- [ ] 5. Implement CustomerService
  - [x] 5.1 Implement customer creation with validation
    - Create create_customer(tenant_id, name, phone) method
    - Validate name and phone are provided
    - Check for duplicate phone numbers within tenant
    - Return descriptive error for duplicates
    - _Requirements: 2.2, 2.3, 2.4, 2.5, 2.6, 2.7_

  - [x] 5.2 Implement customer retrieval and search
    - Create get_customer(tenant_id, search) method supporting name or phone search
    - Implement fuzzy name matching
    - Return list of matching customers
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6_

  - [x] 5.3 Implement customer listing
    - Create list_customers(tenant_id) method
    - Return all customers for tenant sorted by name
    - _Requirements: 4.1_

  - [ ]* 5.4 Write property tests for customer operations
    - **Property 2: Customer Creation with Valid Data**
    - **Validates: Requirements 2.2, 2.6, 2.7**

  - [ ]* 5.5 Write property test for duplicate phone rejection
    - **Property 3: Duplicate Phone Number Rejection**
    - **Validates: Requirements 2.4, 2.5, 3.6**

  - [ ]* 5.6 Write property test for missing data prompts
    - **Property 4: Missing Customer Data Prompts**
    - **Validates: Requirements 2.3**

  - [ ]* 5.7 Write property test for customer search tenant isolation
    - **Property 5: Customer Search Tenant Isolation**
    - **Validates: Requirements 3.2, 4.1**

  - [ ]* 5.8 Write property test for customer search disambiguation
    - **Property 6: Customer Search Disambiguation**
    - **Validates: Requirements 3.4**

  - [ ]* 5.9 Write property test for customer not found handling
    - **Property 7: Customer Not Found Handling**
    - **Validates: Requirements 3.5**

  - [ ]* 5.10 Write property test for single customer match
    - **Property 8: Single Customer Match Display**
    - **Validates: Requirements 3.3**

- [ ] 6. Implement InventoryService
  - [x] 6.1 Implement inventory item creation with validation
    - Create create_item(tenant_id, item: InventoryItemCreate) method
    - Validate category is "ingredient" or "packaging"
    - Validate unit is one of: kg, g, litre, ml, pcs
    - Validate quantity and cost_per_unit are positive
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 5.6_

  - [x] 6.2 Implement inventory item updates with audit logging
    - Create update_item(tenant_id, name, updates) method
    - Retrieve existing item by name and tenant_id
    - Update specified fields (quantity, cost_per_unit)
    - Call AuditService to log changes
    - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5, 6.6_

  - [x] 6.3 Implement inventory stock check
    - Create get_item(tenant_id, name) method
    - Return item details or not found error
    - _Requirements: 7.1, 7.2, 7.3, 7.4_

  - [x] 6.4 Implement inventory listing
    - Create list_items(tenant_id) method
    - Group items by category (ingredients, packaging)
    - Sort items within each category
    - _Requirements: 8.1, 8.2, 8.3_

  - [ ]* 6.5 Write property tests for inventory operations
    - **Property 9: Inventory Item Creation with Valid Data**
    - **Validates: Requirements 5.5, 5.6**

  - [ ]* 6.6 Write property test for category validation
    - **Property 10: Inventory Category Validation**
    - **Validates: Requirements 5.2**

  - [ ]* 6.7 Write property test for unit validation
    - **Property 11: Inventory Unit Validation**
    - **Validates: Requirements 5.3**

  - [ ]* 6.8 Write property test for missing data prompts
    - **Property 12: Missing Inventory Data Prompts**
    - **Validates: Requirements 5.4**

  - [ ]* 6.9 Write property test for inventory update with audit logging
    - **Property 13: Inventory Update with Audit Logging**
    - **Validates: Requirements 6.4, 6.5, 6.6, 21.3**

  - [ ]* 6.10 Write property test for item not found handling
    - **Property 14: Inventory Item Not Found Handling**
    - **Validates: Requirements 6.3, 7.4**

  - [ ]* 6.11 Write property test for inventory tenant isolation
    - **Property 15: Inventory Tenant Isolation**
    - **Validates: Requirements 6.2, 7.2, 8.1**

  - [ ]* 6.12 Write property test for inventory listing grouped by category
    - **Property 16: Inventory Listing Grouped by Category**
    - **Validates: Requirements 8.2, 8.3**

- [ ] 7. Checkpoint - Verify customer and inventory services
  - Test customer creation, search, and listing
  - Test inventory creation, updates, and listing
  - Verify audit logs are created for inventory updates
  - Ensure all tests pass, ask the user if questions arise

- [ ] 8. Implement RecipeService
  - [x] 8.1 Implement recipe creation
    - Create create_recipe(tenant_id, name, yield_per_batch) method
    - Validate yield_per_batch is positive integer
    - Create Recipe record with all required fields
    - _Requirements: 9.1, 9.2, 9.3_

  - [x] 8.2 Implement recipe component addition
    - Create add_component(tenant_id, recipe_name, component) method
    - Validate inventory item exists for tenant
    - Validate component type is "ingredient" or "packaging"
    - Validate quantity is positive
    - Create RecipeComponent record
    - _Requirements: 10.1, 10.2, 10.3, 10.4, 10.5_

  - [x] 8.3 Implement recipe cost calculation
    - Create calculate_cost(tenant_id, recipe_name) method
    - Retrieve all recipe components with inventory item costs
    - Calculate ingredient_cost = SUM(component.quantity × item.cost_per_unit) for ingredients
    - Calculate packaging_cost = SUM(component.quantity × item.cost_per_unit) for packaging
    - Calculate unit_cost = (ingredient_cost + packaging_cost) / yield_per_batch
    - Return RecipeCost object with all three values
    - _Requirements: 11.1, 11.2, 11.3, 11.4, 11.5, 11.6, 11.7_

  - [x] 8.4 Implement recipe formatting for display
    - Create format_recipe(tenant_id, recipe_name) method
    - Format recipe with name, yield, ingredients list, packaging list
    - Include quantities with units
    - Include cost breakdown
    - _Requirements: 24.1, 24.2, 24.3, 24.4_

  - [ ]* 8.5 Write property tests for recipe operations
    - **Property 17: Recipe Creation with Valid Data**
    - **Validates: Requirements 9.2, 9.3**

  - [ ]* 8.6 Write property test for recipe component addition
    - **Property 18: Recipe Component Addition with Validation**
    - **Validates: Requirements 10.2, 10.3, 10.4, 10.5**

  - [ ]* 8.7 Write property test for recipe cost calculation correctness
    - **Property 19: Recipe Cost Calculation Correctness**
    - **Validates: Requirements 11.3, 11.4, 11.5**

  - [ ]* 8.8 Write property test for recipe cost tenant isolation
    - **Property 20: Recipe Cost Tenant Isolation**
    - **Validates: Requirements 11.2**

  - [ ]* 8.9 Write property test for recipe cost display completeness
    - **Property 21: Recipe Cost Display Completeness**
    - **Validates: Requirements 11.6**

  - [ ]* 8.10 Write property test for recipe round-trip
    - **Property 42: Recipe Round-Trip Property**
    - **Validates: Requirements 24.1, 24.2, 24.3, 24.4, 24.5**

- [x] 9. Implement OrderService
  - [x] 9.1 Implement order creation with customer resolution
    - Create create_order(tenant_id, order: OrderCreate) method
    - Resolve customer by name or phone
    - Handle multiple customer matches with disambiguation
    - Validate delivery_date is not in the past
    - Create Order record with status "pending"
    - Create OrderItem records for each item
    - Validate selling_price is positive for each item
    - _Requirements: 12.1, 12.2, 12.3, 12.4, 12.5, 12.6_

  - [x] 9.2 Implement order delivery marking with audit logging
    - Create mark_delivered(tenant_id, order_id) method
    - Retrieve order by ID and tenant_id
    - Update status to "delivered"
    - Call AuditService to log status change
    - _Requirements: 13.1, 13.2, 13.3, 13.4, 13.5_

  - [x] 9.3 Implement upcoming orders retrieval
    - Create get_upcoming_orders(tenant_id) method
    - Retrieve orders with status "pending"
    - Sort by delivery_date ascending
    - Include customer name, items, quantities, delivery date, total price
    - _Requirements: 14.1, 14.2, 14.3_

  - [x] 9.4 Implement unpaid orders retrieval
    - Create get_unpaid_orders(tenant_id) method
    - LEFT JOIN orders with payments
    - Identify orders with no payment or partial payment
    - Calculate total order amount and amount paid
    - Include customer name and order details
    - _Requirements: 16.1, 16.2, 16.3_

  - [ ]* 9.5 Write property tests for order operations
    - **Property 22: Order Creation with Valid Data**
    - **Validates: Requirements 12.4, 12.5, 12.6**

  - [ ]* 9.6 Write property test for order customer resolution
    - **Property 23: Order Customer Resolution**
    - **Validates: Requirements 12.2, 12.3**

  - [ ]* 9.7 Write property test for order delivery marking with audit logging
    - **Property 24: Order Delivery Marking with Audit Logging**
    - **Validates: Requirements 13.3, 13.4, 13.5, 21.4**

  - [ ]* 9.8 Write property test for order tenant isolation
    - **Property 25: Order Tenant Isolation**
    - **Validates: Requirements 13.2**

  - [ ]* 9.9 Write property test for upcoming orders filtering and sorting
    - **Property 26: Upcoming Orders Filtering and Sorting**
    - **Validates: Requirements 14.1, 14.2, 14.3**

  - [ ]* 9.10 Write property test for unpaid orders identification
    - **Property 29: Unpaid Orders Identification**
    - **Validates: Requirements 16.1, 16.2, 16.3**

- [ ] 10. Implement PaymentService
  - [ ] 10.1 Implement payment recording
    - Create record_payment(tenant_id, payment: PaymentCreate) method
    - Validate payment method is one of: Cash, Paytm, Bank Transfer
    - Validate amount is positive
    - Retrieve order by identifier and tenant_id
    - Create Payment record
    - _Requirements: 15.1, 15.2, 15.3, 15.4, 15.5_

  - [ ] 10.2 Implement payment history retrieval
    - Create get_payment_history(tenant_id, start_date, end_date) method
    - Retrieve all payments for tenant
    - Filter by date range if provided
    - Sort by created_at descending
    - Include customer name, order reference, amount, method, date
    - _Requirements: 17.1, 17.2, 17.3, 17.4_

  - [ ]* 10.3 Write property tests for payment operations
    - **Property 27: Payment Recording with Valid Data**
    - **Validates: Requirements 15.2, 15.4, 15.5**

  - [ ]* 10.4 Write property test for payment tenant isolation
    - **Property 28: Payment Tenant Isolation**
    - **Validates: Requirements 15.3, 17.1**

  - [ ]* 10.5 Write property test for payment history sorting and filtering
    - **Property 30: Payment History Sorting and Filtering**
    - **Validates: Requirements 17.2, 17.3, 17.4**

- [ ] 11. Implement ReportingService
  - [ ] 11.1 Implement weekly profit calculation
    - Create calculate_weekly_profit(tenant_id) method
    - Calculate current week date range (Monday to Sunday)
    - Retrieve all delivered orders within the week for tenant
    - Calculate total_revenue = SUM(order_item.quantity × order_item.selling_price)
    - For each order item, retrieve recipe and calculate ingredient_cost and packaging_cost
    - Calculate total_ingredient_cost across all order items
    - Calculate total_packaging_cost across all order items
    - Calculate gross_profit = total_revenue - total_ingredient_cost - total_packaging_cost
    - Return WeeklyProfitReport with all four values
    - _Requirements: 18.1, 18.2, 18.3, 18.4, 18.5, 18.6, 18.7, 18.8_

  - [ ]* 11.2 Write property test for weekly profit calculation correctness
    - **Property 31: Weekly Profit Calculation Correctness**
    - **Validates: Requirements 18.1, 18.2, 18.3, 18.4, 18.5, 18.6, 18.7**

- [ ] 12. Implement AuditService
  - [ ] 12.1 Implement audit logging for data modifications
    - Create log_change(tenant_id, table, record_id, operation, old_values, new_values) method
    - Validate operation_type is "UPDATE" or "DELETE"
    - Create AuditLog record with all fields
    - Store old_values and new_values as JSONB
    - _Requirements: 21.1, 21.2, 21.3, 21.4_

  - [ ]* 12.2 Write property test for audit logging
    - **Property 38: Audit Logging for Modifications**
    - **Validates: Requirements 21.1, 21.2**

  - [ ]* 12.3 Write property test for no audit logging on reads
    - **Property 39: No Audit Logging for Reads**
    - **Validates: Requirements 21.5**

- [ ] 13. Checkpoint - Verify all core services
  - Test recipe creation, component addition, and cost calculation
  - Test order creation, delivery marking, and retrieval
  - Test payment recording and history
  - Test weekly profit calculation
  - Verify audit logs are created for all modifications
  - Ensure all tests pass, ask the user if questions arise

- [x] 14. Implement LLMService for intent detection
  - [x] 14.1 Implement intent classification
    - Create detect_intent(message: str) method
    - Send message to LLM with intent classification prompt
    - Parse LLM response into IntentResult object
    - Extract intent type (create_customer, add_inventory, create_order, etc.)
    - Extract entities (names, quantities, dates, prices)
    - Return confidence score
    - _Requirements: 22.1, 22.2_

  - [x] 14.2 Implement entity extraction and validation
    - Extract customer names and phone numbers
    - Extract inventory item names, categories, quantities, units, costs
    - Extract recipe names and yields
    - Extract order details (customer, items, quantities, prices, dates)
    - Extract payment details (amount, method)
    - Return structured entities dictionary
    - _Requirements: 22.2, 22.3_

  - [x] 14.3 Implement low confidence handling
    - Check confidence score threshold (e.g., 0.7)
    - Return clarification request if confidence is low
    - _Requirements: 22.4_

  - [ ]* 14.4 Write property test for LLM intent classification
    - **Property 40: LLM Intent Classification and Entity Extraction**
    - **Validates: Requirements 22.1, 22.2, 22.3, 22.4**

- [ ] 15. Implement input validation module
  - [ ] 15.1 Implement numeric input validation
    - Create validate_positive_number(value, field_name) function
    - Reject zero or negative values
    - Return descriptive error message
    - _Requirements: 20.1, 20.5_

  - [ ] 15.2 Implement phone number validation
    - Create validate_phone_number(phone) function
    - Validate format (digits and optional country code)
    - Return descriptive error message for invalid formats
    - _Requirements: 20.2, 20.5_

  - [ ] 15.3 Implement date validation
    - Create validate_order_date(date) function
    - Validate date format
    - Validate date is not in the past
    - Return descriptive error message
    - _Requirements: 20.3, 20.5_

  - [ ] 15.4 Implement SQL injection prevention
    - Use SQLAlchemy parameterized queries for all database operations
    - Sanitize all text inputs
    - _Requirements: 20.4_

  - [ ]* 15.5 Write property test for numeric input validation
    - **Property 34: Numeric Input Validation**
    - **Validates: Requirements 20.1, 20.5**

  - [ ]* 15.6 Write property test for phone number validation
    - **Property 35: Phone Number Validation**
    - **Validates: Requirements 20.2, 20.5**

  - [ ]* 15.7 Write property test for order date validation
    - **Property 36: Order Date Validation**
    - **Validates: Requirements 20.3, 20.5**

  - [ ]* 15.8 Write property test for SQL injection prevention
    - **Property 37: SQL Injection Prevention**
    - **Validates: Requirements 20.4**

- [x] 16. Implement error handling module
  - [x] 16.1 Create error response formatter
    - Create format_error_response(error_type, message, details) function
    - Return consistent JSON structure for all errors
    - Never expose internal error details or stack traces
    - _Requirements: 23.1, 23.2, 23.3, 23.4, 23.5_

  - [x] 16.2 Implement specific error handlers
    - Create handlers for validation errors
    - Create handlers for not found errors
    - Create handlers for business logic errors
    - Create handlers for system errors (database, LLM, Telegram API)
    - Each handler returns user-friendly message
    - _Requirements: 23.1, 23.2, 23.3, 23.4, 23.5_

  - [ ]* 16.3 Write property test for user-friendly error messages
    - **Property 41: User-Friendly Error Messages**
    - **Validates: Requirements 23.1, 23.2, 23.3, 23.4, 23.5**

- [x] 17. Implement Telegram webhook handler
  - [x] 17.1 Create webhook endpoint
    - Create POST /webhook endpoint in FastAPI
    - Parse Telegram webhook payload
    - Extract chat_id and message text
    - Handle Telegram-specific message formats
    - _Requirements: 1.5, 23.1_

  - [x] 17.2 Implement message routing logic
    - Call TenantService to resolve or create tenant
    - Call LLMService to detect intent
    - Route to appropriate service based on intent
    - Handle errors and return user-friendly messages
    - Send response via Telegram Bot API
    - _Requirements: 1.1, 1.2, 1.5, 22.1, 22.4, 23.1_

  - [x] 17.3 Implement intent-to-service mapping
    - Map "create_customer" intent to CustomerService.create_customer
    - Map "get_customer" intent to CustomerService.get_customer
    - Map "list_customers" intent to CustomerService.list_customers
    - Map "add_inventory" intent to InventoryService.create_item
    - Map "update_inventory" intent to InventoryService.update_item
    - Map "check_stock" intent to InventoryService.get_item
    - Map "list_inventory" intent to InventoryService.list_items
    - Map "create_recipe" intent to RecipeService.create_recipe
    - Map "add_recipe_component" intent to RecipeService.add_component
    - Map "calculate_recipe_cost" intent to RecipeService.calculate_cost
    - Map "create_order" intent to OrderService.create_order
    - Map "mark_delivered" intent to OrderService.mark_delivered
    - Map "upcoming_orders" intent to OrderService.get_upcoming_orders
    - Map "unpaid_orders" intent to OrderService.get_unpaid_orders
    - Map "record_payment" intent to PaymentService.record_payment
    - Map "payment_history" intent to PaymentService.get_payment_history
    - Map "weekly_profit" intent to ReportingService.calculate_weekly_profit
    - _Requirements: All functional requirements_

  - [x] 17.4 Implement response formatting
    - Format service responses into user-friendly Telegram messages
    - Use Telegram markdown for formatting
    - Handle lists, tables, and structured data
    - _Requirements: 1.5, 23.5_

- [ ] 18. Implement cross-tenant data access prevention
  - [ ] 18.1 Add tenant_id filtering to all database queries
    - Review all service methods
    - Ensure every query includes tenant_id in WHERE clause
    - Add tenant_id validation before processing
    - _Requirements: 19.1, 19.2, 19.3, 19.4_

  - [ ]* 18.2 Write property test for cross-tenant data access prevention
    - **Property 32: Cross-Tenant Data Access Prevention**
    - **Validates: Requirements 19.1, 19.3**

- [ ] 19. Checkpoint - Verify complete system integration
  - Test end-to-end message flow from Telegram to database
  - Test all intents with sample messages
  - Verify tenant isolation across all operations
  - Verify error handling for all error types
  - Ensure all tests pass, ask the user if questions arise

- [ ] 20. Set up property-based testing framework
  - [ ] 20.1 Configure Hypothesis for property-based testing
    - Install hypothesis package
    - Configure hypothesis settings (min_iterations=100)
    - Create custom strategies for domain types
    - _Requirements: All requirements validated by properties_

  - [ ] 20.2 Create custom Hypothesis strategies
    - Create strategy for valid phone numbers
    - Create strategy for valid inventory units (kg, g, litre, ml, pcs)
    - Create strategy for valid payment methods (Cash, Paytm, Bank Transfer)
    - Create strategy for valid categories (ingredient, packaging)
    - Create strategy for future dates
    - Create strategy for positive decimals
    - Create strategy for tenant-scoped data
    - _Requirements: All requirements validated by properties_

  - [ ] 20.3 Create test fixtures for property tests
    - Create tenant fixtures with isolated test data
    - Create database session fixtures with rollback
    - Create mock LLM service fixtures
    - Create mock Telegram API fixtures
    - _Requirements: All requirements validated by properties_

- [ ]* 21. Write remaining property-based tests
  - [ ]* 21.1 Write integration tests for end-to-end flows
    - Test complete customer creation flow
    - Test complete order creation and payment flow
    - Test complete recipe creation and cost calculation flow
    - Test weekly profit calculation with multiple orders
    - _Requirements: All functional requirements_

  - [ ]* 21.2 Write property tests for concurrent operations
    - Test concurrent customer creation with same phone
    - Test concurrent inventory updates
    - Test concurrent order creation
    - _Requirements: 19.1, 19.3_

- [ ] 22. Deployment configuration
  - [ ] 22.1 Create Docker configuration
    - Create Dockerfile for FastAPI application
    - Create docker-compose.yml for local development (app + PostgreSQL)
    - Configure environment variables
    - _Requirements: All requirements depend on deployment_

  - [ ] 22.2 Create AWS deployment configuration
    - Create EC2 deployment script or configuration
    - Configure RDS PostgreSQL connection
    - Set up environment variables for production
    - Configure Telegram webhook URL
    - _Requirements: All requirements depend on deployment_

  - [ ] 22.3 Create deployment documentation
    - Document environment variables required
    - Document database migration process
    - Document Telegram bot setup process
    - Document LLM API configuration
    - _Requirements: All requirements depend on deployment_

- [ ] 23. Final checkpoint - Complete system verification
  - Run full test suite (unit tests + property tests)
  - Verify all 42 properties pass with 100+ iterations
  - Test deployment in staging environment
  - Verify Telegram webhook integration
  - Verify LLM integration
  - Verify database migrations
  - Ensure all tests pass, ask the user if questions arise

- [ ] 24. Implement conversational state management
  - [ ] 24.1 Design conversation state schema
    - Define state structure for multi-turn conversations
    - Design state storage (Redis, database, or in-memory)
    - Define state lifecycle and expiration
    - _Requirements: Enhanced UX for missing data scenarios_
  
  - [ ] 24.2 Implement conversation context tracking
    - Create ConversationService for state management
    - Track pending actions (e.g., "waiting for phone number")
    - Store partial data from previous messages
    - Implement context expiration (e.g., 5 minutes)
    - _Requirements: Multi-turn conversation support_
  
  - [ ] 24.3 Implement smart follow-up handling
    - Detect when customer is missing and ask for phone
    - Auto-create customer when phone is provided
    - Resume order creation after customer is added
    - Handle recipe missing scenario with warnings
    - _Requirements: Seamless user experience_
  
  - [ ] 24.4 Add conversation state to webhook handler
    - Check for active conversation state before intent detection
    - Route follow-up messages to pending actions
    - Clear state after successful completion
    - _Requirements: Context-aware message processing_

- [ ] 25. Implement temporal awareness for LLM
  - [ ] 25.1 Add current date/time to LLM context
    - Include current date and time in system prompt
    - Format as "Today is YYYY-MM-DD, current time is HH:MM"
    - Update prompt to use this for relative date calculations
    - _Requirements: Accurate date handling for "tomorrow", "next week", etc._
  
  - [ ] 25.2 Enhance date parsing in LLM responses
    - Ensure LLM converts relative dates to absolute dates
    - Validate that dates are not in the past for orders
    - Handle timezone considerations
    - _Requirements: Correct date interpretation_
  
  - [ ] 25.3 Add timestamp awareness for database operations
    - Ensure all created_at and updated_at fields use current timestamp
    - Display timestamps in user-friendly format in responses
    - Track when data was last modified
    - _Requirements: Audit trail and data tracking_

## Notes

- Tasks marked with `*` are optional property-based tests and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation at key milestones
- Property tests validate universal correctness properties with minimum 100 iterations
- Unit tests validate specific examples and edge cases
- The implementation follows a bottom-up approach: infrastructure → data layer → services → API → integration → testing → deployment
- All database operations must include tenant_id filtering for data isolation
- All UPDATE and DELETE operations must create audit log entries
- All user inputs must be validated before processing
- All errors must return user-friendly messages without exposing internal details
- The LLM is used only for intent detection and entity extraction, not for business logic or calculations
