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
            description="payment information from this receipt",
            fields=[
                "Amount (number only, no currency symbols)",
                "Payment method (must be one of: Cash, Paytm, Bank Transfer, or Unknown)",
                "Date (in YYYY-MM-DD format if found)",
                "Customer name (if mentioned)"
            ],
            json_schema="""{
    "amount": <number or null>,
    "method": "<Cash|Paytm|Bank Transfer|Unknown>",
    "date": "<YYYY-MM-DD or null>",
    "customer_name": "<name or null>",
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
            'customer_name': None, 'confidence': 0.0, 'raw_text': ''
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

    async def process_receipt_image(self, image_bytes: bytes) -> Dict[str, Any]:
        """Process a receipt/payment image and extract payment details."""
        return await self._process_image(image_bytes, ImageType.RECEIPT)

    async def process_recipe_image(self, image_bytes: bytes) -> Dict[str, Any]:
        """Process a recipe image and extract recipe details."""
        return await self._process_image(image_bytes, ImageType.RECIPE)

    async def process_order_image(self, image_bytes: bytes) -> Dict[str, Any]:
        """Process an order image (WhatsApp screenshot, SMS, etc.) and extract order details."""
        return await self._process_image(image_bytes, ImageType.ORDER)
