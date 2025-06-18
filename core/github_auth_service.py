# github_auth_service.py
import json
import time
import requests
from typing import Dict, Any, Optional, Tuple
from dataclasses import dataclass
import logging

try:
    from ..core.config.config_manager import ConfigManager
except ImportError:
    from core.config.config_manager import ConfigManager

try:
    from ..infrastructure.logger_config import setup_logger
except ImportError:
    from infrastructure.logger_config import setup_logger

logger = setup_logger(__name__)

# GitHub Copilot OAuth 설정
GITHUB_COPILOT_CLIENT_ID = "01ab8ac9400c4e429b23"
GITHUB_COPILOT_SCOPE = "copilot"
DEVICE_CODE_URL = "https://github.com/login/device/code"
ACCESS_TOKEN_URL = "https://github.com/login/oauth/access_token"

@dataclass
class DeviceCodeResponse:
    """GitHub Device Code 응답 데이터"""
    device_code: str
    user_code: str
    verification_uri: str
    expires_in: int
    interval: int

@dataclass
class AccessTokenResponse:
    """GitHub Access Token 응답 데이터"""
    access_token: str
    token_type: str
    scope: str

class GitHubAuthException(Exception):
    """GitHub 인증 관련 예외"""
    pass

class GitHubAuthService:
    """GitHub OAuth Device Flow를 처리하는 서비스 클래스"""
    
    def __init__(self, config_manager: Optional[ConfigManager] = None):
        """
        GitHubAuthService 초기화
        
        Args:
            config_manager: 설정 관리자 인스턴스
        """
        self.config_manager = config_manager or ConfigManager()
        self.session = requests.Session()
        self.session.headers.update({
            "Accept": "application/json",
            "User-Agent": "Neo-Batch-Translator/1.0"
        })
    
    def get_device_code(self) -> DeviceCodeResponse:
        """
        GitHub Device Code를 요청합니다.
        
        Returns:
            DeviceCodeResponse: 디바이스 코드 응답 데이터
            
        Raises:
            GitHubAuthException: 디바이스 코드 요청 실패 시
        """
        try:
            payload = {
                "client_id": GITHUB_COPILOT_CLIENT_ID,
                "scope": GITHUB_COPILOT_SCOPE
            }
            
            logger.info("GitHub Device Code 요청 중...")
            response = self.session.post(DEVICE_CODE_URL, data=payload, timeout=30)
            response.raise_for_status()
            
            data = response.json()
            logger.info(f"Device Code 요청 성공: user_code={data.get('user_code')}")
            
            return DeviceCodeResponse(
                device_code=data["device_code"],
                user_code=data["user_code"],
                verification_uri=data["verification_uri"],
                expires_in=data["expires_in"],
                interval=data["interval"]
            )
        except requests.RequestException as e:
            error_msg = f"GitHub Device Code 요청 실패: {e}"
            logger.error(error_msg)
            raise GitHubAuthException(error_msg) from e
        except KeyError as e:
            error_msg = f"GitHub Device Code 응답 파싱 실패: {e}"
            logger.error(error_msg)
            raise GitHubAuthException(error_msg) from e
    
    def poll_for_access_token(
        self, 
        device_code: str, 
        interval: int,
        timeout: Optional[int] = 300
    ) -> AccessTokenResponse:
        """
        Access Token을 위해 주기적으로 폴링합니다.
        
        Args:
            device_code: 디바이스 코드
            interval: 폴링 간격 (초)
            timeout: 최대 대기 시간 (초), None이면 무제한
            
        Returns:
            AccessTokenResponse: 액세스 토큰 응답 데이터
            
        Raises:
            GitHubAuthException: 토큰 획득 실패 시
        """
        payload = {
            "client_id": GITHUB_COPILOT_CLIENT_ID,
            "device_code": device_code,
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code"
        }
        
        start_time = time.time()
        logger.info(f"Access Token 폴링 시작 (간격: {interval}초)")
        
        while True:
            try:
                if timeout and (time.time() - start_time) > timeout:
                    raise GitHubAuthException(f"토큰 획득 시간 초과 ({timeout}초)")
                
                response = self.session.post(ACCESS_TOKEN_URL, data=payload, timeout=30)
                response.raise_for_status()
                
                data = response.json()
                
                if "access_token" in data:
                    logger.info("Access Token 획득 성공")
                    return AccessTokenResponse(
                        access_token=data["access_token"],
                        token_type=data.get("token_type", "bearer"),
                        scope=data.get("scope", "")
                    )
                elif data.get("error") == "authorization_pending":
                    logger.debug("사용자 인증 대기 중...")
                    time.sleep(interval)
                    continue
                elif data.get("error") == "slow_down":
                    # GitHub가 요청 속도를 줄이라고 요청
                    interval += 5
                    logger.warning(f"폴링 속도 조정: {interval}초")
                    time.sleep(interval)
                    continue
                elif data.get("error") == "expired_token":
                    raise GitHubAuthException("디바이스 코드가 만료되었습니다.")
                elif data.get("error") == "access_denied":
                    raise GitHubAuthException("사용자가 액세스를 거부했습니다.")
                else:
                    error_msg = data.get("error_description", data.get("error", "알 수 없는 오류"))
                    raise GitHubAuthException(f"토큰 획득 실패: {error_msg}")
                    
            except requests.RequestException as e:
                error_msg = f"토큰 폴링 요청 실패: {e}"
                logger.error(error_msg)
                raise GitHubAuthException(error_msg) from e
    
    def save_access_token(self, access_token: str) -> bool:
        """
        획득한 Access Token을 config.json에 저장합니다.
        
        Args:
            access_token: 저장할 액세스 토큰
            
        Returns:
            bool: 저장 성공 여부
        """
        try:
            copilot_config = {
                "github_copilot_enabled": True,
                "github_copilot_access_token": access_token
            }
            
            success = self.config_manager.update_github_copilot_config(copilot_config)
            if success:
                logger.info("GitHub Copilot 액세스 토큰이 config.json에 저장되었습니다.")
            else:
                logger.error("GitHub Copilot 액세스 토큰 저장 실패")
            
            return success
        except Exception as e:
            logger.error(f"토큰 저장 중 오류 발생: {e}")
            return False
    
    def verify_token(self, access_token: Optional[str] = None) -> bool:
        """
        GitHub Copilot 액세스 토큰의 유효성을 확인합니다.
        
        Args:
            access_token: 확인할 토큰. None이면 설정에서 가져옴
            
        Returns:
            bool: 토큰 유효성 여부
        """
        if access_token is None:
            config = self.config_manager.get_github_copilot_config()
            access_token = config.get("github_copilot_access_token", "")
        
        if not access_token:
            logger.warning("확인할 액세스 토큰이 없습니다.")
            return False
        
        try:
            # GitHub API로 토큰 검증
            headers = {
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/vnd.github.v3+json"
            }
            
            response = self.session.get("https://api.github.com/user", headers=headers, timeout=30)
            
            if response.status_code == 200:
                user_data = response.json()
                logger.info(f"토큰 유효성 확인 성공: 사용자 {user_data.get('login', 'unknown')}")
                return True
            elif response.status_code == 401:
                logger.warning("액세스 토큰이 유효하지 않습니다.")
                return False
            else:
                logger.error(f"토큰 검증 실패: HTTP {response.status_code}")
                return False
                
        except requests.RequestException as e:
            logger.error(f"토큰 검증 요청 실패: {e}")
            return False
    
    def get_user_info(self, access_token: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """
        GitHub 사용자 정보를 가져옵니다.
        
        Args:
            access_token: 사용할 토큰. None이면 설정에서 가져옴
            
        Returns:
            Dict[str, Any]: 사용자 정보 또는 None
        """
        if access_token is None:
            config = self.config_manager.get_github_copilot_config()
            access_token = config.get("github_copilot_access_token", "")
        
        if not access_token:
            return None
        
        try:
            headers = {
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/vnd.github.v3+json"
            }
            
            response = self.session.get("https://api.github.com/user", headers=headers, timeout=30)
            response.raise_for_status()
            
            return response.json()
            
        except requests.RequestException as e:
            logger.error(f"사용자 정보 요청 실패: {e}")
            return None
    
    def revoke_token(self, access_token: Optional[str] = None) -> bool:
        """
        GitHub 액세스 토큰을 취소합니다.
        
        Args:
            access_token: 취소할 토큰. None이면 설정에서 가져옴
            
        Returns:
            bool: 취소 성공 여부
        """
        if access_token is None:
            config = self.config_manager.get_github_copilot_config()
            access_token = config.get("github_copilot_access_token", "")
        
        if not access_token:
            logger.warning("취소할 액세스 토큰이 없습니다.")
            return False
        
        try:
            # GitHub App 토큰 취소 엔드포인트
            auth = (GITHUB_COPILOT_CLIENT_ID, access_token)
            response = self.session.delete(
                f"https://api.github.com/applications/{GITHUB_COPILOT_CLIENT_ID}/grant",
                auth=auth,
                timeout=30
            )
            
            if response.status_code in [204, 404]:  # 204: 성공, 404: 이미 취소됨
                logger.info("GitHub 액세스 토큰이 성공적으로 취소되었습니다.")
                
                # 설정에서도 토큰 제거
                copilot_config = {
                    "github_copilot_enabled": False,
                    "github_copilot_access_token": ""
                }
                self.config_manager.update_github_copilot_config(copilot_config)
                
                return True
            else:
                logger.error(f"토큰 취소 실패: HTTP {response.status_code}")
                return False
                
        except requests.RequestException as e:
            logger.error(f"토큰 취소 요청 실패: {e}")
            return False

def authenticate_github_copilot(config_manager: Optional[ConfigManager] = None) -> Tuple[bool, str]:
    """
    GitHub Copilot 인증을 수행하는 편의 함수
    
    Args:
        config_manager: 설정 관리자 인스턴스
        
    Returns:
        Tuple[bool, str]: (성공 여부, 메시지)
    """
    auth_service = GitHubAuthService(config_manager)
    
    try:
        # 1. Device Code 요청
        device_response = auth_service.get_device_code()
        
        print(f"\n=== GitHub Copilot 인증 ===")
        print(f"브라우저에서 다음 URL을 열어주세요: {device_response.verification_uri}")
        print(f"인증 코드를 입력하세요: {device_response.user_code}")
        print(f"만료 시간: {device_response.expires_in}초")
        print("인증 완료를 기다리는 중...")
        
        # 2. Access Token 폴링
        token_response = auth_service.poll_for_access_token(
            device_response.device_code,
            device_response.interval,
            timeout=device_response.expires_in
        )
        
        # 3. 토큰 저장
        if auth_service.save_access_token(token_response.access_token):
            # 4. 사용자 정보 확인
            user_info = auth_service.get_user_info(token_response.access_token)
            username = user_info.get("login", "unknown") if user_info else "unknown"
            
            success_msg = f"GitHub Copilot 인증 성공! 사용자: {username}"
            print(f"\n✅ {success_msg}")
            return True, success_msg
        else:
            error_msg = "토큰 저장 실패"
            print(f"\n❌ {error_msg}")
            return False, error_msg
    
    except GitHubAuthException as e:
        error_msg = f"GitHub Copilot 인증 실패: {e}"
        print(f"\n❌ {error_msg}")
        return False, error_msg
    except Exception as e:
        error_msg = f"예상치 못한 오류: {e}"
        print(f"\n❌ {error_msg}")
        logger.error("인증 중 예상치 못한 오류:", exc_info=True)
        return False, error_msg

if __name__ == "__main__":
    # 테스트 코드
    print("GitHub Auth Service 테스트 시작...")
    
    # ConfigManager 테스트
    try:
        config_manager = ConfigManager()
        auth_service = GitHubAuthService(config_manager)
        
        print("✅ GitHubAuthService 인스턴스 생성 성공")
        
        # 현재 설정된 토큰 확인
        config = config_manager.get_github_copilot_config()
        current_token = config.get("github_copilot_access_token", "")
        
        if current_token:
            print(f"현재 토큰: {current_token[:10]}...")
            is_valid = auth_service.verify_token(current_token)
            print(f"토큰 유효성: {is_valid}")
            
            if is_valid:
                user_info = auth_service.get_user_info(current_token)
                if user_info:
                    print(f"사용자: {user_info.get('login', 'unknown')}")
        else:
            print("설정된 토큰이 없습니다.")
        
    except Exception as e:
        print(f"❌ 테스트 실패: {e}")
        
    print("GitHub Auth Service 테스트 종료.")
