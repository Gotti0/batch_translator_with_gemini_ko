# import unittest
# import json
# import os
# from pathlib import Path
# from unittest.mock import MagicMock, patch

# # Ensure the project root is in the Python path
# import sys
# project_root = Path(__file__).resolve().parent.parent
# sys.path.insert(0, str(project_root))

# from core.translation_memory.memory_service import TranslationMemoryService
# from core.translation_memory.data_structures import TranslationSummary, TranslationMemoryData

# class TestTranslationMemoryService(unittest.TestCase):

#     def setUp(self):
#         """Set up a test environment before each test."""
#         self.test_dir = Path("test_temp_data")
#         self.test_dir.mkdir(exist_ok=True)
#         self.test_memory_file = self.test_dir / "test_memory.json"

#         # Mock GeminiClient
#         self.mock_gemini_client = MagicMock()
#         self.mock_gemini_client.generate_text.return_value = "This is a test summary."

#         # Mock config
#         self.test_config = {
#             "translation_memory": {
#                 "storage_path": str(self.test_memory_file),
#                 "max_chunks_per_summary": 2,
#                 "embedding_model": "sentence-transformers/all-MiniLM-L6-v2"
#             }
#         }

#         # We will patch the summarization service in each test that needs it
#         self.memory_service = TranslationMemoryService(
#             gemini_client=self.mock_gemini_client,
#             config=self.test_config
#         )

#     def tearDown(self):
#         """Clean up the test environment after each test."""
#         if self.test_memory_file.exists():
#             self.test_memory_file.unlink()
#         # Clean up any other files in test_dir before removing it
#         for item in self.test_dir.iterdir():
#             item.unlink()
#         self.test_dir.rmdir()

#     def test_01_initialization(self):
#         """Test if the service initializes correctly."""
#         self.assertIsNotNone(self.memory_service)
#         self.assertIsInstance(self.memory_service.memory_data, TranslationMemoryData)
#         self.assertEqual(len(self.memory_service.memory_data.summaries), 0)

#     @patch('core.translation_memory.summarization.TranslationSummarizationService.create_translation_summary')
#     def test_02_store_result_and_create_summary(self, mock_create_summary):
#         """Test storing results and automatic summary creation."""
#         # Use a real TranslationSummary object for the mock return value
#         mock_summary = TranslationSummary(
#             text="This is a test summary.",
#             source_chunks={0, 1},
#             embedding=[0.1] * 384
#         )
#         mock_create_summary.return_value = mock_summary

#         self.memory_service.store_translation_result(0, "Hello", "안녕하세요")
#         self.memory_service.store_translation_result(1, "World", "세계")
        
#         self.assertTrue(self.test_memory_file.exists())
#         self.assertEqual(len(self.memory_service.memory_data.summaries), 1)
#         summary = self.memory_service.memory_data.summaries[0]
#         self.assertEqual(summary.text, "This is a test summary.")
#         self.assertEqual(summary.source_chunks, {0, 1})

#     @patch('core.translation_memory.summarization.TranslationSummarizationService.create_translation_summary')
#     def test_03_save_memory_on_remaining_chunks(self, mock_create_summary):
#         """Test the final save_memory method."""
#         mock_summary = TranslationSummary(source_chunks={0})
#         mock_create_summary.return_value = mock_summary

#         self.memory_service.store_translation_result(0, "Test", "테스트")
#         self.memory_service.save_memory()
        
#         self.assertEqual(len(self.memory_service.memory_data.summaries), 1)
#         self.assertEqual(self.memory_service.memory_data.summaries[0].source_chunks, {0})

#     @patch('core.translation_memory.summarization.TranslationSummarizationService.create_translation_summary')
#     def test_04_get_stats(self, mock_create_summary):
#         """Test the get_stats method."""
#         mock_create_summary.return_value = TranslationSummary(text="A summary")
#         self.memory_service.store_translation_result(0, "s1", "t1")
#         self.memory_service.store_translation_result(1, "s2", "t2")
        
#         stats = self.memory_service.get_stats()
        
#         self.assertEqual(stats["summaries"], 1)
#         self.assertGreater(stats["file_size"], 0)

#     @patch('core.translation_memory.summarization.TranslationSummarizationService.create_translation_summary')
#     def test_05_clear_memory(self, mock_create_summary):
#         """Test the clear_memory method."""
#         mock_create_summary.return_value = TranslationSummary(text="A summary")
#         self.memory_service.store_translation_result(0, "s1", "t1")
#         self.memory_service.store_translation_result(1, "s2", "t2")
        
#         self.memory_service.clear_memory()
        
#         self.assertEqual(len(self.memory_service.memory_data.summaries), 0)
#         # Ensure the file is recreated, size is not critical as long as it's reset
#         self.assertTrue(self.test_memory_file.exists())

#     @patch('core.translation_memory.summarization.TranslationSummarizationService.create_translation_summary')
#     def test_06_export_memory_as_json(self, mock_create_summary):
#         """Test exporting memory to a JSON string."""
#         mock_summary = TranslationSummary(
#             text="This is a test summary.",
#             key_terms={"term1": "용어1"}
#         )
#         mock_create_summary.return_value = mock_summary
        
#         self.memory_service.store_translation_result(0, "s1", "t1", {"term1": "용어1"})
#         self.memory_service.store_translation_result(1, "s2", "t2")
        
#         json_str = self.memory_service.export_memory_as_json()
#         data = json.loads(json_str)
        
#         self.assertEqual(len(data["summaries"]), 1)
#         self.assertEqual(data["summaries"][0]["text"], "This is a test summary.")
#         self.assertEqual(data["summaries"][0]["key_terms"], {"term1": "용어1"})

# if __name__ == '__main__':
#     unittest.main()