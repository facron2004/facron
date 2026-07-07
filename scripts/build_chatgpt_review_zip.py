"""打包 Review Crawler 源码到 chatgpt_review.zip。

只打包审查需要的文件：源码、配置、文档、测试、模板、静态资源、迁移。
排除：__pycache__、.git、.venv、运行时 profile、SQLite/导出、日志、Redis 二进制。
"""
from __future__ import annotations

import os
import zipfile
from pathlib import Path

ROOT = Path(r"E:\Program\Review Crawler")
OUTPUT = ROOT / "chatgpt_review.zip"

# 顶层排除（目录或文件），命中即整棵跳过
TOP_LEVEL_EXCLUDE = {
    "__pycache__",
    ".git",
    ".venv",
    ".pytest_cache",
    ".claude",
    ".tmall-profile",
    ".tmall-profile-clone",
    ".workbuddy",
    "data",
    "outputs",
    "logs",
    "debug",
    "tools",          # 含 Windows Redis 二进制，非审查范围
    "start.log",      # 运行日志
    "start.vbs",      # Windows 启动脚本（可省略，保留以备参考则需另开）
    "start_hidden.cmd",
    "service_install.bat",
    "service_check.bat",
    "stop.bat",
    "start.bat",
    "review_scraper.egg-info",
    "chatgpt_review.zip",
    "CHATGPT_REVIEW_README.md",  # 单独放到 zip 根
}

# 文件名级别排除
FILE_EXCLUDE = {
    "PKG-INFO",
    "SOURCES.txt",
    "dependency_links.txt",
    "requires.txt",
    "top_level.txt",
}

# 扩展名排除
EXT_EXCLUDE = {".pyc", ".pyo", ".pdb", ".db", ".bak"}

# 解压大小上限：单文件 5MB，整个 zip 80MB（留余量）
MAX_FILE_SIZE = 5 * 1024 * 1024
MAX_TOTAL_SIZE = 80 * 1024 * 1024


def should_skip_dir(name: str) -> bool:
    return name in TOP_LEVEL_EXCLUDE or name.startswith("__pycache__")


def is_under_excluded_top(rel_parts: tuple[str, ...]) -> bool:
    return rel_parts[0] in TOP_LEVEL_EXCLUDE if rel_parts else False


def collect_files() -> list[Path]:
    files: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(ROOT):
        # 排序以便 zip 内顺序稳定
        dirnames.sort()
        filenames.sort()
        # 原地剪枝
        dirnames[:] = [d for d in dirnames if not should_skip_dir(d)]
        for fname in filenames:
            if fname in FILE_EXCLUDE:
                continue
            ext = os.path.splitext(fname)[1].lower()
            if ext in EXT_EXCLUDE:
                continue
            full = Path(dirpath) / fname
            try:
                if full.stat().st_size > MAX_FILE_SIZE:
                    print(f"[skip-too-large] {full}")
                    continue
            except OSError:
                continue
            files.append(full)
    return files


def main() -> None:
    if OUTPUT.exists():
        OUTPUT.unlink()

    files = collect_files()
    total = 0
    included = 0
    with zipfile.ZipFile(OUTPUT, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for full in files:
            rel = full.relative_to(ROOT)
            parts = rel.parts
            if not parts:
                continue
            if is_under_excluded_top(parts):
                continue
            zf.write(full, arcname=str(rel))
            size = full.stat().st_size
            total += size
            included += 1

        # 单独把审查说明放到 zip 根，命名清晰
        readme = ROOT / "CHATGPT_REVIEW_README.md"
        if readme.exists():
            zf.write(readme, arcname="CHATGPT_REVIEW_README.md")
            total += readme.stat().st_size
            included += 1

    print(f"\nDone. {included} files, uncompressed {total/1024:.1f} KB")
    print(f"Output: {OUTPUT} ({OUTPUT.stat().st_size/1024:.1f} KB compressed)")


if __name__ == "__main__":
    main()
