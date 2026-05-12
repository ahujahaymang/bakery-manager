from app.tools._base import fn, str_prop, num_prop, enum_prop


class InventoryTools:
    TOOLS = [
        fn("add_inventory",
           "Add a new inventory item (ingredient or packaging)",
           {
               "name": str_prop(),
               "category": enum_prop("ingredient", "packaging"),
               "quantity": num_prop(),
               "unit": enum_prop("kg", "g", "litre", "ml", "pcs"),
               "cost_per_unit": num_prop(),
           },
           required=["name", "category", "quantity", "unit", "cost_per_unit"]),

        fn("update_inventory",
           "Update quantity or cost of an inventory item",
           {"name": str_prop(), "quantity": num_prop(), "cost_per_unit": num_prop()},
           required=["name"]),

        fn("list_inventory",
           "List inventory items with stock levels and costs. Filter by category optionally.",
           {"category": enum_prop("ingredient", "packaging")}),
    ]
