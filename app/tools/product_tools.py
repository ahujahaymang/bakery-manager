from app.tools._base import fn, str_prop, num_prop


class ProductTools:
    TOOLS = [
        fn("add_product",
           "Add a product to the catalog with size/price variants",
           {
               "name": str_prop(),
               "category": str_prop("e.g. Gourmet Cookies, Desserts, Small Bakes"),
               "description": str_prop(),
               "variants": {
                   "type": "array",
                   "description": "At least one size/price variant required",
                   "items": {
                       "type": "object",
                       "properties": {
                           "size_label": {"type": "string",
                                          "description": "e.g. '250 gms', '½ kg', 'per piece', 'standard'"},
                           "price": {"type": "number"},
                       },
                       "required": ["size_label", "price"],
                   },
               },
           },
           required=["name", "variants"]),

        fn("list_products",
           "Show the product catalog, optionally filtered by category",
           {"category": str_prop()}),

        fn("get_product",
           "Get full product details including variants and linked recipe",
           {"name": str_prop()},
           required=["name"]),

        fn("update_product_price",
           "Update the price of a product variant",
           {"name": str_prop(), "size_label": str_prop(), "price": num_prop()},
           required=["name", "size_label", "price"]),

        fn("add_product_variant",
           "Add a new size/price variant to an existing product",
           {"name": str_prop(), "size_label": str_prop(), "price": num_prop()},
           required=["name", "size_label", "price"]),

        fn("link_product_recipe",
           "Link a product to a recipe for margin calculation",
           {"product_name": str_prop(), "recipe_name": str_prop()},
           required=["product_name", "recipe_name"]),

        fn("delete_product",
           "Remove a product from the catalog",
           {"name": str_prop()},
           required=["name"]),
    ]
