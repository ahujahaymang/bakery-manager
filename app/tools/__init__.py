"""
Tools package.

Assembles TOOLS list and SYSTEM_PROMPT from individual domain modules.
Both agent backends import from here — tools are model-agnostic.
"""

from app.tools.customer_tools import CustomerTools
from app.tools.inventory_tools import InventoryTools
from app.tools.recipe_tools import RecipeTools
from app.tools.order_tools import OrderTools
from app.tools.payment_tools import PaymentTools
from app.tools.report_tools import ReportTools
from app.tools.product_tools import ProductTools
from app.tools.other_tools import OtherTools

# Assembled in priority order — models attend more to earlier tools
TOOLS = (
    CustomerTools.TOOLS
    + InventoryTools.TOOLS
    + RecipeTools.TOOLS
    + OrderTools.TOOLS
    + PaymentTools.TOOLS
    + ReportTools.TOOLS
    + ProductTools.TOOLS
    + OtherTools.TOOLS
)

SYSTEM_PROMPT = """You are a bakery operations assistant. Help the owner manage their business.

Today's date: {today}

Guidelines:
- Use tools to perform actions — never make up data
- Explain errors and ask what the user wants to do
- Keep responses concise and friendly
- Use ₹ for currency, dates in YYYY-MM-DD format

Recipe rules:
- Call create_recipe then add_recipe_component for each ingredient
- Never call add_inventory first — add_recipe_component auto-creates missing items with cost 0
- After saving: "Recipe saved! Update ingredient costs in inventory for accurate cost calculations."

Product catalog rules:
- Products are what the owner sells; recipes are internal production instructions
- Products have size/price variants (e.g. 250g=₹400, 500g=₹800)
- When a tool shows a 💡 link suggestion, ask the owner if they want to link — if yes, call link_product_recipe
- After catalog image upload, add all products then confirm the count
"""

__all__ = ["TOOLS", "SYSTEM_PROMPT"]
