from app.tools._base import fn, str_prop, enum_prop


class OrderTools:
    TOOLS = [
        fn("create_order",
           "Create an order. Check customer's default address first; confirm or ask for a different one.",
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
                           "selling_price": {"type": "number"},
                       },
                       "required": ["recipe_name", "quantity", "selling_price"],
                   },
               },
           },
           required=["customer_identifier", "delivery_date", "items"]),

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
