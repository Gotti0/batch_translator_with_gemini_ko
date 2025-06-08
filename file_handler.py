# file_handler.py
import os
import json
import csv
import hashlib
import time
from pathlib import Path
from typing import List, Dict, Any, Union, Tuple
import re
import logging # logging 모듈 임포트

# 이 파일 내에서 로거를 사용하기 위해 설정
# 다른 모듈에서 이미 logger_config.setup_logger를 통해 설정했다면,
# 여기서는 logging.getLogger(__name__)만 사용해도 됩니다.
# 단독 실행 테스트를 위해 로거를 직접 설정할 수도 있습니다.
try:
    from .logger_config import setup_logger
except ImportError:
    from logger_config import setup_logger # 현재 디렉토리에서 찾기 (단독 실행 시)

logger = setup_logger(__name__) # 이 파일용 로거 인스턴스 생성

def read_text_file(file_path: Union[str, Path]) -> str:
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
        return content
    except FileNotFoundError:
        logger.error(f"파일을 찾을 수 없습니다: {file_path}")
        raise
    except IOError as e:
        logger.error(f"파일 읽기 중 오류 발생 ({file_path}): {e}")
        raise

# --- 일반 파일 처리 ---

def write_text_file(file_path: Union[str, Path], content: str, mode: str = 'w') -> None:
    try:
        ensure_dir_exists(Path(file_path).parent)
        with open(file_path, mode, encoding='utf-8') as f:
            f.write(content)
    except IOError as e:
        logger.error(f"파일 쓰기 중 오류 발생 ({file_path}): {e}")
        raise

def append_to_text_file(file_path: Union[str, Path], content: str) -> None:
    write_text_file(file_path, content, mode='a')

# --- 청크 관련 파일 처리 ---

def save_chunk_with_index_to_file(output_path: Union[str, Path], index: int, chunk_content: str) -> None:
    formatted_content = f"##CHUNK_INDEX: {index}##\n{chunk_content}\n##END_CHUNK##\n\n"
    try:
        append_to_text_file(output_path, formatted_content)
    except IOError as e:
        logger.error(f"청크 파일 저장 중 오류 ({output_path}, 인덱스: {index}): {e}")
        raise

def load_chunks_from_file(file_path: Union[str, Path]) -> Dict[int, str]:
    chunks: Dict[int, str] = {}
    if not Path(file_path).exists():
        return chunks
    try:
        content = read_text_file(file_path)
        pattern = r"##CHUNK_INDEX: (\d+)##\n(.*?)\n##END_CHUNK##"
        matches = re.findall(pattern, content, re.DOTALL)
        for match_tuple in matches: 
            try:
                index_str, chunk_text = match_tuple 
                index = int(index_str)
                chunks[index] = chunk_text
            except ValueError:
                logger.warning(f"청크 인덱스 파싱 중 오류 ({file_path}): 인덱스 '{match_tuple[0]}'가 숫자가 아닙니다.")
                continue
            except IndexError: 
                logger.warning(f"정규식 매칭 결과 처리 중 오류 ({file_path}): 매치된 그룹이 부족합니다 - {match_tuple}")
                continue
        return chunks
    except IOError as e:
        logger.error(f"청크 파일 로드 중 오류 ({file_path}): {e}")
        raise
    except Exception as e: 
        logger.error(f"청크 파일 로드 중 알 수 없는 오류 ({file_path}): {e}", exc_info=True)
        raise

def save_merged_chunks_to_file(output_path: Union[str, Path], merged_chunks: Dict[int, str]) -> None:
    try:
        ensure_dir_exists(Path(output_path).parent)
        with open(output_path, 'w', encoding='utf-8') as f:
            sorted_indices = sorted(merged_chunks.keys())
            for idx in sorted_indices:
                chunk_content = merged_chunks[idx]
                formatted_content = f"##CHUNK_INDEX: {idx}##\n{chunk_content}\n##END_CHUNK##\n\n"
                f.write(formatted_content)
    except IOError as e:
        logger.error(f"병합된 청크 저장 중 오류 ({output_path}): {e}")
        raise

# --- JSON 파일 처리 ---

def read_json_file(file_path: Union[str, Path]) -> Any: # Return type changed to Any
    if not Path(file_path).exists():
        return {}
    try:
        with open(file_path, 'r', encoding='utf-8') as f: 
            content = f.read()
            if not content.strip():
                return {}
            data = json.loads(content)
        return data
    except FileNotFoundError: # 이미 위에서 체크했지만, 만약을 위해
        return {}
    except json.JSONDecodeError as e:
        logger.error(f"JSON 파싱 오류 ({file_path}): {e}")
        raise
    except IOError as e:
        logger.error(f"JSON 파일 읽기 중 오류 발생 ({file_path}): {e}")
        raise

