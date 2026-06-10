"""
Product Catalog Service.

Manages the owner's product catalog — the finished goods they sell.
Each product can have multiple size/weight variants with different prices
(e.g. "250 gms = ₹400, 500 gms = ₹800").

Products are optionally linked to Recipes for cost/margin calculation.

Fuzzy matching is used when suggesting recipe↔product links:
- When a recipe is created, check for products with similar names
- When a product is created, check for recipes with similar names
- Always returns suggestions — never auto-links
"""

import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import Dict, List, Optional
from uuid import UUID

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import Product, ProductVariant, Recipe

logger = logging.getLogger(__name__)


@dataclass
class VariantInput:
    size_label: str
    price: Decimal


class ProductService:
    """CRUD and fuzzy-match logic for the product catalog."""

    def __init__(self, db: Session):
        self.db = db

    # ── CRUD ───────────────────────────────────────────────────────────────

    def create_product(
        self,
        tenant_id: UUID,
        name: str,
        variants: List[VariantInput],
        description: Optional[str] = None,
        category: Optional[str] = None,
        image_url: Optional[str] = None,
        recipe_id: Optional[UUID] = None,
    ) -> Product:
        """
        Create a product with one or more size/price variants.
        Raises ValueError on duplicate name or missing variants.
        """
        name = name.strip()
        if not name:
            raise ValueError("Product name is required")
        if not variants:
            raise ValueError("At least one size/price variant is required")

        existing = self._get_by_name(tenant_id, name)
        if existing:
            raise ValueError(f"A product named '{existing.name}' already exists")

        try:
            product = Product(
                tenant_id=tenant_id,
                name=name,
                description=description,
                category=category.strip() if category else None,
                image_url=image_url,
                recipe_id=recipe_id,
            )
            self.db.add(product)
            self.db.flush()  # get product_id before adding variants

            for v in variants:
                self.db.add(ProductVariant(
                    product_id=product.product_id,
                    size_label=v.size_label.strip(),
                    price=Decimal(str(v.price)),
                ))

            self.db.commit()
            self.db.refresh(product)
            return product
        except IntegrityError:
            self.db.rollback()
            raise ValueError(f"A product named '{name}' already exists")

    def get_product(self, tenant_id: UUID, name: str) -> Optional[Product]:
        """Exact (case-insensitive) lookup by name."""
        return self._get_by_name(tenant_id, name)

    def list_products(self, tenant_id: UUID) -> List[Product]:
        """Return all products ordered by category then name."""
        return (
            self.db.query(Product)
            .filter(Product.tenant_id == tenant_id)
            .order_by(Product.category, Product.name)
            .all()
        )

    def add_variant(
        self, tenant_id: UUID, product_name: str, size_label: str, price: Decimal
    ) -> ProductVariant:
        """Add a new size/price variant to an existing product."""
        product = self._get_by_name(tenant_id, product_name)
        if not product:
            raise ValueError(f"Product '{product_name}' not found")

        existing = next(
            (v for v in product.variants if v.size_label.lower() == size_label.strip().lower()),
            None,
        )
        if existing:
            raise ValueError(
                f"Variant '{size_label}' already exists for '{product_name}' at ₹{existing.price}"
            )

        variant = ProductVariant(
            product_id=product.product_id,
            size_label=size_label.strip(),
            price=Decimal(str(price)),
        )
        self.db.add(variant)
        self.db.commit()
        self.db.refresh(variant)
        return variant

    def update_variant_price(
        self, tenant_id: UUID, product_name: str, size_label: str, new_price: Decimal
    ) -> ProductVariant:
        """Update the price of a specific variant."""
        product = self._get_by_name(tenant_id, product_name)
        if not product:
            raise ValueError(f"Product '{product_name}' not found")

        variant = next(
            (v for v in product.variants if v.size_label.lower() == size_label.strip().lower()),
            None,
        )
        if not variant:
            raise ValueError(f"Variant '{size_label}' not found for '{product_name}'")

        variant.price = Decimal(str(new_price))
        self.db.commit()
        self.db.refresh(variant)
        return variant

    def update_product(
        self,
        tenant_id: UUID,
        name: str,
        new_name: Optional[str] = None,
        description: Optional[str] = None,
        category: Optional[str] = None,
        image_url: Optional[str] = None,
        recipe_id: Optional[UUID] = None,
        unlink_recipe: bool = False,
    ) -> Product:
        """Update product metadata fields."""
        product = self._get_by_name(tenant_id, name)
        if not product:
            raise ValueError(f"Product '{name}' not found")

        if new_name is not None:
            new_name = new_name.strip()
            if not new_name:
                raise ValueError("Product name cannot be empty")
            clash = self._get_by_name(tenant_id, new_name)
            if clash and clash.product_id != product.product_id:
                raise ValueError(f"A product named '{new_name}' already exists")
            product.name = new_name

        if description is not None:
            product.description = description
        if category is not None:
            product.category = category.strip()
        if image_url is not None:
            product.image_url = image_url
        if unlink_recipe:
            product.recipe_id = None
        elif recipe_id is not None:
            product.recipe_id = recipe_id

        self.db.commit()
        self.db.refresh(product)
        return product

    def link_recipe(self, tenant_id: UUID, product_name: str, recipe_id: UUID) -> Product:
        """Link a recipe to a product."""
        return self.update_product(tenant_id, product_name, recipe_id=recipe_id)

    def delete_product(self, tenant_id: UUID, name: str) -> bool:
        """Delete a product and all its variants.

        Also removes any booth_session_items referencing the product's variants,
        since those are soft references (the booth sale is already recorded on the
        order) and should not block catalog cleanup.
        """
        product = self._get_by_name(tenant_id, name)
        if not product:
            raise ValueError(f"Product '{name}' not found")

        # Remove booth session items that reference any of this product's variants
        # before deleting the variants themselves to avoid FK constraint errors.
        from app.models import BoothSessionItem
        variant_ids = [v.variant_id for v in product.variants]
        if variant_ids:
            self.db.query(BoothSessionItem).filter(
                BoothSessionItem.variant_id.in_(variant_ids)
            ).delete(synchronize_session=False)

        self.db.delete(product)
        self.db.commit()
        return True

    def format_product(self, product: Product) -> str:
        """Format a product with all variants for display."""
        lines = [f"*{product.name}*"]
        if product.category:
            lines[0] += f" — {product.category}"
        if product.description:
            lines.append(product.description)
        for v in sorted(product.variants, key=lambda x: x.price):
            lines.append(f"  {v.size_label}: ₹{v.price:.0f}")
        if product.recipe_id:
            recipe = self.db.query(Recipe).filter(
                Recipe.recipe_id == product.recipe_id
            ).first()
            if recipe:
                lines.append(f"  _Recipe: {recipe.name}_")
        return "\n".join(lines)

    def format_catalog(self, tenant_id: UUID) -> str:
        """Format the full product catalog grouped by category."""
        products = self.list_products(tenant_id)
        if not products:
            return "No products in catalog yet."

        # Group by category
        by_category: Dict[str, List[Product]] = {}
        for p in products:
            cat = p.category or "Other"
            by_category.setdefault(cat, []).append(p)

        lines = []
        for cat, items in sorted(by_category.items()):
            lines.append(f"\n*{cat}*")
            for p in items:
                variant_str = "  |  ".join(
                    f"{v.size_label} ₹{v.price:.0f}"
                    for v in sorted(p.variants, key=lambda x: x.price)
                )
                lines.append(f"• {p.name} — {variant_str}")

        return "\n".join(lines).strip()

    # ── Fuzzy matching for link suggestions ───────────────────────────────

    def find_matching_products_for_recipe(
        self, tenant_id: UUID, recipe_name: str
    ) -> List[Product]:
        """
        Find unlinked products whose name fuzzy-matches a recipe name.
        Called after recipe creation to suggest linking.
        """
        return self._fuzzy_products(tenant_id, recipe_name, unlinked_only=True)

    def find_matching_recipes_for_product(
        self, tenant_id: UUID, product_name: str
    ) -> List[Recipe]:
        """
        Find unlinked recipes whose name fuzzy-matches a product name.
        Called after product creation to suggest linking.
        """
        return self._fuzzy_recipes(tenant_id, product_name)

    # ── Internal ───────────────────────────────────────────────────────────

    def _get_by_name(self, tenant_id: UUID, name: str) -> Optional[Product]:
        return (
            self.db.query(Product)
            .filter(
                Product.tenant_id == tenant_id,
                func.lower(Product.name) == name.strip().lower(),
            )
            .first()
        )

    def _fuzzy_products(
        self, tenant_id: UUID, search: str, unlinked_only: bool = False
    ) -> List[Product]:
        """Products whose name shares a meaningful word with `search`."""
        words = [w.lower() for w in search.split() if len(w) >= 4]
        if not words:
            return []
        query = self.db.query(Product).filter(Product.tenant_id == tenant_id)
        if unlinked_only:
            query = query.filter(Product.recipe_id.is_(None))
        return [p for p in query.all() if any(w in p.name.lower() for w in words)]

    def _fuzzy_recipes(self, tenant_id: UUID, search: str) -> List[Recipe]:
        """Recipes not yet linked to any product whose name shares a word with `search`."""
        words = [w.lower() for w in search.split() if len(w) >= 4]
        if not words:
            return []

        linked_ids = {
            p.recipe_id
            for p in self.db.query(Product)
            .filter(Product.tenant_id == tenant_id, Product.recipe_id.isnot(None))
            .all()
        }
        return [
            r for r in self.db.query(Recipe).filter(Recipe.tenant_id == tenant_id).all()
            if r.recipe_id not in linked_ids and any(w in r.name.lower() for w in words)
        ]
