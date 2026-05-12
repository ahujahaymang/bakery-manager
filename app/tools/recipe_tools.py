from app.tools._base import fn, str_prop, num_prop, int_prop, enum_prop


class RecipeTools:
    TOOLS = [
        fn("create_recipe",
           "Create a recipe. Returns ALREADY_EXISTS if name taken — ask user to replace or rename.",
           {"name": str_prop(), "yield_per_batch": int_prop("Units produced per batch")},
           required=["name", "yield_per_batch"]),

        fn("list_recipes",
           "List all recipes",
           {}),

        fn("get_recipe",
           "Get full recipe details: ingredients, packaging, and cost per unit",
           {"name": str_prop()},
           required=["name"]),

        fn("update_recipe",
           "Rename a recipe or change its yield",
           {"name": str_prop("Current name"), "new_name": str_prop(), "new_yield": int_prop()},
           required=["name"]),

        fn("delete_recipe",
           "Permanently delete a recipe and all its components",
           {"name": str_prop()},
           required=["name"]),

        fn("add_recipe_component",
           "Add an ingredient or packaging to a recipe. "
           "Auto-creates missing inventory items with cost 0 — never call add_inventory first.",
           {
               "recipe_name": str_prop(),
               "item_name": str_prop(),
               "quantity": num_prop(),
               "component_type": enum_prop("ingredient", "packaging"),
           },
           required=["recipe_name", "item_name", "quantity", "component_type"]),

        fn("remove_recipe_component",
           "Remove an ingredient or packaging from a recipe",
           {"recipe_name": str_prop(), "item_name": str_prop()},
           required=["recipe_name", "item_name"]),

        fn("update_recipe_component",
           "Change the quantity of a component in a recipe",
           {"recipe_name": str_prop(), "item_name": str_prop(), "quantity": num_prop()},
           required=["recipe_name", "item_name", "quantity"]),
    ]
