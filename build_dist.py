#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Neo Batch Translator (BTG) - 배포 파일 빌드 및 패키징 스크립트

사용법:
    python build_dist.py [--no-clean] [--no-zip]
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
SPEC_FILE = PROJECT_ROOT / "배치_번역기.spec"
DIST_DIR = PROJECT_ROOT / "dist"
BUILD_DIR = PROJECT_ROOT / "build"
PACKAGE_DIR = DIST_DIR / "Batch_Translator"
ZIP_OUTPUT = DIST_DIR / "Batch_Translator.zip"


def run_build(clean: bool = True, create_zip: bool = True) -> int:
    """PyInstaller 빌드를 실행하고 배포 zip 파일을 생성합니다."""
    print("=" * 60)
    print(" [BTG] 배치 번역기 배포 파일 빌드 시작")
    print(f" - 프로젝트 경로: {PROJECT_ROOT}")
    print(f" - Spec 파일: {SPEC_FILE.name}")
    print("=" * 60)

    if not SPEC_FILE.exists():
        print(f"[오류] Spec 파일을 찾을 수 없습니다: {SPEC_FILE}")
        return 1

    # 1. 이전 빌드 정리
    if clean:
        print("\n[1/3] 이전 빌드 잔여물 정리 중...")
        if BUILD_DIR.exists():
            shutil.rmtree(BUILD_DIR, ignore_errors=True)
            print(f" - 삭제됨: {BUILD_DIR}")
        if PACKAGE_DIR.exists():
            shutil.rmtree(PACKAGE_DIR, ignore_errors=True)
            print(f" - 삭제됨: {PACKAGE_DIR}")
    else:
        print("\n[1/3] 이전 빌드 정리 건너뜀 (--no-clean)")

    # 2. PyInstaller 실행
    print("\n[2/3] PyInstaller 빌드 실행 중...")
    pyinstaller_cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        str(SPEC_FILE),
        "--noconfirm",
    ]
    if clean:
        pyinstaller_cmd.append("--clean")

    res = subprocess.run(pyinstaller_cmd, cwd=str(PROJECT_ROOT))
    if res.returncode != 0:
        print(f"\n[오류] PyInstaller 빌드가 실패했습니다 (코드: {res.returncode})")
        return res.returncode

    # 빌드된 디렉터리 내 불필요한 런타임 파일(logs 등) 정리
    logs_dir = PACKAGE_DIR / "logs"
    if logs_dir.exists():
        shutil.rmtree(logs_dir, ignore_errors=True)

    # 3. 배포 zip 파일 생성
    if create_zip:
        print("\n[3/3] 배포용 ZIP 압축 파일 생성 중...")
        if ZIP_OUTPUT.exists():
            ZIP_OUTPUT.unlink()

        archived_count = 0
        with zipfile.ZipFile(ZIP_OUTPUT, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zipf:
            for file_path in sorted(PACKAGE_DIR.rglob("*")):
                if file_path.is_file() and "logs" not in file_path.parts:
                    arcname = file_path.relative_to(PACKAGE_DIR)
                    zipf.write(file_path, arcname)
                    archived_count += 1

        zip_size_mb = ZIP_OUTPUT.stat().st_size / (1024 * 1024)
        print(f" - 압축 완료: {ZIP_OUTPUT.name} ({archived_count}개 파일, {zip_size_mb:.2f} MB)")
    else:
        print("\n[3/3] ZIP 생성 건너뜀 (--no-zip)")

    print("\n" + "=" * 60)
    print(" [성공] 배포 파일 생성이 완료되었습니다!")
    print(f" - 실행 파일 폴더: {PACKAGE_DIR}")
    print(f" - 배포 압축 파일: {ZIP_OUTPUT}")
    print("=" * 60)
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="배치 번역기 배포 패키지 빌더")
    parser.add_argument("--no-clean", action="store_true", help="이전 build/dist 디렉터리를 삭제하지 않음")
    parser.add_argument("--no-zip", action="store_true", help="배포 zip 압축 파일을 생성하지 않음")
    args = parser.parse_args()

    code = run_build(clean=not args.no_clean, create_zip=not args.no_zip)
    sys.exit(code)


if __name__ == "__main__":
    main()
