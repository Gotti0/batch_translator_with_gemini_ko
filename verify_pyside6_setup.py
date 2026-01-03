#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
PySide6 및 qasync 설치 검증 스크립트
설치된 라이브러리 버전 확인 및 기본 동작 테스트
"""

import sys
import importlib


def check_version(module_name, display_name=None):
    """라이브러리 버전 확인"""
    if display_name is None:
        display_name = module_name
    
    try:
        module = importlib.import_module(module_name)
        version = getattr(module, '__version__', 'unknown')
        print("[OK] {0:20s} v{1}".format(display_name, version))
        return True
    except ImportError as e:
        print("[NG] {0:20s} 설치 안 됨 ({1})".format(display_name, e))
        return False


def main():
    print("=" * 60)
    print("PySide6 + qasync Installation Verification")
    print("=" * 60)
    
    # 필수 라이브러리 확인
    print("\n[Required Libraries]")
    results = []
    results.append(check_version("PySide6", "PySide6"))
    results.append(check_version("qasync", "qasync"))
    results.append(check_version("aiofiles", "aiofiles"))
    results.append(check_version("google", "google-genai"))
    
    # 기존 라이브러리 확인
    print("\n[Existing Libraries]")
    results.append(check_version("tqdm", "tqdm"))
    results.append(check_version("dotenv", "python-dotenv"))
    results.append(check_version("concurrent_log_handler", "concurrent-log-handler"))
    
    # PySide6 상세 검증
    print("\n[PySide6 Module Verification]")
    try:
        from PySide6.QtWidgets import QApplication, QMainWindow, QPushButton
        from PySide6.QtCore import Qt
        print("[OK] PySide6.QtWidgets      loaded")
        print("[OK] PySide6.QtCore         loaded")
    except ImportError as e:
        print("[NG] PySide6 module load failed: {0}".format(e))
        return 1
    
    # qasync 상세 검증
    print("\n[qasync Module Verification]")
    try:
        from qasync import QEventLoop, asyncSlot
        print("[OK] qasync.QEventLoop      loaded")
        print("[OK] qasync.asyncSlot       loaded")
    except ImportError as e:
        print("[NG] qasync module load failed: {0}".format(e))
        return 1
    
    # asyncio 검증
    print("\n[asyncio Verification]")
    try:
        import asyncio
        print("[OK] asyncio                v{0}".format(sys.version.split()[0]))
    except ImportError as e:
        print("[NG] asyncio load failed: {0}".format(e))
        return 1
    
    # 최종 결과
    print("\n" + "=" * 60)
    if all(results):
        print("[SUCCESS] All libraries installed!")
        print("[NEXT] Phase 1-4: Create minimal PySide6 app prototype")
        print("=" * 60)
        return 0
    else:
        print("[FAILED] Some libraries installation failed")
        print("=" * 60)
        return 1


if __name__ == "__main__":
    sys.exit(main())
