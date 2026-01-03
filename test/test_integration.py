# import unittest
# import json
# import os
# from pathlib import Path
# from unittest.mock import MagicMock, patch
# import time
# import threading

# # Ensure the project root is in the Python path
# import sys
# project_root = Path(__file__).resolve().parent.parent
# sys.path.insert(0, str(project_root))

# from app.app_service import AppService
# from core.dtos import TranslationJobProgressDTO

# class TestIntegration(unittest.TestCase):

#     def setUp(self):
#         """Set up a test environment for integration testing."""
#         self.test_dir = Path("test_integration_temp_data")
#         self.test_dir.mkdir(exist_ok=True)

#         # File Paths
#         self.input_file = self.test_dir / "input.txt"
#         self.output_file = self.test_dir / "output.txt"
#         self.config_file = self.test_dir / "config.json"
#         self.memory_file = self.test_dir / "translation_memory.json"

#         # Write sample input file
#         self.input_file.write_text("Chunk 1: Hello.\nChunk 2: World.", encoding='utf-8')

#         # Write sample config file
#         self.test_config = {
#             "api_keys": ["fake_api_key_for_test"], # Add a fake API key to trigger client initialization
#             "model_name": "gemini-2.0-flash",
#             "chunk_size": 20, # Small chunk size to get 2 chunks
#             "max_workers": 1,
#             "enable_translation_memory": True,
#             "translation_memory": {
#                 "storage_path": str(self.memory_file),
#                 "max_chunks_per_summary": 1, # Create a summary for each chunk
#             }
#         }
#         self.config_file.write_text(json.dumps(self.test_config), encoding='utf-8')

#     def tearDown(self):
#         """Clean up the test environment."""
#         for item in self.test_dir.glob('*'):
#             if item.is_file():
#                 item.unlink()
#         self.test_dir.rmdir()

#     @patch('app.app_service.GeminiClient')
#     def test_full_translation_with_memory(self, MockGeminiClient):
#         """
#         Tests a full translation workflow, verifying that the Translation Memory
#         is created and used.
#         """
#         # --- Part 1: First translation run to generate memory ---

#         # Mock GeminiClient and its methods
#         mock_gemini_instance = MockGeminiClient.return_value
#         mock_gemini_instance.generate_text.side_effect = [
#             "Translated: Chunk 1: Hello.", # First call for translation
#             "Summary for chunk 1",         # Second call for summarization
#             "Translated: Chunk 2: World.", # Third call for translation
#             "Summary for chunk 2"          # Fourth call for summarization
#         ]

#         # 1. Initialize and run AppService
#         app_service = AppService(config_file_path=self.config_file)
        
#         # Use a simple callback to wait for completion
#         translation_finished = threading.Event()
#         def status_callback(message: str):
#             if "완료" in message or "오류" in message:
#                 translation_finished.set()

#         app_service.start_translation(
#             self.input_file, self.output_file, status_callback=status_callback
#         )
        
#         # Wait for the translation to finish (with a timeout)
#         completed = translation_finished.wait(timeout=10)
#         self.assertTrue(completed, "Translation did not complete in time")

#         # 2. Verify the results of the first run
#         self.assertTrue(self.output_file.exists())
#         self.assertTrue(self.memory_file.exists())

#         # Check output content
#         output_content = self.output_file.read_text(encoding='utf-8')
#         self.assertIn("Translated: Chunk 1: Hello.", output_content)
#         self.assertIn("Translated: Chunk 2: World.", output_content)

#         # Check memory content
#         memory_content = json.loads(self.memory_file.read_text(encoding='utf-8'))
#         self.assertEqual(len(memory_content["summaries"]), 2)
#         self.assertEqual(memory_content["summaries"][0]["text"], "Summary for chunk 1")
#         self.assertEqual(memory_content["summaries"][1]["text"], "Summary for chunk 2")


#         # --- Part 2: Second run to verify memory is USED ---

#         # Reset the output file to see the new result clearly
#         self.output_file.unlink()

#         # Re-initialize AppService to simulate a fresh start
#         app_service_2 = AppService(config_file_path=self.config_file)
        
#         # Patch the memory service's prompt building method to verify it's called
#         with patch.object(
#             app_service_2.translation_memory_service, 
#             'build_memory_enhanced_prompt', 
#             wraps=app_service_2.translation_memory_service.build_memory_enhanced_prompt
#         ) as mock_build_prompt:
            
#             translation_finished_2 = threading.Event()
#             def status_callback_2(message: str):
#                 if "완료" in message or "오류" in message:
#                     translation_finished_2.set()

#             app_service_2.start_translation(
#                 self.input_file, self.output_file, status_callback=status_callback_2
#             )
            
#             completed_2 = translation_finished_2.wait(timeout=10)
#             self.assertTrue(completed_2, "Second translation did not complete in time")

#             # 3. Verify that the memory was used
#             # Since we are not resuming, it will re-translate all chunks.
#             # The key is to check if the prompt builder was called.
#             self.assertGreater(mock_build_prompt.call_count, 0, "build_memory_enhanced_prompt was not called")


# if __name__ == '__main__':
#     unittest.main()