def write_json_file(file_path: Union[str, Path], data: Any, indent: int = 4) -> None: # data type changed to Any
    try:
        ensure_dir_exists(Path(file_path).parent)
        with open(file_path, 'w', encoding='utf-8') as f: 
            json.dump(data, f, ensure_ascii=False, indent=indent)
    except IOError as e:
        logger.error(f"JSON 파일 쓰기 중 오류 발생 ({file_path}): {e}")
        raise

# --- CSV 파일 처리 ---
# PRONOUN_CSV_HEADER is removed as lorebooks use JSON. load_pronouns_from_csv is also removed.
def read_csv_file(file_path: Union[str, Path]) -> List[List[str]]:
    if not Path(file_path).exists():
        return []
    try:
        # encoding='utf-8-sig'를 사용하여 UTF-8 BOM을 자동으로 처리
        with open(file_path, 'r', encoding='utf-8-sig', newline='') as f:
            reader = csv.reader(f)
            data = list(reader)
        return data
    except FileNotFoundError: # 이 경우는 위에서 이미 처리됨
        return []
    except IOError as e:
        logger.error(f"CSV 파일 읽기 중 오류 발생 ({file_path}): {e}")
        raise
    except Exception as e: # 그 외 CSV 파싱 관련 예외 등
        logger.error(f"CSV 파일 읽기 중 예상치 못한 오류 ({file_path}): {e}", exc_info=True)
        raise


def write_csv_file(file_path: Union[str, Path], data: List[List[str]], header: List[str] = None) -> None:
    try:
        ensure_dir_exists(Path(file_path).parent)
        with open(file_path, 'w', encoding='utf-8', newline='') as f: 
            writer = csv.writer(f)
            if header:
                writer.writerow(header)
            writer.writerows(data)
    except IOError as e:
        logger.error(f"CSV 파일 쓰기 중 오류 발생 ({file_path}): {e}")
        raise

# --- 메타데이터 파일 처리 함수 ---
def get_metadata_file_path(input_file_path: Union[str, Path]) -> Path:
    p = Path(input_file_path)
    
    # 이미 _metadata.json으로 끝나는 경우 그대로 반환
    if p.name.endswith('_metadata.json'):
        return p
    
    # _metadata 부분이 있는지 확인 후 제거
    stem = p.stem
    if stem.endswith('_metadata'):
        stem = stem[:-9]  # '_metadata' 제거
    
    return p.with_name(f"{stem}_metadata.json")


def load_metadata(input_file_path: Union[str, Path]) -> Dict[str, Any]:
    metadata_path = get_metadata_file_path(input_file_path)
    try:
        return read_json_file(metadata_path)
    except Exception as e:
        logger.warning(f"메타데이터 로드 실패 ({metadata_path}): {e}. 새 메타데이터를 생성합니다.")
        return {}

def save_metadata(input_file_path: Union[str, Path], metadata: Dict[str, Any]) -> None:
    metadata_path = get_metadata_file_path(input_file_path)
    try:
        write_json_file(metadata_path, metadata)
    except Exception as e:
        logger.error(f"메타데이터 저장 실패 ({metadata_path}): {e}", exc_info=True)

def _hash_config_for_metadata(config: Dict[str, Any]) -> str:
    config_copy = config.copy()
    config_copy.pop('api_key', None) 
    config_copy.pop('api_keys', None) 
    config_str = json.dumps(config_copy, sort_keys=True, ensure_ascii=False)
    return hashlib.md5(config_str.encode('utf-8')).hexdigest()

def create_new_metadata(input_file_path: Union[str, Path], total_chunks: int, config: Dict[str, Any]) -> Dict[str, Any]:
    current_time = time.time()
    metadata = {
        "input_file": str(input_file_path),
        "total_chunks": total_chunks,
        "translated_chunks": {}, 
        "config_hash": _hash_config_for_metadata(config),
        "creation_time": current_time,
        "last_updated": current_time,
        "status": "initialized", 
    }
    return metadata

def update_metadata_for_chunk_completion(input_file_path: Union[str, Path], chunk_index: int) -> bool:
    # 입력이 이미 메타데이터 파일인지 확인
    p = Path(input_file_path)
    if p.name.endswith('_metadata.json'):
        metadata_path = p
    else:
        metadata_path = get_metadata_file_path(input_file_path)
    
    metadata_path = get_metadata_file_path(input_file_path)
    try:
        metadata = read_json_file(metadata_path)
        if not metadata: 
            logger.error(f"메타데이터 파일이 존재하지 않아 청크 완료를 업데이트할 수 없습니다: {metadata_path}")
            return False

        metadata["translated_chunks"][str(chunk_index)] = time.time()
        metadata["last_updated"] = time.time()
        if len(metadata["translated_chunks"]) == metadata.get("total_chunks", -1): 
            metadata["status"] = "completed"
        else:
            metadata["status"] = "in_progress"

        write_json_file(metadata_path, metadata)
        return True
    except Exception as e:
        logger.error(f"메타데이터 청크 완료 업데이트 중 오류 ({metadata_path}): {e}", exc_info=True)
        return False

