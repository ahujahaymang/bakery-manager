"""
Example usage of the LLM client.

This file demonstrates how to use the LLMClient for making API calls.
This is just the HTTP client wrapper - the actual intent detection logic
will be implemented in task 14.1 (LLMService).
"""

import asyncio

from app.llm_client import LLMClient


async def example_basic_usage():
    """Basic example of using the LLM client."""
    async with LLMClient() as client:
        messages = [
            {
                "role": "system",
                "content": "You are a helpful assistant for a bakery operations bot."
            },
            {
                "role": "user",
                "content": "Add customer John Doe with phone 1234567890"
            }
        ]
        
        response = await client.call_llm(messages, temperature=0.7)
        
        # Extract the assistant's response
        assistant_message = response["choices"][0]["message"]["content"]
        print(f"LLM Response: {assistant_message}")


async def example_with_custom_params():
    """Example with custom parameters."""
    client = LLMClient(
        max_retries=5,
        initial_retry_delay=2.0,
        timeout=60.0
    )
    
    try:
        messages = [
            {"role": "user", "content": "What is the recipe cost for chocolate cake?"}
        ]
        
        response = await client.call_llm(
            messages,
            temperature=0.3,  # Lower temperature for more deterministic output
            max_tokens=500
        )
        
        print(f"Response: {response}")
    finally:
        await client.close()


async def example_error_handling():
    """Example showing error handling."""
    async with LLMClient() as client:
        try:
            messages = [
                {"role": "user", "content": "Process this order"}
            ]
            
            response = await client.call_llm(messages)
            print(f"Success: {response}")
            
        except Exception as e:
            print(f"Error calling LLM: {e}")
            # Handle the error appropriately
            # In production, this would return a user-friendly error message


if __name__ == "__main__":
    # Run the examples
    print("Example 1: Basic usage")
    asyncio.run(example_basic_usage())
    
    print("\nExample 2: Custom parameters")
    asyncio.run(example_with_custom_params())
    
    print("\nExample 3: Error handling")
    asyncio.run(example_error_handling())
