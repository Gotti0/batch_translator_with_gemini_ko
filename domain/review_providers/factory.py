from domain.review_providers.base_provider import BaseReviewProvider
from domain.review_providers.standard_provider import StandardReviewProvider
from domain.review_providers.integrity_provider import IntegrityReviewProvider
from domain.review_providers.epub_provider import EpubReviewProvider
from infrastructure.file_handler import load_metadata

def get_review_provider(file_path: str, app_service) -> BaseReviewProvider:
    if file_path.lower().endswith('.epub'):
        return EpubReviewProvider(app_service)
        
    meta = load_metadata(file_path)
    pipeline_type = meta.get("pipeline_type", "standard")
    
    if pipeline_type == "integrity":
        return IntegrityReviewProvider(app_service)
    return StandardReviewProvider(app_service)