# --- 유틸리티 함수 ---
def ensure_dir_exists(dir_path: Union[str, Path]) -> None:
    Path(dir_path).mkdir(parents=True, exist_ok=True)

def delete_file(file_path: Union[str, Path]) -> bool:
    try:
        Path(file_path).unlink(missing_ok=True) 
        return True
    except OSError as e: 
        logger.warning(f"파일 삭제 중 오류 ({file_path}): {e}")
        return False

if __name__ == '__main__':
    logger = setup_logger("file_handler_main_test", log_level=logging.DEBUG, log_to_console=True, log_to_file=False)

    test_dir = Path("test_file_handler_output")
    ensure_dir_exists(test_dir)

    sample_input_file = test_dir / "my_novel.txt"
    write_text_file(sample_input_file, "이것은 테스트 소설입니다.")

    logger.info("\n--- 메타데이터 테스트 ---")
    initial_config = {"model_name": "gemini-pro", "temperature": 0.7}
    delete_file(get_metadata_file_path(sample_input_file))
    new_meta = create_new_metadata(sample_input_file, 10, initial_config)
    save_metadata(sample_input_file, new_meta)
    logger.info(f"새 메타데이터 저장됨: {get_metadata_file_path(sample_input_file)}")
    loaded_meta = load_metadata(sample_input_file)
    logger.info(f"로드된 메타데이터: {loaded_meta}")
    
    if loaded_meta:
        update_metadata_for_chunk_completion(sample_input_file, 0)
        update_metadata_for_chunk_completion(sample_input_file, 1)
        updated_meta = load_metadata(sample_input_file)
        logger.info(f"청크 완료 후 메타데이터: {updated_meta}")
        changed_config = {"model_name": "gemini-flash", "temperature": 0.8}
        current_config_hash = _hash_config_for_metadata(changed_config)
        if updated_meta and updated_meta.get("config_hash") != current_config_hash:
            logger.info("설정이 변경되었습니다. 이전 진행 상황을 이어받을지 확인 필요.")
        elif updated_meta:
            logger.info("설정이 동일합니다.")
        else:
            logger.warning("메타데이터 로드에 실패하여 설정 변경을 확인할 수 없습니다.")

    logger.info("\n--- 일반 파일 테스트 ---")
    txt_file = test_dir / "sample.txt"
    delete_file(txt_file)
    write_text_file(txt_file, "안녕하세요, file_handler 테스트입니다.\n")
    append_to_text_file(txt_file, "이것은 추가된 줄입니다.\n")
    content = read_text_file(txt_file)
    logger.info(f"텍스트 파일 내용:\n{content}")

    logger.info("\n--- 청크 파일 테스트 ---")
    chunk_output_file = test_dir / "chunks_output.txt"
    if chunk_output_file.exists():
        delete_file(chunk_output_file)

    save_chunk_with_index_to_file(chunk_output_file, 0, "첫 번째 번역된 청크입니다.")
    save_chunk_with_index_to_file(chunk_output_file, 1, "두 번째 번역된 청크 내용입니다.")
    save_chunk_with_index_to_file(chunk_output_file, 2, "세 번째 청크입니다.\n여러 줄을 가질 수 있습니다.")
    
    logger.info(f"청크 파일 저장됨: {chunk_output_file}")
    
    loaded_chunks = load_chunks_from_file(chunk_output_file)
    logger.info(f"로드된 청크: {loaded_chunks}")

    logger.info("\n--- JSON 파일 테스트 (로어북 예시) ---")
    lorebook_file = test_dir / "my_novel_lorebook.json"
    sample_lorebook_data = [{"keyword": "엘리제", "description": "마법 왕국의 공주", "category": "인물"}, {"keyword": "아르카나 스톤", "description": "고대 유물", "category": "아이템"}]
    write_json_file(lorebook_file, sample_lorebook_data)
    loaded_lorebook = read_json_file(lorebook_file)
    logger.info(f"로드된 로어북 데이터: {loaded_lorebook}")
    assert loaded_lorebook == sample_lorebook_data

    logger.info(f"\n테스트 완료. 결과는 '{test_dir}' 디렉토리에서 확인할 수 있습니다.")
    # import shutil
    # shutil.rmtree(test_dir)
    # logger.info(f"테스트 디렉토리 '{test_dir}' 삭제됨.")
