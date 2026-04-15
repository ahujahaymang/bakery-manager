"""
Tests for the LLM client module.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.llm_client import LLMClient


@pytest.fixture
def mock_settings():
    """Mock settings for testing."""
    with patch("app.llm_client.settings") as mock:
        mock.LLM_API_KEY = "test-api-key"
        mock.LLM_API_BASE_URL = "https://api.test.com/v1"
        mock.LLM_MODEL = "test-model"
        yield mock


@pytest.fixture
def llm_client(mock_settings):
    """Create an LLM client for testing."""
    return LLMClient()


@pytest.mark.asyncio
async def test_llm_client_initialization(mock_settings):
    """Test that LLM client initializes with correct settings."""
    client = LLMClient()
    
    assert client.api_key == "test-api-key"
    assert client.base_url == "https://api.test.com/v1"
    assert client.model == "test-model"
    assert client.max_retries == 3
    assert client.initial_retry_delay == 1.0
    assert client.max_retry_delay == 10.0
    
    await client.close()


@pytest.mark.asyncio
async def test_llm_client_initialization_with_custom_values():
    """Test that LLM client can be initialized with custom values."""
    client = LLMClient(
        api_key="custom-key",
        base_url="https://custom.api.com",
        model="custom-model",
        max_retries=5,
        initial_retry_delay=2.0,
        max_retry_delay=20.0,
    )
    
    assert client.api_key == "custom-key"
    assert client.base_url == "https://custom.api.com"
    assert client.model == "custom-model"
    assert client.max_retries == 5
    assert client.initial_retry_delay == 2.0
    assert client.max_retry_delay == 20.0
    
    await client.close()


@pytest.mark.asyncio
async def test_llm_client_missing_api_key():
    """Test that LLM client raises error when API key is missing."""
    with patch("app.llm_client.settings") as mock:
        mock.LLM_API_KEY = ""
        mock.LLM_API_BASE_URL = "https://api.test.com/v1"
        mock.LLM_MODEL = "test-model"
        
        with pytest.raises(ValueError, match="LLM_API_KEY must be configured"):
            LLMClient()


@pytest.mark.asyncio
async def test_call_llm_success(llm_client):
    """Test successful LLM API call."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "id": "test-id",
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": "Test response"
                }
            }
        ]
    }
    
    with patch.object(llm_client.client, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_response
        
        messages = [{"role": "user", "content": "Test message"}]
        result = await llm_client.call_llm(messages)
        
        assert result["id"] == "test-id"
        assert result["choices"][0]["message"]["content"] == "Test response"
        
        # Verify the request was made correctly
        mock_post.assert_called_once()
        call_args = mock_post.call_args
        assert call_args[0][0] == "https://api.test.com/v1/chat/completions"
        assert call_args[1]["json"]["model"] == "test-model"
        assert call_args[1]["json"]["messages"] == messages
    
    await llm_client.close()


@pytest.mark.asyncio
async def test_call_llm_with_optional_params(llm_client):
    """Test LLM API call with optional parameters."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"id": "test-id"}
    
    with patch.object(llm_client.client, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_response
        
        messages = [{"role": "user", "content": "Test"}]
        await llm_client.call_llm(
            messages,
            temperature=0.5,
            max_tokens=100,
            top_p=0.9
        )
        
        call_args = mock_post.call_args
        payload = call_args[1]["json"]
        assert payload["temperature"] == 0.5
        assert payload["max_tokens"] == 100
        assert payload["top_p"] == 0.9
    
    await llm_client.close()


@pytest.mark.asyncio
async def test_call_llm_retry_on_server_error(llm_client):
    """Test that LLM client retries on server errors."""
    # First two calls fail with 500, third succeeds
    error_response = MagicMock()
    error_response.status_code = 500
    error_response.raise_for_status.side_effect = httpx.HTTPStatusError(
        "Server error", request=MagicMock(), response=error_response
    )
    
    success_response = MagicMock()
    success_response.status_code = 200
    success_response.json.return_value = {"id": "success"}
    
    with patch.object(llm_client.client, "post", new_callable=AsyncMock) as mock_post:
        mock_post.side_effect = [
            error_response,
            error_response,
            success_response
        ]
        
        # Reduce retry delay for faster test
        llm_client.initial_retry_delay = 0.01
        
        messages = [{"role": "user", "content": "Test"}]
        result = await llm_client.call_llm(messages)
        
        assert result["id"] == "success"
        assert mock_post.call_count == 3
    
    await llm_client.close()


@pytest.mark.asyncio
async def test_call_llm_retry_on_rate_limit(llm_client):
    """Test that LLM client retries on rate limit errors (429)."""
    error_response = MagicMock()
    error_response.status_code = 429
    error_response.raise_for_status.side_effect = httpx.HTTPStatusError(
        "Rate limit", request=MagicMock(), response=error_response
    )
    
    success_response = MagicMock()
    success_response.status_code = 200
    success_response.json.return_value = {"id": "success"}
    
    with patch.object(llm_client.client, "post", new_callable=AsyncMock) as mock_post:
        mock_post.side_effect = [error_response, success_response]
        
        llm_client.initial_retry_delay = 0.01
        
        messages = [{"role": "user", "content": "Test"}]
        result = await llm_client.call_llm(messages)
        
        assert result["id"] == "success"
        assert mock_post.call_count == 2
    
    await llm_client.close()


@pytest.mark.asyncio
async def test_call_llm_no_retry_on_client_error(llm_client):
    """Test that LLM client does not retry on client errors (4xx except 429)."""
    error_response = MagicMock()
    error_response.status_code = 400
    error_response.raise_for_status.side_effect = httpx.HTTPStatusError(
        "Bad request", request=MagicMock(), response=error_response
    )
    
    with patch.object(llm_client.client, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = error_response
        
        messages = [{"role": "user", "content": "Test"}]
        
        with pytest.raises(httpx.HTTPStatusError):
            await llm_client.call_llm(messages)
        
        # Should only try once, no retries
        assert mock_post.call_count == 1
    
    await llm_client.close()


@pytest.mark.asyncio
async def test_call_llm_exponential_backoff(llm_client):
    """Test that retry delays follow exponential backoff."""
    error_response = MagicMock()
    error_response.status_code = 500
    error_response.raise_for_status.side_effect = httpx.HTTPStatusError(
        "Server error", request=MagicMock(), response=error_response
    )
    
    with patch.object(llm_client.client, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = error_response
        
        llm_client.initial_retry_delay = 0.1
        llm_client.max_retry_delay = 1.0
        
        messages = [{"role": "user", "content": "Test"}]
        
        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            with pytest.raises(httpx.HTTPStatusError):
                await llm_client.call_llm(messages)
            
            # Should have 3 sleep calls (for 3 retries)
            assert mock_sleep.call_count == 3
            
            # Verify exponential backoff: 0.1, 0.2, 0.4
            sleep_delays = [call[0][0] for call in mock_sleep.call_args_list]
            assert sleep_delays[0] == 0.1
            assert sleep_delays[1] == 0.2
            assert sleep_delays[2] == 0.4
    
    await llm_client.close()


@pytest.mark.asyncio
async def test_call_llm_max_retry_delay(llm_client):
    """Test that retry delay respects max_retry_delay."""
    error_response = MagicMock()
    error_response.status_code = 500
    error_response.raise_for_status.side_effect = httpx.HTTPStatusError(
        "Server error", request=MagicMock(), response=error_response
    )
    
    with patch.object(llm_client.client, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = error_response
        
        llm_client.initial_retry_delay = 5.0
        llm_client.max_retry_delay = 6.0
        
        messages = [{"role": "user", "content": "Test"}]
        
        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            with pytest.raises(httpx.HTTPStatusError):
                await llm_client.call_llm(messages)
            
            # Verify delays are capped at max_retry_delay
            sleep_delays = [call[0][0] for call in mock_sleep.call_args_list]
            assert sleep_delays[0] == 5.0  # Initial delay
            assert sleep_delays[1] == 6.0  # Capped at max (would be 10.0)
            assert sleep_delays[2] == 6.0  # Still capped
    
    await llm_client.close()


@pytest.mark.asyncio
async def test_call_llm_request_error_retry(llm_client):
    """Test that LLM client retries on request errors."""
    with patch.object(llm_client.client, "post", new_callable=AsyncMock) as mock_post:
        # First call raises RequestError, second succeeds
        success_response = MagicMock()
        success_response.status_code = 200
        success_response.json.return_value = {"id": "success"}
        
        mock_post.side_effect = [
            httpx.RequestError("Connection failed"),
            success_response
        ]
        
        llm_client.initial_retry_delay = 0.01
        
        messages = [{"role": "user", "content": "Test"}]
        result = await llm_client.call_llm(messages)
        
        assert result["id"] == "success"
        assert mock_post.call_count == 2
    
    await llm_client.close()


@pytest.mark.asyncio
async def test_context_manager(mock_settings):
    """Test that LLM client works as async context manager."""
    async with LLMClient() as client:
        assert client.api_key == "test-api-key"
        assert client.client is not None
    
    # Client should be closed after exiting context
    # We can't easily test this without accessing internals
