import math
from typing import Dict, Any, List, Tuple
import logging

try:
    from infrastructure.logger_config import setup_logger
except ImportError:
    # 단위 테스트 또는 독립 실행 시 폴백
    logging.basicConfig(level=logging.INFO)
    def setup_logger(name):
        return logging.getLogger(name)

logger = setup_logger(__name__)

class QualityCheckService:
    """
    번역 품질 이상 감지 서비스.
    선형 회귀 분석을 통해 번역 누락(Omission) 및 환각(Hallucination) 의심 구간을 탐지합니다.
    외부 라이브러리(numpy 등) 없이 순수 Python으로 구현되었습니다.
    """

    def __init__(self):
        pass

    def analyze_translation_quality(self, metadata: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        메타데이터의 번역 통계를 분석하여 이상치 목록을 반환합니다.
        
        Args:
            metadata: 'translated_chunks' 정보를 포함한 메타데이터 딕셔너리

        Returns:
            이상치 정보가 담긴 딕셔너리 리스트
        """
        translated_chunks = metadata.get('translated_chunks', {})
        if not translated_chunks:
            return []

        # 데이터 포인트 수집 (chunk_index, source_length, translated_length)
        data_points: List[Tuple[int, int, int]] = []
        
        for idx_str, info in translated_chunks.items():
            try:
                if not isinstance(info, dict):
                    continue
                idx = int(idx_str)
                src_len = info.get('source_length', 0)
                trans_len = info.get('translated_length', 0)
                
                # 유효한 데이터만 포함 (길이가 0인 경우 제외)
                if src_len > 0 and trans_len > 0:
                    data_points.append((idx, src_len, trans_len))
            except (ValueError, TypeError):
                continue

        # 데이터가 너무 적으면 분석 신뢰도가 낮으므로 스킵 (최소 5개)
        n = len(data_points)
        if n < 5:
            return []

        # 1. 선형 회귀 파라미터 계산 (Least Squares Method)
        # y = ax + b
        sum_x = sum(p[1] for p in data_points)
        sum_y = sum(p[2] for p in data_points)
        sum_xy = sum(p[1] * p[2] for p in data_points)
        sum_x2 = sum(p[1] ** 2 for p in data_points)

        denominator = (n * sum_x2) - (sum_x ** 2)
        
        if denominator == 0:
            # 모든 x값이 동일한 경우 등 회귀 분석 불가능
            return []

        a = ((n * sum_xy) - (sum_x * sum_y)) / denominator
        b = (sum_y - (a * sum_x)) / n

        # 2. 잔차(Residual) 및 표준편차 계산
        residuals = []
        for idx, x, y in data_points:
            predicted_y = (a * x) + b
            residual = y - predicted_y
            residuals.append(residual)

        mean_residual = sum(residuals) / n # 이론상 0에 가까움
        variance = sum((r - mean_residual) ** 2 for r in residuals) / n
        std_dev = math.sqrt(variance)

        if std_dev == 0:
            return []

        # 3. 이상치 탐지 (Z-Score)
        suspicious_chunks = []
        
        for i, (idx, x, y) in enumerate(data_points):
            residual = residuals[i]
            z_score = residual / std_dev
            
            issue_type = None
            
            # 임계값 설정 (표준편차의 2배)
            if z_score < -2.0:
                issue_type = "omission" # 예상보다 짧음 (누락 의심)
            elif z_score > 2.0:
                issue_type = "hallucination" # 예상보다 김 (환각 의심)
            
            if issue_type:
                expected_y = (a * x) + b
                suspicious_chunks.append({
                    "chunk_index": idx,
                    "issue_type": issue_type,
                    "source_length": x,
                    "translated_length": y,
                    "expected_length": round(expected_y, 2),
                    "ratio": round(y / x, 4) if x > 0 else 0,
                    "z_score": round(z_score, 2)
                })

        # 인덱스 순으로 정렬하여 반환
        suspicious_chunks.sort(key=lambda item: item['chunk_index'])
        
        return suspicious_chunks
