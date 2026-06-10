from app.tools._base import fn, str_prop, int_prop, num_prop, enum_prop


class OrderTools:
    TOOLS = [
        fn("create_order",
           "Create an order. Check customer's default address first; confirm or ask for a different one. "
           "ALWAYS ask for delivery date if not provided — never default to today. "
           "selling_price is the price PER UNIT — NOT the total. "
           "IMPORTANT: Only convert weight to quantity when the price is explicitly per-unit weight "
           "(e.g. '₹900 per ½ kg' → quantity=4 for 2 kg). "
           "If the price is a flat amount for the whole item (e.g. '2 kg cake at ₹2600'), "
           "set quantity=1 and selling_price=2600 — do NOT split by weight. "
           "When in doubt, treat as flat price (quantity=1). "
           "After creating, the result includes a confirmation prompt — pass it through as-is.",
           {
               "customer_identifier": str_prop("Customer name or phone"),
               "delivery_date": str_prop("YYYY-MM-DD"),
               "delivery_address": str_prop("Optional if customer has a default"),
               "items": {
                   "type": "array",
                   "items": {
                       "type": "object",
                       "properties": {
                           "recipe_name": {"type": "string"},
                           "quantity": {"type": "integer"},
                           "selling_price": {"type": "number",
                                             "description": "Price PER UNIT (not total). Total = quantity × selling_price."},
                           "customization_charge": {"type": "number",
                                                    "description": "Extra charge for customization (e.g. 200 for fondant). Added to selling_price on invoice."},
                           "customization_note": {"type": "string",
                                                  "description": "Description of customization e.g. 'fondant decoration'"},
                       },
                       "required": ["recipe_name", "quantity", "selling_price"],
                   },
               },
           },
           required=["customer_identifier", "delivery_date", "items"]),

        fn("update_order_item",
           "Edit an item on an existing order — change quantity, price, or customization. "
           "Use when owner says 'make it quantity 1', 'change price to X', 'edit the order', etc. "
           "Finds the most recent pending order for the customer unless a delivery date is given.",
           {
               "customer_identifier": str_prop("Customer name or phone"),
               "recipe_name": str_prop("Name of the item to edit"),
               "delivery_date": str_prop("YYYY-MM-DD — optional, to target a specific order"),
               "quantity": int_prop("New quantity (omit to keep unchanged)"),
               "selling_price": num_prop("New unit price (omit to keep unchanged)"),
               "customization_charge": num_prop("New customization charge (omit to keep unchanged)"),
               "customization_note": str_prop("New customization note (omit to keep unchanged)"),
           },
           required=["customer_identifier", "recipe_name"]),

        fn("cancel_order",
           "Mark order as cancelled (keeps it for analysis). Use when customer cancels.",
           {"customer_identifier": str_prop(), "delivery_date": str_prop("YYYY-MM-DD, optional")},
           required=["customer_identifier"]),

        fn("delete_order",
           "Permanently delete an order. Only for data entry mistakes.",
           {"customer_identifier": str_prop(), "delivery_date": str_prop("YYYY-MM-DD, optional")},
           required=["customer_identifier"]),

        fn("mark_delivered",
           "Mark an order as delivered",
           {"order_id": str_prop("UUID of the order")},
           required=["order_id"]),

        fn("upcoming_orders",
           "View orders. Use filter=unpaid for outstanding balances.",
           {"filter": enum_prop("paid", "unpaid", "delivered", "pending")}),
    ]
