"""
Agent Service - LLM agent with tool calling.

The LLM decides what tools to call based on the conversation.
No manual intent routing or state machines needed.
"""

import json
import logging
from typing import Any, Dict, List, Optional
from uuid import UUID

from app.llm_client import LLMClient
from app.config import settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tool definitions (what the LLM sees)
# ---------------------------------------------------------------------------

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "create_customer",
            "description": "Add a new customer",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "phone": {"type": "string"}
                },
                "required": ["name", "phone"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_customer",
            "description": "Search for a customer by name or phone",
            "parameters": {
                "type": "object",
                "properties": {
                    "search": {"type": "string", "description": "Name or phone number"}
                },
                "required": ["search"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_customers",
            "description": "List all customers",
            "parameters": {"type": "object", "properties": {}}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "add_inventory",
            "description": "Add a new inventory item",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "category": {"type": "string", "enum": ["ingredient", "packaging"]},
                    "quantity": {"type": "number"},
                    "unit": {"type": "string", "enum": ["kg", "g", "litre", "ml", "pcs"]},
                    "cost_per_unit": {"type": "number"}
                },
                "required": ["name", "category", "quantity", "unit", "cost_per_unit"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "update_inventory",
            "description": "Update quantity or cost of an inventory item",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "quantity": {"type": "number"},
                    "cost_per_unit": {"type": "number"}
                },
                "required": ["name"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "check_stock",
            "description": "Check stock level for an inventory item",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"}
                },
                "required": ["name"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_inventory",
            "description": "List inventory items, optionally filtered by category",
            "parameters": {
                "type": "object",
                "properties": {
                    "category": {"type": "string", "enum": ["ingredient", "packaging"], "description": "Optional filter"}
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "create_recipe",
            "description": "Create a new recipe. If a recipe with the same name already exists, this will return an error with the existing recipe details so you can ask the user whether to replace it or use a different name.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "yield_per_batch": {"type": "integer", "description": "Number of units produced per batch"}
                },
                "required": ["name", "yield_per_batch"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "replace_recipe",
            "description": "Delete an existing recipe and recreate it with a new yield. Use this when the user confirms they want to replace/overwrite an existing recipe.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "yield_per_batch": {"type": "integer"}
                },
                "required": ["name", "yield_per_batch"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_recipes",
            "description": "List all recipes",
            "parameters": {"type": "object", "properties": {}}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_recipe",
            "description": "Get full details of a recipe including ingredients and cost",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"}
                },
                "required": ["name"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "update_recipe",
            "description": "Rename a recipe or change its yield per batch",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Current recipe name"},
                    "new_name": {"type": "string"},
                    "new_yield": {"type": "integer"}
                },
                "required": ["name"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "add_recipe_component",
            "description": "Add an ingredient or packaging item to a recipe",
            "parameters": {
                "type": "object",
                "properties": {
                    "recipe_name": {"type": "string"},
                    "item_name": {"type": "string"},
                    "quantity": {"type": "number"},
                    "component_type": {"type": "string", "enum": ["ingredient", "packaging"]}
                },
                "required": ["recipe_name", "item_name", "quantity", "component_type"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "remove_recipe_component",
            "description": "Remove an ingredient or packaging item from a recipe",
            "parameters": {
                "type": "object",
                "properties": {
                    "recipe_name": {"type": "string"},
                    "item_name": {"type": "string"}
                },
                "required": ["recipe_name", "item_name"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "update_recipe_component",
            "description": "Update the quantity of a component in a recipe",
            "parameters": {
                "type": "object",
                "properties": {
                    "recipe_name": {"type": "string"},
                    "item_name": {"type": "string"},
                    "quantity": {"type": "number"}
                },
                "required": ["recipe_name", "item_name", "quantity"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "delete_recipe",
            "description": "Permanently delete a recipe and all its components",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"}
                },
                "required": ["name"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "calculate_recipe_cost",
            "description": "Calculate the cost breakdown for a recipe",
            "parameters": {
                "type": "object",
                "properties": {
                    "recipe_name": {"type": "string"}
                },
                "required": ["recipe_name"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "create_order",
            "description": "Create a new order for a customer",
            "parameters": {
                "type": "object",
                "properties": {
                    "customer_identifier": {"type": "string", "description": "Customer name or phone"},
                    "delivery_date": {"type": "string", "description": "ISO date YYYY-MM-DD"},
                    "items": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "recipe_name": {"type": "string"},
                                "quantity": {"type": "integer"},
                                "selling_price": {"type": "number"}
                            },
                            "required": ["recipe_name", "quantity", "selling_price"]
                        }
                    }
                },
                "required": ["customer_identifier", "delivery_date", "items"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "cancel_order",
            "description": "Mark an order as cancelled (keeps it in DB for analysis). Use when customer cancels.",
            "parameters": {
                "type": "object",
                "properties": {
                    "customer_identifier": {"type": "string"},
                    "delivery_date": {"type": "string", "description": "ISO date YYYY-MM-DD, optional"}
                },
                "required": ["customer_identifier"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "delete_order",
            "description": "Permanently delete an order. Use only for data entry mistakes.",
            "parameters": {
                "type": "object",
                "properties": {
                    "customer_identifier": {"type": "string"},
                    "delivery_date": {"type": "string", "description": "ISO date YYYY-MM-DD, optional"}
                },
                "required": ["customer_identifier"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "mark_delivered",
            "description": "Mark an order as delivered",
            "parameters": {
                "type": "object",
                "properties": {
                    "order_id": {"type": "string", "description": "UUID of the order"}
                },
                "required": ["order_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "upcoming_orders",
            "description": "View upcoming orders, optionally filtered by status",
            "parameters": {
                "type": "object",
                "properties": {
                    "filter": {"type": "string", "enum": ["paid", "unpaid", "delivered", "pending"]}
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "unpaid_orders",
            "description": "View all orders with outstanding balance",
            "parameters": {"type": "object", "properties": {}}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "record_payment",
            "description": "Record a payment for an order",
            "parameters": {
                "type": "object",
                "properties": {
                    "order_identifier": {"type": "string", "description": "Customer name, phone, or order UUID"},
                    "amount": {"type": "number"},
                    "method": {"type": "string", "enum": ["Cash", "Paytm", "Bank Transfer"]}
                },
                "required": ["order_identifier", "amount", "method"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "payment_history",
            "description": "View payment history",
            "parameters": {
                "type": "object",
                "properties": {
                    "start_date": {"type": "string", "description": "ISO date YYYY-MM-DD"},
                    "end_date": {"type": "string", "description": "ISO date YYYY-MM-DD"}
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "weekly_profit",
            "description": "Calculate this week's profit report",
            "parameters": {"type": "object", "properties": {}}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "generate_invoice",
            "description": "Generate a PDF invoice for an order and send it to the user. Use when the user asks to create or send an invoice for a customer's order. delivery_date is optional — if omitted, uses the most recent order for that customer.",
            "parameters": {
                "type": "object",
                "properties": {
                    "customer_identifier": {
                        "type": "string",
                        "description": "Customer name or phone number"
                    },
                    "delivery_date": {
                        "type": "string",
                        "description": "ISO date YYYY-MM-DD — optional, only needed if customer has multiple orders"
                    }
                },
                "required": ["customer_identifier"]
            }
        }
    }
]

SYSTEM_PROMPT = """You are a bakery operations assistant. Help the user manage their bakery business.

Today's date: {today}

You have tools to manage customers, inventory, recipes, orders, and payments.

Guidelines:
- Always use tools to perform actions - never make up data
- When a tool returns an error (e.g. recipe already exists), explain it to the user and ask what they want to do
- For ambiguous requests (multiple customers match, etc.), ask the user to clarify
- Keep responses concise and friendly
- Use ₹ for currency
- Dates should be in YYYY-MM-DD format when calling tools
- When user says "replace" or "yes" or "new name" in context of a previous question, understand the context and act accordingly
"""


class AgentService:
    """
    LLM agent that uses tool calling to handle all bakery operations.
    
    The LLM decides what tools to call. No manual intent routing needed.
    Conversation history provides context for follow-up messages like "replace" or "yes".
    """

    def __init__(self, llm_client: Optional[LLMClient] = None):
        self.llm_client = llm_client or LLMClient()

    async def run(
        self,
        user_message: str,
        history: List[Dict],
        tool_executor,  # callable: (tool_name, args) -> str
    ) -> str:
        """
        Run one turn of the agent loop.

        Args:
            user_message: The user's latest message
            history: Previous messages in this conversation (list of {role, content})
            tool_executor: Async callable that executes a tool and returns a string result

        Returns:
            str: The assistant's final text response
        """
        from datetime import date
        today = date.today().isoformat()

        system = SYSTEM_PROMPT.format(today=today)

        messages = [{"role": "system", "content": system}]
        messages.extend(history)
        messages.append({"role": "user", "content": user_message})

        # Agent loop - keep calling until we get a text response (no more tool calls)
        for _ in range(10):  # max 10 tool calls per turn to prevent infinite loops
            response = await self.llm_client.call_llm(
                messages=messages,
                temperature=0.3,
                max_tokens=2000,
                tools=TOOLS,
                tool_choice="auto"
            )

            choice = response.get("choices", [{}])[0]
            message = choice.get("message", {})
            finish_reason = choice.get("finish_reason", "stop")

            # Append assistant message to history
            messages.append(message)

            if finish_reason == "tool_calls" or message.get("tool_calls"):
                # Execute all tool calls
                tool_calls = message.get("tool_calls", [])
                for tool_call in tool_calls:
                    tool_name = tool_call["function"]["name"]
                    try:
                        args = json.loads(tool_call["function"]["arguments"])
                    except json.JSONDecodeError:
                        args = {}

                    logger.info(f"Tool call: {tool_name}({args})")

                    try:
                        result = await tool_executor(tool_name, args)
                    except Exception as e:
                        result = f"Error: {str(e)}"

                    logger.info(f"Tool result: {result[:200] if len(str(result)) > 200 else result}")

                    # Invoice PDF — return immediately without sending back to LLM
                    if isinstance(result, str) and result.startswith("INVOICE_PDF:"):
                        return result

                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call["id"],
                        "content": str(result)
                    })
            else:
                # Text response - we're done
                return message.get("content", "")

        return "I ran into an issue processing your request. Please try again."

    async def close(self):
        await self.llm_client.close()
