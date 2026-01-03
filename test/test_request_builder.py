# test/test_request_builder.py
import unittest
import os
import json
from pathlib import Path

# 테스트 대상 모듈 임포트
from utils.request_builder import BatchRequestBuilder

class TestBatchRequestBuilder(unittest.TestCase):

    def setUp(self):
        """테스트 실행 전 필요한 설정을 합니다."""
        self.test_dir = Path("test_temp_data")
        self.test_dir.mkdir(exist_ok=True)

        self.source_file_path = self.test_dir / "source.txt"
        self.output_file_path = self.test_dir / "requests.jsonl"

        # 테스트용 소스 파일 생성
        with open(self.source_file_path, "w", encoding="utf-8") as f:
            f.write("첫 번째 문단입니다.\n\n두 번째 문단입니다.\n")

        # BatchRequestBuilder 인스턴스 생성
        self.model_id = "gemini-1.5-flash"
        self.system_instruction = "Test instruction"
        self.base_prompt = [{"role": "user", "parts": [{"text": "base"}]}]
        self.generation_config = {"temperature": 0.5}
        
        self.builder = BatchRequestBuilder(
            model_id=self.model_id,
            system_instruction=self.system_instruction,
            base_prompt=self.base_prompt,
            generation_config=self.generation_config
        )

    def tearDown(self):
        """테스트 실행 후 생성된 파일들을 정리합니다."""
        if os.path.exists(self.source_file_path):
            os.remove(self.source_file_path)
        if os.path.exists(self.output_file_path):
            os.remove(self.output_file_path)
        if os.path.exists(self.test_dir):
            os.rmdir(self.test_dir)

    def test_create_requests_from_file(self):
        """
        소스 파일로부터 jsonl 요청 파일을 올바르게 생성하는지 테스트합니다.
        """
        # 메서드 실행
        num_requests = self.builder.create_requests_from_file(
            str(self.source_file_path),
            str(self.output_file_path)
        )

        # 1. 반환된 요청 수가 올바른지 확인 (2개 문단)
        self.assertEqual(num_requests, 2)

        # 2. 출력 파일이 생성되었는지 확인
        self.assertTrue(os.path.exists(self.output_file_path))

        # 3. jsonl 파일의 내용이 올바른지 확인
        with open(self.output_file_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        
        # 3-1. 파일에 두 개의 요청이 포함되어 있는지 확인
        self.assertEqual(len(lines), 2)

        # 3-2. 첫 번째 요청의 ���조와 내용 확인
        first_request_data = json.loads(lines[0])
        self.assertEqual(first_request_data["key"], "paragraph_1")
        
        request_content = first_request_data["request"]
        self.assertEqual(request_content["model"], f"models/{self.model_id}")
        self.assertEqual(request_content["system_instruction"]["parts"][0]["text"], self.system_instruction)
        self.assertEqual(request_content["generation_config"], self.generation_config)
        
        # 프롬프트 내용 확인 (기본 프롬프트 + 첫 번째 문단)
        self.assertEqual(len(request_content["contents"]), 2)
        self.assertEqual(request_content["contents"][0], self.base_prompt[0])
        self.assertEqual(request_content["contents"][1]["parts"][0]["text"], "첫 번째 문단입니다.")

        # 3-3. 두 번째 요청의 내용 확인
        second_request_data = json.loads(lines[1])
        self.assertEqual(second_request_data["key"], "paragraph_2")
        self.assertEqual(second_request_data["request"]["contents"][1]["parts"][0]["text"], "두 번째 문단입니다.")

if __name__ == '__main__':
    # test 디렉토리 생성
    if not os.path.exists('test'):
        os.makedirs('test')
    
    # 테스트 실행
    unittest.main(argv=['first-arg-is-ignored'], exit=False)
