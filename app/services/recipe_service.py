"""
Recipe Service for managing bakery recipe operations.

This service handles recipe creation, component addition, cost calculation,
and formatting with proper tenant isolation and validation.
"""

from typing import Optional, Dict, List
from uuid import UUID
from decimal import Decimal
from dataclasses import dataclass
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from app.models import Recipe, RecipeComponent, InventoryItem


@dataclass
class RecipeCost:
    """Data class for recipe cost breakdown."""
    ingredient_cost: Decimal
    packaging_cost: Decimal
    unit_cost: Decimal
    recipe_name: str
    yield_per_batch: int


class RecipeService:
    """
    Service for managing recipe operations.
    
    Handles recipe creation, component addition, cost calculation, and
    formatting with tenant isolation and validation.
    """
    
    def __init__(self, db: Session):
        """
        Initialize RecipeService with database session.
        
        Args:
            db: SQLAlchemy database session
        """
        self.db = db
    
    def create_recipe(
        self,
        tenant_id: UUID,
        name: str,
        yield_per_batch: int
    ) -> Recipe:
        """
        Create a new recipe.
        
        Validates yield_per_batch is positive and creates the recipe record.
        
        Args:
            tenant_id: UUID of the tenant
            name: Recipe name
            yield_per_batch: Number of units produced per batch
        
        Returns:
            Recipe: The newly created recipe
        
        Raises:
            ValueError: If validation fails
        
        Requirements:
            - 9.1: Extract recipe name and yield per batch
            - 9.2: Create Recipe record with all required fields
            - 9.3: Confirm recipe was created
        """
        # Validate required fields
        if not name or not name.strip():
            raise ValueError("Recipe name is required")
        
        if yield_per_batch is None:
            raise ValueError("Yield per batch is required")
        
        # Clean inputs
        name = name.strip()
        
        # Validate yield_per_batch is positive integer
        try:
            yield_per_batch = int(yield_per_batch)
            if yield_per_batch <= 0:
                raise ValueError("Yield per batch must be a positive integer")
        except (ValueError, TypeError) as e:
            if "positive" in str(e):
                raise
            raise ValueError(f"Invalid yield per batch value: {yield_per_batch}")
        
        # Check for duplicate name
        existing_recipe = self.db.query(Recipe).filter(
            Recipe.tenant_id == tenant_id,
            Recipe.name == name
        ).first()
        
        if existing_recipe:
            raise ValueError(
                f"A recipe with name '{name}' already exists"
            )
        
        # Create recipe
        try:
            recipe = Recipe(
                tenant_id=tenant_id,
                name=name,
                yield_per_batch=yield_per_batch
            )
            self.db.add(recipe)
            self.db.commit()
            self.db.refresh(recipe)
            return recipe
        except IntegrityError as e:
            self.db.rollback()
            # Handle race condition
            existing = self.db.query(Recipe).filter(
                Recipe.tenant_id == tenant_id,
                Recipe.name == name
            ).first()
            if existing:
                raise ValueError(
                    f"A recipe with name '{name}' already exists"
                )
            raise
    
    def add_component(
        self,
        tenant_id: UUID,
        recipe_name: str,
        item_name: str,
        quantity: Decimal,
        component_type: str
    ) -> RecipeComponent:
        """
        Add a component (ingredient or packaging) to a recipe.
        
        Validates that the inventory item exists, component type is valid,
        and quantity is positive.
        
        Args:
            tenant_id: UUID of the tenant
            recipe_name: Name of the recipe
            item_name: Name of the inventory item
            quantity: Quantity of the item used
            component_type: Type of component ("ingredient" or "packaging")
        
        Returns:
            RecipeComponent: The newly created recipe component
        
        Raises:
            ValueError: If validation fails
        
        Requirements:
            - 10.1: Extract recipe name, item name, quantity, and component type
            - 10.2: Validate Inventory_Item exists for Tenant_ID
            - 10.3: Validate component type is "ingredient" or "packaging"
            - 10.4: Create Recipe_Component record
            - 10.5: Confirm component was added
        """
        # Validate required fields
        if not recipe_name or not recipe_name.strip():
            raise ValueError("Recipe name is required")
        
        if not item_name or not item_name.strip():
            raise ValueError("Item name is required")
        
        if not component_type or not component_type.strip():
            raise ValueError("Component type is required")
        
        if quantity is None:
            raise ValueError("Quantity is required")
        
        # Clean inputs
        recipe_name = recipe_name.strip()
        item_name = item_name.strip()
        component_type = component_type.strip().lower()
        
        # Validate component type
        if component_type not in {"ingredient", "packaging"}:
            raise ValueError(
                f"Invalid component type '{component_type}'. Must be 'ingredient' or 'packaging'"
            )
        
        # Validate quantity is positive
        try:
            quantity = Decimal(str(quantity))
            if quantity <= 0:
                raise ValueError("Quantity must be positive")
        except (ValueError, TypeError) as e:
            if "positive" in str(e):
                raise
            raise ValueError(f"Invalid quantity value: {quantity}")
        
        # Retrieve recipe
        recipe = self.db.query(Recipe).filter(
            Recipe.tenant_id == tenant_id,
            Recipe.name == recipe_name
        ).first()
        
        if not recipe:
            raise ValueError(f"Recipe '{recipe_name}' not found")
        
        # Validate inventory item exists
        item = self.db.query(InventoryItem).filter(
            InventoryItem.tenant_id == tenant_id,
            InventoryItem.name == item_name
        ).first()
        
        if not item:
            raise ValueError(f"Inventory item '{item_name}' not found")
        
        # Create recipe component
        component = RecipeComponent(
            recipe_id=recipe.recipe_id,
            item_id=item.item_id,
            quantity=quantity,
            type=component_type
        )
        self.db.add(component)
        self.db.commit()
        self.db.refresh(component)
        
        return component
    
    def calculate_cost(
        self,
        tenant_id: UUID,
        recipe_name: str
    ) -> RecipeCost:
        """
        Calculate the cost breakdown for a recipe.
        
        Calculates ingredient cost, packaging cost, and unit cost based on
        recipe components and inventory item costs.
        
        Args:
            tenant_id: UUID of the tenant
            recipe_name: Name of the recipe
        
        Returns:
            RecipeCost: Cost breakdown with ingredient, packaging, and unit costs
        
        Raises:
            ValueError: If recipe not found
        
        Requirements:
            - 11.1: Extract recipe name
            - 11.2: Retrieve Recipe_Components filtered by Tenant_ID
            - 11.3: Calculate ingredient_cost
            - 11.4: Calculate packaging_cost
            - 11.5: Calculate unit_cost
            - 11.6: Display all three costs
            - 11.7: Do NOT use LLM for calculations
        """
        if not recipe_name or not recipe_name.strip():
            raise ValueError("Recipe name is required")
        
        recipe_name = recipe_name.strip()
        
        # Retrieve recipe
        recipe = self.db.query(Recipe).filter(
            Recipe.tenant_id == tenant_id,
            Recipe.name == recipe_name
        ).first()
        
        if not recipe:
            raise ValueError(f"Recipe '{recipe_name}' not found")
        
        # Retrieve all recipe components with inventory items
        components = self.db.query(RecipeComponent, InventoryItem).join(
            InventoryItem,
            RecipeComponent.item_id == InventoryItem.item_id
        ).filter(
            RecipeComponent.recipe_id == recipe.recipe_id,
            InventoryItem.tenant_id == tenant_id
        ).all()
        
        # Calculate costs
        ingredient_cost = Decimal("0")
        packaging_cost = Decimal("0")
        
        for component, item in components:
            cost = component.quantity * item.cost_per_unit
            
            if component.type == "ingredient":
                ingredient_cost += cost
            elif component.type == "packaging":
                packaging_cost += cost
        
        # Calculate unit cost
        total_cost = ingredient_cost + packaging_cost
        unit_cost = total_cost / Decimal(str(recipe.yield_per_batch))
        
        return RecipeCost(
            ingredient_cost=ingredient_cost,
            packaging_cost=packaging_cost,
            unit_cost=unit_cost,
            recipe_name=recipe.name,
            yield_per_batch=recipe.yield_per_batch
        )
    
    def format_recipe(
        self,
        tenant_id: UUID,
        recipe_name: str
    ) -> str:
        """
        Format a recipe for display with all details.
        
        Includes recipe name, yield, ingredients, packaging, and cost breakdown.
        
        Args:
            tenant_id: UUID of the tenant
            recipe_name: Name of the recipe
        
        Returns:
            str: Formatted recipe string
        
        Raises:
            ValueError: If recipe not found
        
        Requirements:
            - 24.1: Format recipe with name and yield
            - 24.2: Include ingredients list
            - 24.3: Include packaging list
            - 24.4: Include cost breakdown
        """
        if not recipe_name or not recipe_name.strip():
            raise ValueError("Recipe name is required")
        
        recipe_name = recipe_name.strip()
        
        # Retrieve recipe
        recipe = self.db.query(Recipe).filter(
            Recipe.tenant_id == tenant_id,
            Recipe.name == recipe_name
        ).first()
        
        if not recipe:
            raise ValueError(f"Recipe '{recipe_name}' not found")
        
        # Retrieve components with inventory items
        components = self.db.query(RecipeComponent, InventoryItem).join(
            InventoryItem,
            RecipeComponent.item_id == InventoryItem.item_id
        ).filter(
            RecipeComponent.recipe_id == recipe.recipe_id,
            InventoryItem.tenant_id == tenant_id
        ).all()
        
        # Group by type
        ingredients = []
        packaging = []
        
        for component, item in components:
            item_str = f"  - {item.name}: {component.quantity} {item.unit}"
            if component.type == "ingredient":
                ingredients.append(item_str)
            elif component.type == "packaging":
                packaging.append(item_str)
        
        # Calculate costs
        cost = self.calculate_cost(tenant_id, recipe_name)
        
        # Format output
        lines = [
            f"Recipe: {recipe.name}",
            f"Yield: {recipe.yield_per_batch} units per batch",
            "",
            "Ingredients:"
        ]
        
        if ingredients:
            lines.extend(ingredients)
        else:
            lines.append("  (none)")
        
        lines.append("")
        lines.append("Packaging:")
        
        if packaging:
            lines.extend(packaging)
        else:
            lines.append("  (none)")
        
        lines.extend([
            "",
            "Cost Breakdown:",
            f"  Ingredient Cost: ${cost.ingredient_cost:.2f}",
            f"  Packaging Cost: ${cost.packaging_cost:.2f}",
            f"  Total Cost: ${cost.ingredient_cost + cost.packaging_cost:.2f}",
            f"  Cost per Unit: ${cost.unit_cost:.2f}"
        ])
        
        return "\n".join(lines)
    
    def get_recipe(
        self,
        tenant_id: UUID,
        recipe_name: str
    ) -> Optional[Recipe]:
        """
        Retrieve a recipe by name.
        
        Args:
            tenant_id: UUID of the tenant
            recipe_name: Name of the recipe
        
        Returns:
            Optional[Recipe]: The recipe if found, None otherwise
        """
        if not recipe_name or not recipe_name.strip():
            return None
        
        return self.db.query(Recipe).filter(
            Recipe.tenant_id == tenant_id,
            Recipe.name == recipe_name.strip()
        ).first()
    
    def get_recipe_by_id(
        self,
        tenant_id: UUID,
        recipe_id: UUID
    ) -> Optional[Recipe]:
        """
        Retrieve a recipe by ID with tenant isolation.
        
        Args:
            tenant_id: UUID of the tenant
            recipe_id: UUID of the recipe
        
        Returns:
            Optional[Recipe]: The recipe if found, None otherwise
        """
        return self.db.query(Recipe).filter(
            Recipe.tenant_id == tenant_id,
            Recipe.recipe_id == recipe_id
        ).first()

    def search_recipes(
        self,
        tenant_id: UUID,
        search_term: str
    ) -> List[Recipe]:
        """
        Search for recipes by name with fuzzy matching.
        
        Performs case-insensitive partial matching on recipe names.
        
        Args:
            tenant_id: UUID of the tenant
            search_term: Search term to match against recipe names
        
        Returns:
            List[Recipe]: List of matching recipes
        """
        if not search_term or not search_term.strip():
            return []
        
        search_term = search_term.strip().lower()
        
        # Get all recipes for tenant
        all_recipes = self.db.query(Recipe).filter(
            Recipe.tenant_id == tenant_id
        ).all()
        
        # Filter recipes that contain the search term (case-insensitive)
        matching_recipes = [
            recipe for recipe in all_recipes
            if search_term in recipe.name.lower()
        ]
        
        return matching_recipes
