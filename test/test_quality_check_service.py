import unittest
from utils.quality_check_service import QualityCheckService

class TestQualityCheckService(unittest.TestCase):
    def setUp(self):
        self.service = QualityCheckService()

    def test_insufficient_data(self):
        """데이터가 5개 미만일 때 분석을 수행하지 않고 빈 리스트를 반환해야 함"""
        metadata = {
            "translated_chunks": {
                "0": {"source_length": 100, "translated_length": 100},
                "1": {"source_length": 100, "translated_length": 100},
                "2": {"source_length": 100, "translated_length": 100}
            }
        }
        result = self.service.analyze_translation_quality(metadata)
        self.assertEqual(result, [])

    def test_perfect_linear_relationship(self):
        """완벽한 선형 관계에서는 이상치가 없어야 함"""
        metadata = {"translated_chunks": {}}
        for i in range(10):
            metadata["translated_chunks"][str(i)] = {
                "source_length": 100 * (i + 1),
                "translated_length": 100 * (i + 1)
            }
        result = self.service.analyze_translation_quality(metadata)
        self.assertEqual(result, [])

    def test_detect_omission(self):
        """번역 누락(길이가 매우 짧음)을 감지해야 함"""
        metadata = {"translated_chunks": {}}
        # 정상 데이터 10개 (y = x) - x값 분산 필요 (분모 0 방지)
        for i in range(10):
            length = 1000 + (i * 50) 
            metadata["translated_chunks"][str(i)] = {
                "source_length": length,
                "translated_length": length
            }
        # 누락 데이터 1개 (길이 100, 1/10 수준)
        metadata["translated_chunks"]["10"] = {
            "source_length": 1000,
            "translated_length": 100
        }
        
        result = self.service.analyze_translation_quality(metadata)
        
        # 결과 검증
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['chunk_index'], 10)
        self.assertEqual(result[0]['issue_type'], 'omission')
        self.assertTrue(result[0]['z_score'] < -2.0)

    def test_detect_hallucination(self):
        """환각(길이가 매우 김)을 감지해야 함"""
        metadata = {"translated_chunks": {}}
        # 정상 데이터 10개 (y = x) - x값 분산 필요 (분모 0 방지)
        for i in range(10):
            length = 1000 + (i * 50)
            metadata["translated_chunks"][str(i)] = {
                "source_length": length,
                "translated_length": length
            }
        # 환각 데이터 1개 (길이 3000, 3배 수준)
        metadata["translated_chunks"]["10"] = {
            "source_length": 1000,
            "translated_length": 3000
        }
        
        result = self.service.analyze_translation_quality(metadata)
        
        # 결과 검증
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['chunk_index'], 10)
        self.assertEqual(result[0]['issue_type'], 'hallucination')
        self.assertTrue(result[0]['z_score'] > 2.0)

    def test_invalid_data_handling(self):
        """잘못된 데이터 형식이 섞여 있어도 견고하게 처리해야 함"""
        metadata = {
            "translated_chunks": {
                "0": {"source_length": 100, "translated_length": 100},
                "1": "invalid_data", # 잘못된 형식 (dict가 아님)
                "2": {"source_length": 0, "translated_length": 100}, # 길이가 0 (제외됨)
                "3": {"source_length": 100, "translated_length": 0}, # 길이가 0 (제외됨)
                "4": {"source_length": 100}, # 키 누락
            }
        }
        # 유효한 데이터가 부족하므로 빈 리스트 반환
        result = self.service.analyze_translation_quality(metadata)
        self.assertEqual(result, [])

if __name__ == '__main__':
    unittest.main()
