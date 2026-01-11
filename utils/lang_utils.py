# utils/lang_utils.py

def normalize_language_code(lang: str) -> str:
    """
    언어 코드를 정규화합니다. (예: 'ko-KR' -> 'ko', 'KOREAN' -> 'ko')
    지원되는 알려진 언어 이름들을 ISO 639-1 형식의 짧은 코드로 변환하고, 
    그 외에는 하이픈(-)이나 언더바(_) 이전의 첫 부분만 반환합니다.
    """
    if not lang:
        return ""
    
    # 기본 전처리: 공백 제거 및 소문자화
    lang = lang.strip().lower()
    
    # 1. 알려진 긴 언어 이름 또는 3글자 코드 매핑
    mapping = {
        "korean": "ko",
        "kor": "ko",
        "japanese": "ja",
        "jpn": "ja",
        "chinese": "zh",
        "chi": "zh",
        "zho": "zh",
        "english": "en",
        "eng": "en",
        "french": "fr",
        "fra": "fr",
        "german": "de",
        "deu": "de",
        "spanish": "es",
        "spa": "es",
        "russian": "ru",
        "rus": "ru",
        "vietnamese": "vi",
        "vie": "vi",
        "italian": "it",
        "ita": "it",
        "portuguese": "pt",
        "por": "pt",
    }
    
    # 매핑에 있으면 즉시 반환
    if lang in mapping:
        return mapping[lang]
    
    # 2. BCP-47 또는 ISO 639-1/-2 부가 정보 제거 (예: 'ko-KR' -> 'ko')
    normalized = lang.split('-')[0].split('_')[0]
    
    # 부가 정보 제거 후 다시 매핑 확인
    if normalized in mapping:
        return mapping[normalized]
        
    return normalized
