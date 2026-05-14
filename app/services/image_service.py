"""
Image Processing Service using GPT-4o Vision.

Sends images directly to GPT-4o which handles handwriting,
printed text, screenshots, and receipts natively - no OCR needed.
"""

import logging
import base64
import json
from typing import Dict, Any, List
from io import BytesIO
from enum import Enum
from dataclasses import dataclass
from PIL import Image

logger = logging.getLogger(__name__)


class ImageType(Enum):
    """Enum for different types of images to process."""
    RECEIPT = "receipt"
    RECIPE = "recipe"
    ORDER = "order"


@dataclass
class ExtractionPrompt:
    """Configuration for LLM extraction prompts."""
    description: str
    fields: List[str]
    json_schema: str


class ImageService:
    """
    Service for processing images and extracting structured data.

    Sends images directly to GPT-4o Vision which handles:
    - Handwritten text (notebooks, handwritten recipes)
    - Printed receipts and bills
    - WhatsApp/SMS screenshots

    Follows DRY principle with a single extraction pipeline for all image types.
    """

    EXTRACTION_PROMPTS = {
        ImageType.RECEIPT: ExtractionPrompt(
            description="receipt information",
            fields=[
                "Total amount (number only, no currency symbols)",
                "Payment method (must be one of: Cash, Paytm, Bank Transfer, Card, UPI, or Unknown)",
                "Date (in YYYY-MM-DD format if found)",
                "Name on receipt (customer name or shop/vendor name)",
                "Line items if visible (item name, quantity, unit, price)"
            ],
            json_schema="""{
    "amount": <number or null>,
    "method": "<Cash|Paytm|Bank Transfer|Card|UPI|Unknown>",
    "date": "<YYYY-MM-DD or null>",
    "customer_name": "<name or null>",
    "items": [
        {"name": "<item name>", "quantity": <number or null>, "unit": "<unit or null>", "price": <number or null>}
    ],
    "confidence": <0.0 to 1.0>,
    "raw_text": "<all text you can read from the image>"
}"""
        ),
        ImageType.RECIPE: ExtractionPrompt(
            description="recipe information",
            fields=[
                "Recipe name",
                "Yield per batch (number of items produced, if mentioned)",
                "Ingredients with quantities and units",
                "Packaging materials with quantities and units (if any)"
            ],
            json_schema="""{
    "name": "<recipe name or null>",
    "yield_per_batch": <number or null>,
    "ingredients": [
        {"item_name": "<name>", "quantity": <number>, "unit": "<kg|g|litre|ml|pcs>"}
    ],
    "packaging": [
        {"item_name": "<name>", "quantity": <number>, "unit": "<kg|g|litre|ml|pcs>"}
    ],
    "confidence": <0.0 to 1.0>,
    "raw_text": "<all text you can read from the image>"
}"""
        ),
        ImageType.ORDER: ExtractionPrompt(
            description="order information from this text (could be WhatsApp message, SMS, etc.)",
            fields=[
                "Customer name",
                "Customer phone number",
                "Delivery date (in YYYY-MM-DD format)",
                "Order items with quantities and prices"
            ],
            json_schema="""{
    "customer_name": "<name or null>",
    "customer_phone": "<phone or null>",
    "delivery_date": "<YYYY-MM-DD or null>",
    "items": [
        {"recipe_name": "<item name>", "quantity": <number>, "selling_price": <number>}
    ],
    "confidence": <0.0 to 1.0>,
    "raw_text": "<all text you can read from the image>"
}"""
        )
    }

    DEFAULT_RESULTS = {
        ImageType.RECEIPT: {
            'amount': None, 'method': None, 'date': None,
            'customer_name': None, 'items': [], 'confidence': 0.0, 'raw_text': ''
        },
        ImageType.RECIPE: {
            'name': None, 'yield_per_batch': None,
            'ingredients': [], 'packaging': [], 'confidence': 0.0, 'raw_text': ''
        },
        ImageType.ORDER: {
            'customer_name': None, 'customer_phone': None,
            'delivery_date': None, 'items': [], 'confidence': 0.0, 'raw_text': ''
        }
    }

    def __init__(self, llm_service):
        self.llm_service = llm_service

    def _encode_image(self, image_bytes: bytes) -> str:
        """
        Encode image to base64 JPEG for GPT-4o Vision API.
        Resizes large images to reduce cost while keeping quality.
        """
        image = Image.open(BytesIO(image_bytes))

        if image.mode not in ('RGB',):
            image = image.convert('RGB')

        # Resize if too large - GPT-4o handles up to 2048px well
        max_dimension = 1568
        width, height = image.size
        if width > max_dimension or height > max_dimension:
            scale = min(max_dimension / width, max_dimension / height)
            new_size = (int(width * scale), int(height * scale))
            image = image.resize(new_size, Image.LANCZOS)
            logger.info(f"Resized image from {width}x{height} to {new_size[0]}x{new_size[1]}")

        buffer = BytesIO()
        image.save(buffer, format='JPEG', quality=90)
        buffer.seek(0)
        return base64.b64encode(buffer.read()).decode('utf-8')

    def _build_extraction_prompt(self, image_type: ImageType) -> str:
        """Build extraction prompt for the given image type (DRY)."""
        config = self.EXTRACTION_PROMPTS[image_type]
        fields_text = "\n".join(
            [f"{i+1}. {field}" for i, field in enumerate(config.fields)]
        )
        return f"""Extract {config.description} from this image.

Extract the following information:
{fields_text}

Return ONLY a JSON object with these exact keys:
{config.json_schema}

If you cannot find a field, use null or empty array.
Set confidence based on how clearly you can read the information (1.0 = very clear, 0.5 = somewhat readable).
Include ALL text visible in the image in the raw_text field."""

    async def _process_image(self, image_bytes: bytes, image_type: ImageType) -> Dict[str, Any]:
        """
        Generic image processing pipeline using GPT-4o Vision (DRY).
        Sends image directly - no OCR preprocessing needed.
        """
        try:
            image_b64 = self._encode_image(image_bytes)
        except Exception as e:
            logger.error(f"Failed to encode image: {e}", exc_info=True)
            result = self.DEFAULT_RESULTS[image_type].copy()
            result['error'] = f"Failed to read image file: {str(e)}"
            return result

        prompt = self._build_extraction_prompt(image_type)

        try:
            result = await self.llm_service.extract_structured_data_from_image(prompt, image_b64)
            return result
        except Exception as e:
            logger.error(f"Error processing {image_type.value} image: {e}", exc_info=True)
            result = self.DEFAULT_RESULTS[image_type].copy()
            result['error'] = str(e)
            return result

    async def classify_image(self, image_bytes: bytes, caption: str = "") -> dict:
        """
        Use GPT-4o to look at the image and determine what it is.

        Returns a dict with:
          - type: "recipe" | "receipt_customer" | "receipt_purchase" |
                  "order" | "catalog" | "unknown"
          - confidence: 0.0–1.0
          - summary: one-sentence description of what was found
          - hint: suggested question to ask the owner for confirmation
        """
        image_b64 = self._encode_image(image_bytes)
        caption_hint = f'The owner captioned it: "{caption}"' if caption else "No caption provided."

        prompt = f"""Look at this image carefully. {caption_hint}

Classify it into exactly one of these types:
- recipe: A handwritten or printed recipe with ingredients and quantities
- receipt_customer: A payment receipt showing money received FROM a customer (for an order/sale)
- receipt_purchase: A purchase receipt/bill showing money SPENT by the owner (buying ingredients, supplies, packaging)
- order: A WhatsApp/SMS/chat screenshot showing a customer placing an order
- catalog: A product menu or price list showing items for sale with prices
- unknown: Cannot determine

Return ONLY this JSON:
{{
  "type": "<one of the types above>",
  "confidence": <0.0 to 1.0>,
  "summary": "<one sentence: what you see in the image>",
  "hint": "<short question to confirm with owner, e.g. 'This looks like an ingredient purchase receipt for ₹2364. Should I update your inventory?'>"
}}"""

        try:
            result = await self.llm_service.extract_structured_data_from_image(prompt, image_b64)
            return result
        except Exception as e:
            logger.error(f"Image classification failed: {e}")
            return {
                "type": "unknown",
                "confidence": 0.0,
                "summary": "Could not analyse the image",
                "hint": "What type of image is this? (recipe / receipt / order / catalog)"
            }
        """Process a receipt/payment image and extract payment details."""
        return await self._process_image(image_bytes, ImageType.RECEIPT)

    async def process_recipe_image(self, image_bytes: bytes) -> Dict[str, Any]:
        """Process a recipe image and extract recipe details."""
        return await self._process_image(image_bytes, ImageType.RECIPE)

    async def process_order_image(self, image_bytes: bytes) -> Dict[str, Any]:
        """Process an order image (WhatsApp screenshot, SMS, etc.) and extract order details."""
        return await self._process_image(image_bytes, ImageType.ORDER)

    async def process_catalog_image(self, image_bytes: bytes) -> Dict[str, Any]:
        """
        Extract the full product catalog from a menu/price-list image.

        Returns a structured dict with categories and products, each product
        having one or more size/price variants.

        Example output:
        {
          "categories": [
            {
              "name": "Gourmet Cookies",
              "products": [
                {
                  "name": "Oatmeal Raisin",
                  "variants": [
                    {"size_label": "250 gms", "price": 400},
                    {"size_label": "500 gms", "price": 800}
                  ]
                }
              ]
            }
          ]
        }
        """
        image_b64 = self._encode_image(image_bytes)
        prompt = """Extract the complete product catalog from this menu/price list image.

For each category and product, extract:
- Category name (e.g. "Gourmet Cookies", "Gourmet Brownies", "Desserts", "Small Bakes")
- Product name
- All size/weight variants with their prices (e.g. "250 gms = 400", "500 gms = 800")
- If a product has only one price with no size label, use "standard" as the size_label

Return ONLY a JSON object:
{
  "categories": [
    {
      "name": "<category name>",
      "products": [
        {
          "name": "<product name>",
          "variants": [
            {"size_label": "<size or weight or pack>", "price": <number>}
          ]
        }
      ]
    }
  ],
  "confidence": <0.0 to 1.0>,
  "raw_text": "<all text visible in the image>"
}

Rules:
- Extract ALL products visible, do not skip any
- Prices are numbers only (no currency symbols)
- size_label examples: "250 gms", "500 gms", "½ kg", "1 kg", "per piece", "Pack of 6", "Pack of 3", "standard"
- If the image shows column headers like "250 GMS  500 GMS", those are the size labels for all products in that section
- Preserve exact product names as shown"""

        try:
            result = await self.llm_service.extract_structured_data_from_image(prompt, image_b64)
            return result
        except Exception as e:
            logger.error(f"Error processing catalog image: {e}", exc_info=True)
            return {"error": str(e), "categories": []}
