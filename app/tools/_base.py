"""
Shared helpers for building tool definitions.
All tool modules import from here.
"""

from typing import List


def fn(name: str, description: str, properties: dict, required: list = None) -> dict:
    """Build an OpenAI-compatible function tool definition."""
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                **({"required": required} if required else {}),
            },
        },
    }


def str_prop(desc: str = "") -> dict:
    return {"type": "string", "description": desc} if desc else {"type": "string"}


def num_prop(desc: str = "") -> dict:
    return {"type": "number", "description": desc} if desc else {"type": "number"}


def int_prop(desc: str = "") -> dict:
    return {"type": "integer", "description": desc} if desc else {"type": "integer"}


def enum_prop(*values, desc: str = "") -> dict:
    d = {"type": "string", "enum": list(values)}
    if desc:
        d["description"] = desc
    return d
