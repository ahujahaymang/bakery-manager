"""
Disambiguation Helper for handling multiple matches.

This module provides utilities for creating disambiguation prompts
and parsing user choices when multiple customers or recipes match.
"""

from typing import List, Dict, Optional, Any
import logging

logger = logging.getLogger(__name__)


class DisambiguationHelper:
    """
    Helper class for handling disambiguation when multiple items match.
    
    Provides methods to create prompts and parse user choices for
    customers, recipes, and other entities.
    """
    
    @staticmethod
    def create_customer_options(customers: List[Any]) -> List[Dict[str, str]]:
        """
        Convert customer objects to option dictionaries.
        
        Args:
            customers: List of Customer model objects
        
        Returns:
            List of dicts with name, phone, and id
        """
        return [
            {
                'name': c.name,
                'phone': c.phone,
                'id': str(c.customer_id)
            }
            for c in customers
        ]
    
    @staticmethod
    def create_customer_prompt(customer_identifier: str, customers: List[Any]) -> str:
        """
        Create a disambiguation prompt for multiple customer matches.
        
        Args:
            customer_identifier: The search term that matched multiple customers
            customers: List of Customer model objects
        
        Returns:
            Formatted prompt string for Telegram
        """
        result = f"🤔 I found multiple customers named '*{customer_identifier}*':\n\n"
        for idx, customer in enumerate(customers, 1):
            result += f"{idx}. {customer.name} ({customer.phone})\n"
        result += f"\nWhich one? Reply with the number or phone number."
        return result
    
    @staticmethod
    def create_recipe_options(recipes: List[Any]) -> List[Dict[str, str]]:
        """
        Convert recipe objects to option dictionaries.
        
        Args:
            recipes: List of Recipe model objects
        
        Returns:
            List of dicts with name and id
        """
        return [
            {
                'name': r.name,
                'id': str(r.recipe_id)
            }
            for r in recipes
        ]
    
    @staticmethod
    def create_recipe_prompt(recipe_name: str, recipes: List[Any]) -> str:
        """
        Create a disambiguation prompt for multiple recipe matches.
        
        Args:
            recipe_name: The search term that matched multiple recipes
            recipes: List of Recipe model objects
        
        Returns:
            Formatted prompt string for Telegram
        """
        result = f"🤔 I found multiple recipes matching '*{recipe_name}*':\n\n"
        for idx, recipe in enumerate(recipes, 1):
            result += f"{idx}. {recipe.name}\n"
        result += f"\nWhich one should I use? Reply with the number or full name."
        return result
    
    @staticmethod
    def parse_customer_choice(
        text: str,
        customer_options: List[Dict[str, str]]
    ) -> Optional[Dict[str, str]]:
        """
        Parse user's choice from customer disambiguation.
        
        Args:
            text: User's input text
            customer_options: List of customer option dicts
        
        Returns:
            Selected customer dict or None if invalid choice
        """
        text_lower = text.strip().lower()
        
        # Try to parse as number first (for menu selection like "1", "2")
        try:
            choice_num = int(text_lower)
            # Only treat as menu choice if it's in valid range
            if 1 <= choice_num <= len(customer_options):
                return customer_options[choice_num - 1]
            # If out of range, fall through to phone matching
        except ValueError:
            pass
        
        # Try to match by phone (must be at least 4 digits)
        if len(text_lower) >= 4 and text_lower.isdigit():
            for customer_dict in customer_options:
                phone = customer_dict['phone'].lower()
                if text_lower in phone:
                    return customer_dict
        
        return None
    
    @staticmethod
    def parse_recipe_choice(
        text: str,
        recipe_options: List[Dict[str, str]]
    ) -> Optional[Dict[str, str]]:
        """
        Parse user's choice from recipe disambiguation.
        
        Args:
            text: User's input text
            recipe_options: List of recipe option dicts
        
        Returns:
            Selected recipe dict or None if invalid choice
        """
        text_lower = text.strip().lower()
        
        # Try to parse as number
        try:
            choice_num = int(text_lower)
            if 1 <= choice_num <= len(recipe_options):
                return recipe_options[choice_num - 1]
        except ValueError:
            pass
        
        # Try to match by name
        for recipe_dict in recipe_options:
            if text_lower in recipe_dict['name'].lower():
                return recipe_dict
        
        return None
    
    @staticmethod
    def create_invalid_choice_prompt(options: List[Dict[str, str]], option_type: str = "customer") -> str:
        """
        Create a prompt for invalid choice.
        
        Args:
            options: List of option dicts (customer or recipe)
            option_type: Type of option ("customer" or "recipe")
        
        Returns:
            Formatted prompt string
        """
        result = "I didn't understand your choice. Please reply with:\n"
        
        if option_type == "customer":
            for i, opt in enumerate(options, 1):
                result += f"{idx}. {opt['name']} ({opt['phone']})\n"
        else:  # recipe
            for i, opt in enumerate(options, 1):
                result += f"{i}. {opt['name']}\n"
        
        return result
