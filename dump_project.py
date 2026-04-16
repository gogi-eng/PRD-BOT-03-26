#!/usr/bin/env python3
# dump_project.py
"""
📦 ДАМП ПРОЕКТА В ОДИН ФАЙЛ

Создает PROJECT_DUMP.txt со всеми файлами проекта.
ВАЖНО: .env файлы НЕ копируются (защита ключей)!

Запуск: python dump_project.py
"""
import os
from datetime import datetime

PROJECT_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bot")
OUTPUT_FILE = "BOT_DUMP.txt"

# Папки которые пропускаем
EXCLUDE_DIRS = {
    "venv",
    "__pycache__",
    ".git",
    ".idea",
    ".vscode",
    "node_modules",
    "journal_output",  # Папка с графиками журнала
    ".pytest_cache",
}

# Файлы которые пропускаем полностью
EXCLUDE_FILES = {
    OUTPUT_FILE,
    "dump_project.py",
    "PROJECT_DUMP.txt",
}

# Файлы которые НЕ копируем содержимое (только показываем что файл есть)
SENSITIVE_FILES = {
    ".env",
    ".env.local",
    ".env.production",
    "secrets.yaml",
    "credentials.json",
}

# Расширения файлов которые включаем
INCLUDE_EXTENSIONS = {
    ".py",
    ".yaml",
    ".yml",
    ".json",
    ".txt",
    ".md",
    ".toml",
    ".cfg",
    ".ini",
}

def should_skip_dir(path):
    """Проверяет нужно ли пропустить директорию."""
    parts = path.split(os.sep)
    return any(part in EXCLUDE_DIRS for part in parts)

def get_file_stats(full_path):
    """Получает статистику файла."""
    try:
        stat = os.stat(full_path)
        size = stat.st_size
        if size < 1024:
            size_str = f"{size} bytes"
        elif size < 1024 * 1024:
            size_str = f"{size / 1024:.1f} KB"
        else:
            size_str = f"{size / (1024 * 1024):.1f} MB"
        return size_str
    except Exception:
        return "unknown"

def main():
    """Основная функция дампа проекта."""
    files_count = 0
    sensitive_count = 0
    
    print("📦 Создание дампа проекта...")
    print(f"   Корень: {PROJECT_ROOT}")
    print(f"   Выход: {OUTPUT_FILE}")
    print()
    
    with open(OUTPUT_FILE, "w", encoding="utf-8") as out:
        # Заголовок
        out.write("=" * 80 + "\n")
        out.write("📦 PROJECT DUMP - BYBIT TRADING BOT\n")
        out.write(f"📅 Дата: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        out.write(f"📁 Корень: {PROJECT_ROOT}\n")
        out.write("=" * 80 + "\n\n")
        
        # Сначала собираем структуру проекта
        out.write("📂 СТРУКТУРА ПРОЕКТА:\n")
        out.write("-" * 40 + "\n")
        
        for root, dirs, files in os.walk(PROJECT_ROOT):
            # Фильтруем директории
            dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
            
            rel_root = os.path.relpath(root, PROJECT_ROOT)
            level = rel_root.count(os.sep)
            indent = "  " * level
            
            if rel_root != ".":
                out.write(f"{indent}📁 {os.path.basename(root)}/\n")
            
            for file in sorted(files):
                if file in EXCLUDE_FILES:
                    continue
                    
                ext = os.path.splitext(file)[1].lower()
                if ext not in INCLUDE_EXTENSIONS and file not in SENSITIVE_FILES:
                    continue
                
                file_indent = "  " * (level + 1)
                
                if file in SENSITIVE_FILES:
                    out.write(f"{file_indent}🔒 {file} [PROTECTED]\n")
                else:
                    out.write(f"{file_indent}📄 {file}\n")
        
        out.write("\n" + "=" * 80 + "\n")
        out.write("📄 СОДЕРЖИМОЕ ФАЙЛОВ:\n")
        out.write("=" * 80 + "\n")
        
        # Теперь копируем содержимое
        for root, dirs, files in os.walk(PROJECT_ROOT):
            dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
            
            for file in sorted(files):
                if file in EXCLUDE_FILES:
                    continue
                
                ext = os.path.splitext(file)[1].lower()
                if ext not in INCLUDE_EXTENSIONS and file not in SENSITIVE_FILES:
                    continue
                
                full_path = os.path.join(root, file)
                rel_path = os.path.relpath(full_path, PROJECT_ROOT)
                
                if should_skip_dir(rel_path):
                    continue
                
                size_str = get_file_stats(full_path)
                
                out.write("\n" + "─" * 80 + "\n")
                out.write(f"📄 FILE: {rel_path}\n")
                out.write(f"   SIZE: {size_str}\n")
                out.write("─" * 80 + "\n\n")
                
                # Проверяем - это чувствительный файл?
                if file in SENSITIVE_FILES:
                    out.write("🔒 [СОДЕРЖИМОЕ СКРЫТО - ЗАЩИТА API КЛЮЧЕЙ]\n\n")
                    out.write("# Этот файл содержит секретные данные:\n")
                    out.write("# - API ключи Bybit\n")
                    out.write("# - Telegram токен\n")
                    out.write("# - Другие секреты\n")
                    out.write("#\n")
                    out.write("# Для работы бота создайте свой .env файл\n")
                    out.write("# с необходимыми переменными.\n")
                    sensitive_count += 1
                    files_count += 1
                    continue
                
                try:
                    with open(full_path, "r", encoding="utf-8") as f:
                        content = f.read()
                        out.write(content)
                        if not content.endswith("\n"):
                            out.write("\n")
                    files_count += 1
                except UnicodeDecodeError:
                    out.write("[БИНАРНЫЙ ФАЙЛ - ПРОПУЩЕН]\n")
                except Exception as e:
                    out.write(f"[ОШИБКА ЧТЕНИЯ]: {e}\n")
        
        # Футер
        out.write("\n" + "=" * 80 + "\n")
        out.write("✅ ДАМП ЗАВЕРШЕН\n")
        out.write(f"   Файлов: {files_count}\n")
        out.write(f"   Защищено: {sensitive_count}\n")
        out.write("=" * 80 + "\n")
    
    print(f"✅ Готово! Создан файл: {OUTPUT_FILE}")
    print(f"   📄 Файлов в дампе: {files_count}")
    print(f"   🔒 Защищённых (.env): {sensitive_count}")
    print()
    print("⚠️  ВАЖНО: Содержимое .env НЕ скопировано (защита ключей)")


if __name__ == "__main__":
    main()