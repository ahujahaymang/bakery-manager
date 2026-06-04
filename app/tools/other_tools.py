from app.tools._base import fn, str_prop, num_prop, int_prop


class OtherTools:
    TOOLS = [
        fn("connect_instagram",
           "Connect the owner's Instagram account to receive orders from DMs",
           {}),

        fn("set_order_template",
           "Save the owner's custom order form template. Call this when the owner shares "
           "the format/template they use to collect orders (e.g. a blank form with field names "
           "like Name, Phone, Delivery date, Weight, Flavour, etc.). "
           "Once saved, the agent will use it to parse future pasted orders automatically.",
           {"template": str_prop("The raw template text exactly as the owner provided it")},
           required=["template"]),

        fn("get_order_template",
           "Retrieve the owner's saved order template. Use this to remind yourself of the "
           "field names and structure before parsing a pasted order.",
           {}),

        # ── Expenses ────────────────────────────────────────────────────────

        fn("record_expense",
           "Record any business expense — ingredients, equipment, utilities, rent, marketing, etc. "
           "Call automatically when processing a purchase receipt. "
           "Also call when owner mentions buying something (e.g. 'bought an oven for ₹8500').",
           {
               "amount": num_prop("Total amount spent"),
               "vendor_name": str_prop("Shop or vendor name"),
               "expense_date": str_prop("Date in YYYY-MM-DD format"),
               "category": {"type": "string",
                            "enum": ["ingredients", "packaging", "equipment",
                                     "utilities", "rent", "marketing", "other"],
                            "description": "Type of expense"},
               "description": str_prop("What was bought, e.g. 'OTG oven 45L', 'flour 10kg', 'electricity bill'"),
               "is_capital": {"type": "boolean",
                              "description": "True for durable assets (oven, mixer) — False for running costs"},
               "notes": str_prop("Any extra detail"),
           },
           required=["amount", "expense_date", "category"]),

        fn("list_expenses",
           "Show business expenses with totals. Filter by category, date range, or capital items only. "
           "Use when owner asks 'how much did I spend on equipment', 'show my expenses', etc.",
           {
               "start_date": str_prop("YYYY-MM-DD"),
               "end_date": str_prop("YYYY-MM-DD"),
               "category": {"type": "string",
                            "enum": ["ingredients", "packaging", "equipment",
                                     "utilities", "rent", "marketing", "other"],
                            "description": "Filter by category"},
               "capital_only": {"type": "boolean",
                                "description": "True to show only capital assets (equipment)"},
           }),

        # ── Booth ──────────────────────────────────────────────────────────

        fn("create_booth_session",
           "Create a new exhibition booth session. "
           "Ask for the event name, then call create_booth_from_categories with categories=['all']. "
           "Do NOT ask about categories or products — the web app handles product selection.",
           {
               "name": str_prop("Event name, e.g. 'Pune Food Fest May 2026'"),
               "items": {
                   "type": "array",
                   "description": "Products to sell at the booth",
                   "items": {
                       "type": "object",
                       "properties": {
                           "variant_id": {"type": "string", "description": "ProductVariant UUID"},
                           "booth_price": {"type": "number", "description": "Selling price at this event"},
                           "stock_qty": {"type": "integer", "description": "Units brought (omit for unlimited)"},
                       },
                       "required": ["variant_id", "booth_price"],
                   },
               },
           },
           required=["name", "items"]),

        fn("create_booth_from_categories",
           "Create a booth session with ALL products from the specified categories at their catalog prices. "
           "Use this after the owner picks categories — no need to list individual products.",
           {
               "name": str_prop("Event name"),
               "categories": {
                   "type": "array",
                   "items": {"type": "string"},
                   "description": "Category names to include, e.g. ['Gourmet Cookies', 'Desserts']"
               },
           },
           required=["name", "categories"]),

        fn("add_booth_item",
           "Add a product variant to the currently active booth session.",
           {
               "variant_id": str_prop("ProductVariant UUID"),
               "booth_price": num_prop("Selling price at this event"),
               "stock_qty": int_prop("Units available (omit for unlimited)"),
           },
           required=["variant_id", "booth_price"]),

        fn("remove_booth_item",
           "Remove a product variant from the currently active booth session.",
           {"variant_id": str_prop("ProductVariant UUID")},
           required=["variant_id"]),

        fn("end_booth_session",
           "Close the currently active booth session. Returns a sales summary.",
           {}),

        fn("get_booth_url",
           "Return the booth web app URL for this owner. "
           "Call when the owner asks to open the booth or wants the link.",
           {}),

        fn("list_booth_sessions",
           "List all past booth sessions with dates and revenue totals.",
           {}),

        fn("get_booth_session_summary",
           "Get a detailed sales breakdown for a specific booth session.",
           {"session_name": str_prop("Session name or partial name to search for")},
           required=["session_name"]),
    ]
