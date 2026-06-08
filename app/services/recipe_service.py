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
from sqlalchemy import func

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
        
        # Check for duplicate name (case-insensitive)
        existing_recipe = self.db.query(Recipe).filter(
            Recipe.tenant_id == tenant_id,
            func.lower(Recipe.name) == name.lower()
        ).first()
        
        if existing_recipe:
            raise ValueError(
                f"A recipe with name '{existing_recipe.name}' already exists"
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
                func.lower(Recipe.name) == name.lower()
            ).first()
            if existing:
                raise ValueError(
                    f"A recipe with name '{existing.name}' already exists"
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
        
        # Retrieve recipe (case-insensitive)
        recipe = self.db.query(Recipe).filter(
            Recipe.tenant_id == tenant_id,
            func.lower(Recipe.name) == recipe_name.lower()
        ).first()
        
        if not recipe:
            raise ValueError(f"Recipe '{recipe_name}' not found")
        
        # Validate inventory item exists (case-insensitive)
        item = self.db.query(InventoryItem).filter(
            InventoryItem.tenant_id == tenant_id,
            func.lower(InventoryItem.name) == item_name.lower()
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
        
        # Retrieve recipe (case-insensitive)
        recipe = self.db.query(Recipe).filter(
            Recipe.tenant_id == tenant_id,
            func.lower(Recipe.name) == recipe_name.lower()
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
            func.lower(Recipe.name) == recipe_name.strip().lower()
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

    def list_recipes(self, tenant_id: UUID) -> List[Recipe]:
        """
        List all recipes for a tenant.

        Args:
            tenant_id: UUID of the tenant

        Returns:
            List[Recipe]: All recipes for the tenant
        """
        return self.db.query(Recipe).filter(
            Recipe.tenant_id == tenant_id
        ).order_by(Recipe.name).all()

    def get_recipe_with_components(
        self,
        tenant_id: UUID,
        recipe_name: str
    ) -> Optional[Dict]:
        """
        Get a recipe with all its components as a dict.

        Args:
            tenant_id: UUID of the tenant
            recipe_name: Name of the recipe

        Returns:
            Dict with recipe and components, or None if not found
        """
        recipe = self.get_recipe(tenant_id, recipe_name)
        if not recipe:
            return None

        components = self.db.query(RecipeComponent, InventoryItem).join(
            InventoryItem,
            RecipeComponent.item_id == InventoryItem.item_id
        ).filter(
            RecipeComponent.recipe_id == recipe.recipe_id,
            InventoryItem.tenant_id == tenant_id
        ).all()

        ingredients = []
        packaging = []
        for component, item in components:
            entry = {
                'component_id': component.component_id,
                'item_name': item.name,
                'quantity': component.quantity,
                'unit': item.unit,
                'type': component.type
            }
            if component.type == 'ingredient':
                ingredients.append(entry)
            else:
                packaging.append(entry)

        return {
            'recipe_id': recipe.recipe_id,
            'name': recipe.name,
            'yield_per_batch': recipe.yield_per_batch,
            'ingredients': ingredients,
            'packaging': packaging
        }

    def update_recipe(
        self,
        tenant_id: UUID,
        recipe_name: str,
        new_name: Optional[str] = None,
        new_yield: Optional[int] = None
    ) -> Recipe:
        """
        Update recipe name or yield.

        Args:
            tenant_id: UUID of the tenant
            recipe_name: Current recipe name
            new_name: New name (optional)
            new_yield: New yield per batch (optional)

        Returns:
            Recipe: Updated recipe

        Raises:
            ValueError: If recipe not found or validation fails
        """
        recipe = self.get_recipe(tenant_id, recipe_name)
        if not recipe:
            raise ValueError(f"Recipe '{recipe_name}' not found")

        if new_name is not None:
            new_name = new_name.strip()
            if not new_name:
                raise ValueError("Recipe name cannot be empty")
            # Check for duplicate
            existing = self.get_recipe(tenant_id, new_name)
            if existing and existing.recipe_id != recipe.recipe_id:
                raise ValueError(f"A recipe named '{new_name}' already exists")
            recipe.name = new_name

        if new_yield is not None:
            new_yield = int(new_yield)
            if new_yield <= 0:
                raise ValueError("Yield per batch must be a positive integer")
            recipe.yield_per_batch = new_yield

        self.db.commit()
        self.db.refresh(recipe)
        return recipe

    def remove_component(
        self,
        tenant_id: UUID,
        recipe_name: str,
        item_name: str
    ) -> bool:
        """
        Remove a component from a recipe.

        Args:
            tenant_id: UUID of the tenant
            recipe_name: Name of the recipe
            item_name: Name of the inventory item to remove

        Returns:
            bool: True if removed, False if not found

        Raises:
            ValueError: If recipe not found
        """
        recipe = self.get_recipe(tenant_id, recipe_name)
        if not recipe:
            raise ValueError(f"Recipe '{recipe_name}' not found")

        item = self.db.query(InventoryItem).filter(
            InventoryItem.tenant_id == tenant_id,
            func.lower(InventoryItem.name) == item_name.strip().lower()
        ).first()

        if not item:
            raise ValueError(f"Inventory item '{item_name}' not found")

        component = self.db.query(RecipeComponent).filter(
            RecipeComponent.recipe_id == recipe.recipe_id,
            RecipeComponent.item_id == item.item_id
        ).first()

        if not component:
            return False

        self.db.delete(component)
        self.db.commit()
        return True

    def update_component_quantity(
        self,
        tenant_id: UUID,
        recipe_name: str,
        item_name: str,
        new_quantity: Decimal
    ) -> RecipeComponent:
        """
        Update the quantity of a component in a recipe.

        Args:
            tenant_id: UUID of the tenant
            recipe_name: Name of the recipe
            item_name: Name of the inventory item
            new_quantity: New quantity

        Returns:
            RecipeComponent: Updated component

        Raises:
            ValueError: If recipe, item, or component not found
        """
        recipe = self.get_recipe(tenant_id, recipe_name)
        if not recipe:
            raise ValueError(f"Recipe '{recipe_name}' not found")

        item = self.db.query(InventoryItem).filter(
            InventoryItem.tenant_id == tenant_id,
            func.lower(InventoryItem.name) == item_name.strip().lower()
        ).first()

        if not item:
            raise ValueError(f"Inventory item '{item_name}' not found")

        component = self.db.query(RecipeComponent).filter(
            RecipeComponent.recipe_id == recipe.recipe_id,
            RecipeComponent.item_id == item.item_id
        ).first()

        if not component:
            raise ValueError(f"'{item_name}' is not a component of recipe '{recipe_name}'")

        new_quantity = Decimal(str(new_quantity))
        if new_quantity <= 0:
            raise ValueError("Quantity must be positive")

        component.quantity = new_quantity
        self.db.commit()
        self.db.refresh(component)
        return component

    def delete_recipe(self, tenant_id: UUID, recipe_name: str) -> bool:
        """
        Delete a recipe and all its components.

        Args:
            tenant_id: UUID of the tenant
            recipe_name: Name of the recipe

        Returns:
            bool: True if deleted

        Raises:
            ValueError: If recipe not found
        """
        recipe = self.get_recipe(tenant_id, recipe_name)
        if not recipe:
            raise ValueError(f"Recipe '{recipe_name}' not found")

        # Delete components first (FK constraint)
        self.db.query(RecipeComponent).filter(
            RecipeComponent.recipe_id == recipe.recipe_id
        ).delete()

        self.db.delete(recipe)
        self.db.commit()
        return True


