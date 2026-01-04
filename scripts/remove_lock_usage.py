#!/usr/bin/env python3
"""
app_service.py에서 Lock 관련 코드를 제거하는 스크립트
asyncio 단일 스레드 환경에서는 Lock이 불필요함
"""

import re
from pathlib import Path

def remove_lock_usage():
    app_service_path = Path(__file__).parent.parent / "app" / "app_service.py"
    
    with open(app_service_path, 'r', encoding='utf-8') as f:
        lines = f.readlines()
    
    # with self._XXX_lock: 패턴 찾기
    lock_patterns = [
        r'^\s+with self\._file_write_lock:',
        r'^\s+with self\._progress_lock:',
        r'^\s+with self\._translation_lock:'
    ]
    
    new_lines = []
    i = 0
    removed_count = 0
    
    while i < len(lines):
        line = lines[i]
        
        # Lock 패턴 매칭
        is_lock_line = any(re.match(pattern, line) for pattern in lock_patterns)
        
        if is_lock_line:
            # with 블록 제거
            # with 라인 바로 다음부터 들여쓰기된 코드를 찾아 들여쓰기 제거
            base_indent = len(line) - len(line.lstrip())
            removed_count += 1
            
            # with 라인 스킵
            i += 1
            
            # 이제 들여쓰기된 코드를 찾아서 들여쓰기 제거
            while i < len(lines):
                current_line = lines[i]
                
                # 빈 줄이면 그냥 추가
                if current_line.strip() == '':
                    new_lines.append(current_line)
                    i += 1
                    continue
                
                # 들여쓰기 확인
                current_indent = len(current_line) - len(current_line.lstrip())
                
                # with 블록 안의 코드인지 확인 (base_indent + 4가 블록 안의 들여쓰기)
                if current_indent > base_indent:
                    # 4칸 제거하고 추가
                    dedented_line = ' ' * (current_indent - 4) + current_line.lstrip()
                    new_lines.append(dedented_line)
                    i += 1
                else:
                    # with 블록이 끝남
                    break
        else:
            new_lines.append(line)
            i += 1
    
    # 파일 저장
    with open(app_service_path, 'w', encoding='utf-8') as f:
        f.writelines(new_lines)
    
    print(f"✅ {removed_count}개의 Lock 사용처 제거 완료")
    return True

if __name__ == "__main__":
    remove_lock_usage()
