# listed_models.py
from google import genai
import logging
import time
import threading

# 로깅 설정
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class ModelLister:
    """Google Gemini API에서 사용 가능한 모델 목록을 가져오는 클래스"""
    
    def __init__(self, api_key=None, tqdm_out=None):
        """
        ModelLister 클래스 초기화
        
        Args:
            api_key (str, optional): Google Gemini API 키
            tqdm_out: GUI 로그 출력용 객체
        """
        self.api_key = api_key
        self.models = []
        self.last_update = None
        self._lock = threading.Lock()
        self.tqdm_out = tqdm_out
    
    def log(self, message):
        """로그 출력 (GUI 또는 콘솔)"""
        if self.tqdm_out:
            self.tqdm_out.write(message + "\n")
        else:
            logger.info(message)
    
    def set_api_key(self, api_key):
        """API 키 설정"""
        self.api_key = api_key
        
    def get_models(self, force_refresh=False):
        """
        사용 가능한 모델 목록 반환
        
        Args:
            force_refresh (bool): 강제로 새로고침할지 여부
            
        Returns:
            list: 모델 이름 목록
        """
        # API 키 확인
        if not self.api_key:
            self.log("API 키가 설정되지 않았습니다.")
            return []
            
        # 캐시된 모델 사용 여부 확인
        with self._lock:
            if not force_refresh and self.models:
                return self.models
            
            # 모델 목록 가져오기
            try:
                self.log("모델 목록 가져오는 중...")
                
                # Google API 구성
                client = genai.Client(api_key=self.api_key)
                models = client.models.list()
                
                # 텍스트 생성 가능한 모델만 필터링
                valid_models = [
                    model.name.split('/')[-1]  # 'models/gemini-1.0-pro' -> 'gemini-1.0-pro'
                    for model in models
                    if 'generateContent' in model.supported_actions
                ]
                
                # 결과 저장
                self.models = sorted(valid_models)
                self.last_update = time.time()
                
                self.log(f"{len(self.models)}개의 모델을 성공적으로 가져왔습니다.")
                return self.models
                
            except Exception as e:
                self.log(f"모델 목록을 가져오는 중 오류 발생: {str(e)}")
                return []
    
    def get_recommended_models(self):
        """
        번역에 권장되는 모델 목록 반환
        
        Returns:
            list: 권장 모델 이름 목록
        """
        # 모든 모델 가져오기
        all_models = self.get_models()
        
        # 번역에 적합한 모델 필터링
        recommended = [
            model for model in all_models 
            if any(keyword in model.lower() for keyword in ['gemini-2.0', 'gemini-1.5', 'flash', 'pro'])
        ]
        
        return recommended if recommended else all_models

def fetch_models(api_key, tqdm_out=None):
    """
    사용 가능한 모델 목록을 가져오는 함수
    
    Args:
        api_key (str): Google Gemini API 키
        tqdm_out: GUI 로그 출력용 객체
        
    Returns:
        list: 모델 이름 목록
    """
    lister = ModelLister(api_key, tqdm_out)
    return lister.get_models()

def fetch_recommended_models(api_key, tqdm_out=None):
    """
    번역에 권장되는 모델 목록을 가져오는 함수
    
    Args:
        api_key (str): Google Gemini API 키
        tqdm_out: GUI 로그 출력용 객체
        
    Returns:
        list: 권장 모델 이름 목록
    """
    lister = ModelLister(api_key, tqdm_out)
    return lister.get_recommended_models()

def fetch_models_async(api_key, callback, tqdm_out=None):
    """
    백그라운드 스레드에서 모델 목록을 가져오는 함수
    
    Args:
        api_key (str): Google Gemini API 키
        callback (function): 모델 목록을 가져온 후 호출할 콜백 함수
        tqdm_out: GUI 로그 출력용 객체
    """
    def _fetch():
        models = fetch_models(api_key, tqdm_out)
        if callback:
            callback(models)
    
    thread = threading.Thread(target=_fetch)
    thread.daemon = True
    thread.start()
    return thread

def get_model_details(api_key, model_name, tqdm_out=None):
    """
    특정 모델의 세부 정보를 가져오는 함수
    
    Args:
        api_key (str): Google Gemini API 키
        model_name (str): 모델 이름
        tqdm_out: GUI 로그 출력용 객체
        
    Returns:
        dict: 모델 세부 정보
    """
    try:
        lister = ModelLister(api_key, tqdm_out)
        lister.log(f"'{model_name}' 모델의 세부 정보 가져오는 중...")
        
        client = genai.Client(api_key=api_key)
        
        # 모델 목록에서 특정 모델 찾기
        models = client.models.list()
        for model in models:
            if model.name.endswith(model_name):
                # 모델 세부 정보 구성
                details = {
                    "name": model.name.split('/')[-1],
                    "display_name": getattr(model, "display_name", model.name.split('/')[-1]),
                    "description": getattr(model, "description", ""),
                    "supported_actions": getattr(model, "supported_actions", []),
                    "input_token_limit": getattr(model, "input_token_limit", 0),
                    "output_token_limit": getattr(model, "output_token_limit", 0),
                }
                return details
        
        lister.log(f"모델 '{model_name}'을(를) 찾을 수 없습니다.")
        return None
        
    except Exception as e:
        if tqdm_out:
            tqdm_out.write(f"모델 세부 정보를 가져오는 중 오류 발생: {str(e)}\n")
        else:
            logger.error(f"모델 세부 정보를 가져오는 중 오류 발생: {str(e)}")
        return None
