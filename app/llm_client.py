"""
LLM Client for making API calls to OpenAI-compatible LLM services.

This module provides a wrapper around httpx for making HTTP requests to LLM APIs
with built-in retry logic and exponential backoff.
"""

import asyncio
import logging
from typing import Any, Dict, List, Optional

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

# Default timeouts
_CONNECT_TIMEOUT = 10.0   # seconds to establish connection
_READ_TIMEOUT    = 120.0  # seconds to wait for response (vision/catalog calls can be slow)


class LLMClient:
    """
    HTTP client wrapper for LLM API calls with retry logic.
    
    Supports OpenAI-compatible APIs with exponential backoff retry strategy.
    Uses a generous read timeout (120s) to handle slow vision/catalog responses.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        max_retries: int = 2,
        initial_retry_delay: float = 1.0,
        max_retry_delay: float = 5.0,
        timeout: float = None,  # kept for backwards compat, ignored
    ):
        self.api_key = api_key or settings.LLM_API_KEY
        self.base_url = (base_url or settings.LLM_API_BASE_URL).rstrip("/")
        self.model = model or settings.LLM_MODEL
        self.max_retries = max_retries
        self.initial_retry_delay = initial_retry_delay
        self.max_retry_delay = max_retry_delay

        if not self.api_key:
            raise ValueError("LLM_API_KEY must be configured")

        self.client = httpx.AsyncClient(
            timeout=httpx.Timeout(
                connect=_CONNECT_TIMEOUT,
                read=_READ_TIMEOUT,
                write=_CONNECT_TIMEOUT,
                pool=_CONNECT_TIMEOUT,
            ),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
        )

    async def call_llm(
        self,
        messages: List[Dict[str, Any]],
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """
        Make an LLM API call with exponential backoff retry logic.

        Args:
            messages:    List of message dicts with 'role' and 'content'
            temperature: Sampling temperature (0.0 to 2.0)
            max_tokens:  Maximum tokens to generate (optional)
            **kwargs:    Additional parameters passed to the API (tools, tool_choice, etc.)

        Returns:
            Dict containing the API response

        Raises:
            httpx.HTTPError: If all retry attempts fail
        """
        url = f"{self.base_url}/chat/completions"
        
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            **kwargs,
        }
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens

        retry_delay = self.initial_retry_delay
        last_exception = None

        for attempt in range(self.max_retries + 1):
            try:
                response = await self.client.post(url, json=payload)
                response.raise_for_status()
                return response.json()

            except httpx.HTTPStatusError as e:
                last_exception = e
                status_code = e.response.status_code
                # Don't retry on 4xx client errors except rate limits (429)
                if 400 <= status_code < 500 and status_code != 429:
                    logger.error(f"LLM API client error ({status_code}): {e}")
                    raise
                if attempt < self.max_retries:
                    logger.warning(f"LLM API error ({status_code}), retrying in {retry_delay}s: {e}")
                    await asyncio.sleep(retry_delay)
                    retry_delay = min(retry_delay * 2, self.max_retry_delay)
                else:
                    logger.error(f"LLM API failed after {self.max_retries + 1} attempts: {e}")
                    raise

            except (httpx.RequestError, httpx.TimeoutException) as e:
                last_exception = e
                if attempt < self.max_retries:
                    logger.warning(f"LLM API request error, retrying in {retry_delay}s: {type(e).__name__}: {e}")
                    await asyncio.sleep(retry_delay)
                    retry_delay = min(retry_delay * 2, self.max_retry_delay)
                else:
                    logger.error(f"LLM API failed after {self.max_retries + 1} attempts: {type(e).__name__}: {e}")
                    raise

        if last_exception:
            raise last_exception
        raise RuntimeError("LLM API call failed with unknown error")

    async def close(self):
        await self.client.aclose()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()


class LLMClient:
    """
    HTTP client wrapper for LLM API calls with retry logic.
    
    Supports OpenAI-compatible APIs with exponential backoff retry strategy.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        max_retries: int = 3,
        initial_retry_delay: float = 1.0,
        max_retry_delay: float = 10.0,
        timeout: float = 30.0,
    ):
        """
        Initialize the LLM client.

        Args:
            api_key: API key for authentication (defaults to settings.LLM_API_KEY)
            base_url: Base URL for the API (defaults to settings.LLM_API_BASE_URL)
            model: Model name to use (defaults to settings.LLM_MODEL)
            max_retries: Maximum number of retry attempts
            initial_retry_delay: Initial delay in seconds before first retry
            max_retry_delay: Maximum delay in seconds between retries
            timeout: Request timeout in seconds
        """
        self.api_key = api_key or settings.LLM_API_KEY
        self.base_url = (base_url or settings.LLM_API_BASE_URL).rstrip("/")
        self.model = model or settings.LLM_MODEL
        self.max_retries = max_retries
        self.initial_retry_delay = initial_retry_delay
        self.max_retry_delay = max_retry_delay
        self.timeout = timeout

        if not self.api_key:
            raise ValueError("LLM_API_KEY must be configured")

        self.client = httpx.AsyncClient(
            timeout=self.timeout,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
        )

    async def call_llm(
        self,
        messages: list[Dict[str, str]],
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """
        Make an LLM API call with exponential backoff retry logic.

        Args:
            messages: List of message dictionaries with 'role' and 'content' keys
            temperature: Sampling temperature (0.0 to 2.0)
            max_tokens: Maximum tokens to generate (optional)
            **kwargs: Additional parameters to pass to the API

        Returns:
            Dict containing the API response

        Raises:
            httpx.HTTPError: If all retry attempts fail
            ValueError: If the response format is invalid
        """
        url = f"{self.base_url}/chat/completions"
        
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            **kwargs,
        }
        
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens

        retry_delay = self.initial_retry_delay
        last_exception = None

        for attempt in range(self.max_retries + 1):
            try:
                logger.debug(
                    f"LLM API call attempt {attempt + 1}/{self.max_retries + 1}"
                )
                
                response = await self.client.post(url, json=payload)
                response.raise_for_status()
                
                result = response.json()
                logger.info(f"LLM API call succeeded on attempt {attempt + 1}")
                return result

            except httpx.HTTPStatusError as e:
                last_exception = e
                status_code = e.response.status_code
                
                # Don't retry on client errors (4xx) except rate limits
                if 400 <= status_code < 500 and status_code != 429:
                    logger.error(
                        f"LLM API client error (status {status_code}): {e}"
                    )
                    raise
                
                # Retry on server errors (5xx) and rate limits (429)
                if attempt < self.max_retries:
                    logger.warning(
                        f"LLM API error (status {status_code}), "
                        f"retrying in {retry_delay}s: {e}"
                    )
                    await asyncio.sleep(retry_delay)
                    retry_delay = min(retry_delay * 2, self.max_retry_delay)
                else:
                    logger.error(
                        f"LLM API call failed after {self.max_retries + 1} attempts"
                    )
                    raise

            except (httpx.RequestError, httpx.TimeoutException) as e:
                last_exception = e
                
                if attempt < self.max_retries:
                    logger.warning(
                        f"LLM API request error, retrying in {retry_delay}s: {e}"
                    )
                    await asyncio.sleep(retry_delay)
                    retry_delay = min(retry_delay * 2, self.max_retry_delay)
                else:
                    logger.error(
                        f"LLM API call failed after {self.max_retries + 1} attempts"
                    )
                    raise

        # This should never be reached, but just in case
        if last_exception:
            raise last_exception
        raise RuntimeError("LLM API call failed with unknown error")

    async def close(self):
        """Close the HTTP client connection."""
        await self.client.aclose()

    async def __aenter__(self):
        """Async context manager entry."""
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        await self.close()
