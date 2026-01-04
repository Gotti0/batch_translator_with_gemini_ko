#!/usr/bin/env python3
"""
app_service.py에서 레거시 동기 메서드를 안전하게 제거하는 스크립트
- start_translation() 메서드 제거
- _translation_task() 메서드 제거  
- stop_translation() 메서드 제거
- request_stop_translation() 메서드 업데이트
"""

import ast
import sys
from pathlib import Path

def remove_sync_methods():
    app_service_path = Path(__file__).parent.parent / "app" / "app_service.py"
    
    # 파일 읽기
    with open(app_service_path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # AST 파싱
    try:
        tree = ast.parse(content)
    except SyntaxError as e:
        print(f"파일 파싱 실패: {e}")
        sys.exit(1)
    
    # 메서드 찾기
    class_def = None
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "AppService":
            class_def = node
            break
    
    if not class_def:
        print("AppService 클래스를 찾을 수 없습니다")
        sys.exit(1)
    
    # 제거할 메서드 찾기
    methods_to_remove = ['start_translation', '_translation_task', 'stop_translation']
    line_ranges = []
    
    for method in class_def.body:
        if isinstance(method, ast.FunctionDef) and method.name in methods_to_remove:
            # 메서드의 시작과 끝 라인 저장
            start_line = method.lineno - 1  # 0-indexed
            end_line = method.end_lineno  # inclusive
            line_ranges.append((method.name, start_line, end_line))
            print(f"메서드 '{method.name}' 발견: L{start_line+1}-{end_line}")
    
    if not line_ranges:
        print("제거할 메서드를 찾을 수 없습니다")
        sys.exit(1)
    
    # 역순으로 정렬 (뒤에서부터 제거해야 라인 번호가 안 밀림)
    line_ranges.sort(key=lambda x: x[1], reverse=True)
    
    # 라인별로 분할
    lines = content.splitlines(keepends=True)
    
    # 메서드 제거
    for method_name, start_line, end_line in line_ranges:
        # end_line은 inclusive이므로 +1 필요
        del lines[start_line:end_line]
    
    # 주석 추가
    insert_pos = None
    for i, line in enumerate(lines):
        if "# ===== 끝: 비동기 메서드 =====" in line:
            insert_pos = i + 1
            break
    
    if insert_pos:
        comment = """
    # === LEGACY SYNC METHODS REMOVED ===
    # 다음 메서드들은 비동기 마이그레이션으로 인해 제거되었습니다:
    # - start_translation() (구 L1304-L1360)
    # - _translation_task() (구 L1363-L1756)
    # - stop_translation() (구 L1757-L1775)
    # 대신 start_translation_async()를 사용하세요.
    # CLI: asyncio.run(app_service.start_translation_async(...))
    # GUI (PySide6): await app_service.start_translation_async(...) with @asyncSlot

"""
        lines.insert(insert_pos, comment)
    
    # 파일에 저장
    with open(app_service_path, 'w', encoding='utf-8') as f:
        f.writelines(lines)
    
    print(f"✅ 성공적으로 레거시 메서드를 제거했습니다: {app_service_path}")
    return True

if __name__ == "__main__":
    remove_sync_methods()
