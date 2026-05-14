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

        # ── Booth ──────────────────────────────────────────────────────────

        fn("create_booth_session",
           "Create a new exhibition booth session with the products the owner wants to sell. "
           "Call this when the owner says they want to set up a booth or exhibition. "
           "First call list_products to show the catalog, then create the session with the "
           "items the owner specifies. Returns the booth URL.",
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
