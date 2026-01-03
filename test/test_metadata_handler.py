# test/test_metadata_handler.py
import unittest
from pathlib import Path
import os
import json
import time

# 테스트 대상 모듈 임포트
from infrastructure.file_handler import (
    get_metadata_file_path, load_metadata, save_metadata,
    _hash_config_for_metadata, create_new_metadata,
    update_metadata_for_chunk_completion
)

class TestMetadataHandler(unittest.TestCase):

    def setUp(self):
        """테스트를 위한 임시 디렉터리와 파일 경로를 설정합니다."""
        self.test_dir = Path("test_temp_data")
        self.test_dir.mkdir(exist_ok=True)
        self.input_file = self.test_dir / "sample.txt"
        with open(self.input_file, "w") as f:
            f.write("test content")
        
        self.sample_metadata = {
            "input_file": str(self.input_file),
            "total_chunks": 5,
            "translated_chunks": {"0": time.time()},
            "status": "in_progress"
        }

    def tearDown(self):
        """테스트 후 생성된 파일과 디렉터리를 정리합니다."""
        for f in self.test_dir.glob('*'):
            os.remove(f)
        os.rmdir(self.test_dir)

    def test_get_metadata_file_path(self):
        """메타데이터 파일 경로가 올바르게 생성되는지 테스트합니다."""
        metadata_path = get_metadata_file_path(self.input_file)
        self.assertEqual(metadata_path, self.test_dir / "sample_metadata.json")

        # 이미 메타데이터 경로인 경우
        metadata_path_itself = self.test_dir / "sample_metadata.json"
        self.assertEqual(get_metadata_file_path(metadata_path_itself), metadata_path_itself)

    def test_load_and_save_metadata(self):
        """메타데이터 저장 및 로드 기능을 테스트합니다."""
        metadata_path = get_metadata_file_path(self.input_file)
        
        # 1. 없는 파일 로드 시 빈 딕셔너리 반환
        loaded_empty = load_metadata(self.input_file)
        self.assertEqual(loaded_empty, {})

        # 2. 메타데이터 저장
        save_metadata(self.input_file, self.sample_metadata)
        self.assertTrue(metadata_path.exists())

        # 3. 저장된 메타데이터 로드
        loaded_data = load_metadata(self.input_file)
        self.assertEqual(loaded_data["total_chunks"], self.sample_metadata["total_chunks"])
        self.assertEqual(loaded_data["status"], self.sample_metadata["status"])

    def test_create_new_metadata(self):
        """새로운 메타데이터 생성 기능을 테스트합니다."""
        config = {"model": "test-model", "temperature": 0.8}
        metadata = create_new_metadata(self.input_file, 10, config)
        
        self.assertEqual(metadata["total_chunks"], 10)
        self.assertEqual(metadata["status"], "initialized")
        self.assertIn("config_hash", metadata)
        self.assertEqual(metadata["config_hash"], _hash_config_for_metadata(config))

    def test_update_metadata_for_chunk_completion(self):
        """청크 완료 시 메타데이터 업데이트 기능을 테스트합니다."""
        # 초기 메타데이터 저장
        save_metadata(self.input_file, self.sample_metadata)
        
        # 청크 완료 업데이트
        update_metadata_for_chunk_completion(self.input_file, 1)
        
        # 업데이트된 내용 확인
        updated_metadata = load_metadata(self.input_file)
        self.assertIn("1", updated_metadata["translated_chunks"])
        self.assertEqual(updated_metadata["status"], "in_progress")

        # 모든 청크 완료 시 상태 변경 확인
        for i in range(1, 5): # 0번은 setUp에서 이미 완료됨
            update_metadata_for_chunk_completion(self.input_file, i)
        
        final_metadata = load_metadata(self.input_file)
        self.assertEqual(len(final_metadata["translated_chunks"]), 5)
        self.assertEqual(final_metadata["status"], "completed")

if __name__ == '__main__':
    unittest.main(argv=['first-arg-is-ignored'], exit=False)
