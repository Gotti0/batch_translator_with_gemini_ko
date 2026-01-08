import unittest
import asyncio
import sys
import os
from unittest.mock import MagicMock, patch, AsyncMock
from google.api_core import exceptions as api_core_exceptions
from google.genai import types as genai_types

# Add project root to sys.path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from infrastructure.gemini_client import GeminiClient, GeminiApiException

class TestGeminiClientTimeout(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        # API Key mocking to surpass initialization checks
        self.api_key = "fake_api_key"
        self.patcher = patch('infrastructure.gemini_client.genai.Client')
        self.MockGenaiClient = self.patcher.start()
        
        # Setup the mock client instance
        self.mock_client_instance = AsyncMock()
        self.MockGenaiClient.return_value = self.mock_client_instance
        self.mock_client_instance.aio.models.generate_content = AsyncMock()

    async def asyncTearDown(self):
        self.patcher.stop()

    async def test_timeout_configuration_initialization(self):
        """Test if api_timeout is correctly converted to http_options in milliseconds"""
        api_timeout_sec = 10.5
        expected_timeout_ms = 10500
        
        client = GeminiClient(auth_credentials=self.api_key, api_timeout=api_timeout_sec)
        
        # Verify http_options attribute
        self.assertIsInstance(client.http_options, genai_types.HttpOptions)
        self.assertEqual(client.http_options.timeout, expected_timeout_ms)
        
        # Verify it was passed to genai.Client
        # Note: Depending on implementation, it might be passed in __init__ or generate
        # In current implementation, it's passed efficiently.
        # Check __init__ call args of GenaiClient
        call_kwargs = self.MockGenaiClient.call_args.kwargs
        self.assertIn('http_options', call_kwargs)
        self.assertEqual(call_kwargs['http_options'].timeout, expected_timeout_ms)

    async def test_retry_on_timeout_exception(self):
        """Test if the client retries on DeadlineExceeded exception"""
        client = GeminiClient(auth_credentials=self.api_key, api_timeout=5.0)
        
        # Configure mock to raise DeadlineExceeded
        # Simulate generic 504 deadline exceeded from google.api_core
        timeout_error = api_core_exceptions.DeadlineExceeded("Deadline Exceeded")
        self.mock_client_instance.aio.models.generate_content.side_effect = timeout_error
        
        max_retries = 2
        
        # We expect it to eventually fail or return None/Empty depending on logic after retries
        # But here we want to ensure it actually retried.
        
        # Since _generate_text_async_impl raises an exception after retries are exhausted (if not switched to next key)
        # We expect a GeminiApiException or similar (or specifically what the code raises).
        # In the code: catch Exception -> log -> if retry exhausted -> break loop -> eventually raise GeminiAllApiKeysExhaustedException (since we have 1 key)
        
        from infrastructure.gemini_client import GeminiAllApiKeysExhaustedException
        
        with self.assertRaises(GeminiAllApiKeysExhaustedException):
            await client.generate_text_async(
                prompt="test prompt",
                model_name="gemini-test",
                max_retries=max_retries,
                initial_backoff=0.1, # Short backoff for test speed
                max_backoff=0.2
            )
            
        # Verify call count
        # initial attempt + max_retries
        expected_call_count = 1 + max_retries
        self.assertEqual(self.mock_client_instance.aio.models.generate_content.call_count, expected_call_count)

    async def test_cleanup_after_timeout(self):
        """Test success after one timeout failure (retry works)"""
        client = GeminiClient(auth_credentials=self.api_key, api_timeout=5.0)
        
        timeout_error = api_core_exceptions.DeadlineExceeded("Deadline Exceeded")
        mock_response = MagicMock()
        mock_response.text = "Success after retry"
        # Explicitly set prompt_feedback to None to avoid safety check triggers via MagicMock
        mock_response.prompt_feedback = None
        mock_response.candidates = []
        
        # Fail once, then succeed
        self.mock_client_instance.aio.models.generate_content.side_effect = [timeout_error, mock_response]
        
        result = await client.generate_text_async(
            prompt="test prompt",
            model_name="gemini-test",
            max_retries=2,
            initial_backoff=0.1
        )
        
        self.assertEqual(result, "Success after retry")
        self.assertEqual(self.mock_client_instance.aio.models.generate_content.call_count, 2)

if __name__ == '__main__':
    unittest.main()
