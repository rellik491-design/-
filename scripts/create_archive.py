import os
import shutil
import zipfile
from pathlib import Path

def make_archive():
    project_root = Path(r"C:\Users\ildar\.gemini\antigravity\scratch\telegram-companion-ai").resolve()
    target_zip = Path(r"C:\Users\ildar\.gemini\antigravity\scratch\telegram-companion-ai.zip").resolve()
    artifact_zip = Path(r"C:\Users\ildar\.gemini\antigravity\brain\7d9dca96-6636-4822-bad8-e851dde6088e\telegram-companion-ai.zip").resolve()

    exclude_dirs = {".venv", "__pycache__", ".pytest_cache", ".git", ".idea", ".vscode"}
    exclude_extensions = {".pyc", ".pyo", ".pyd", ".log"}

    print(f"📦 Упаковка проекта из {project_root}...")

    file_count = 0
    total_bytes = 0

    with zipfile.ZipFile(target_zip, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(project_root):
            # Prune excluded directories
            dirs[:] = [d for d in dirs if d not in exclude_dirs]

            for file in files:
                file_path = Path(root) / file
                if file_path.suffix in exclude_extensions:
                    continue
                if file.endswith("-journal"):
                    continue

                rel_path = file_path.relative_to(project_root.parent)
                # Store relative to parent folder name: telegram-companion-ai/...
                zf.write(file_path, arcname=str(rel_path))
                file_count += 1
                total_bytes += file_path.stat().st_size

    # Copy to artifact directory for convenient direct access
    artifact_zip.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(target_zip, artifact_zip)

    zip_size_mb = target_zip.stat().st_size / (1024 * 1024)
    print(f"✅ Архив успешно создан:")
    print(f"   Файлов упаковано: {file_count}")
    print(f"   Размер архива: {zip_size_mb:.2f} MB")
    print(f"   Основной путь: {target_zip}")
    print(f"   Копия в артефактах: {artifact_zip}")

    print("\n🔍 Проверка содержимого архива:")
    with zipfile.ZipFile(target_zip, "r") as test_zf:
        names = test_zf.namelist()
        for n in sorted(names):
            print(f"   - {n}")


if __name__ == "__main__":
    make_archive()
