"""
Request handlers for business operations.

These handlers are platform-agnostic and contain the business logic
for processing user requests. They can be used with Telegram, WhatsApp,
or any other messaging platform.
"""

from app.handlers.request_handler import RequestHandler

__all__ = ['RequestHandler']
