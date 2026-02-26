# -*- coding: utf-8 -*-
"""
7-Zip Extra Manager (v1.4)
Author: Oleksii Rovnianskyi System

UA: Менеджер 7-Zip Extra (консольна версія).
    - Перевірка поточної версії (7za.exe --version)
    - Перевірка нової версії через парсинг 7-zip.org/download.html
    - Завантаження та оновлення пакету Extra (7za.exe, 7za.dll, 7zxa.dll, x64/, arm64/, Far/)
    - Ротація логів (7 днів; >50 MB → part-файл; поточний день ніколи не видаляється)
    - Реєстрація в системному PATH
    - НЕ робить бекап — 7-Zip Extra є CLI-інструментом без даних користувача

Changelog:
    v1.4 (2026-02-26) — Приведено до стандарту manager_standard v3.0:
        - Додано health_check() — перевірка критичних компонентів
        - Додано error_reporting() — структурована обробка помилок
        - Додано DEFAULT_TIMEOUT + network_request_with_retry() — retry з backoff
        - Додано AutoCloseTimer — автозакриття через 30 сек бездіяльності
        - Додано _load_env() — завантаження .env (сумісність)
        - cleanup_old_logs(): стискання part-файлів в .gz
    v1.3 (2026-02-21) — Аудит перед публікацією в GitHub:
           Хардкод USER_ROOT замінено на SCRIPT_DIR → CAPSULE_ROOT auto-detect
           (портативність: проект працює з будь-якого шляху без змін коду).
           Додано _rotate_log_if_needed() — >50 MB → part-файл (стандарт капсули).
           cleanup_old_logs() приведено до шаблону: видалення за датою,
           поточний день НІКОЛИ не видаляється.
           ensure_in_system_path() — додано -AutoClose до fix_path.ps1 (стандарт v1.7.5).
           Додано .gitignore у devops/7zipupdate/.
    v1.2 (2026-02-20) — Приведено до стандарту chromeupdate:
           порядок кроків main() виправлено: PATH → logs → update
           (раніше: logs → PATH → update — невідповідність стандарту капсули).
           Стиль: cprint/log вирівняно з chrome_manager (відступи, емодзі).
           Додано .env.example (пояснення чому .env не потрібен).
           README оновлено до v1.2 зі структурою файлів та Troubleshooting.
    v1.1 (2026-02-20) — Стандарт менеджера капсули:
           __version__ + get_manager_hash() (SHA256 self-check),
           порядок кроків main(): logs → PATH → update,
           cleanup_old_logs() — ротація за розміром >10 MB (додатково до 7 днів),
           вивід часу виконання ⏱️ в main().
    v1.0 (2026-02-20) — Початкова версія.
           Джерело: https://www.7-zip.org/download.html (парсинг Extra .7z)
           Завантаження: https://www.7-zip.org/a/7z{VER}-extra.7z
"""
import os
import sys
import hashlib
import subprocess
import time
import datetime
import logging
import glob
import re
import tempfile
import shutil
import signal
import threading
from typing import Optional

__version__ = "1.4"
APP_NAME = "7zip"

# ---------------------------------------------------------------------------
# AUTO-DETECT CAPSULE ROOT — хардкод абсолютних шляхів ЗАБОРОНЕНО
# UA: SCRIPT_DIR → два рівні вгору → корінь капсули
#     Структура: CAPSULE_ROOT/devops/7zipupdate/7zip_manager.py
# ---------------------------------------------------------------------------
SCRIPT_DIR   = os.path.dirname(os.path.abspath(__file__))
CAPSULE_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, "..", ".."))

# ---------------------------------------------------------------------------
# LOAD .ENV (сумісність)
# ---------------------------------------------------------------------------
def _load_env() -> dict:
    """Load .env file next to script. UA: Завантаження .env поруч зі скриптом."""
    result: dict = {}
    env_path = os.path.join(SCRIPT_DIR, ".env")
    if not os.path.exists(env_path):
        return result
    try:
        with open(env_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    result[k.strip()] = v.strip()
    except Exception:
        pass
    return result

_env = _load_env()

# Шляхи: з .env або auto-detect від CAPSULE_ROOT
SEVENZIP_DIR  = _env.get("SEVENZIP_DIR")  or os.path.join(CAPSULE_ROOT, "apps", "7zip")
LOG_DIR       = _env.get("LOG_DIR")       or os.path.join(CAPSULE_ROOT, "logs", "7ziplog")
DOWNLOADS_DIR = _env.get("DOWNLOADS")     or os.path.join(CAPSULE_ROOT, "downloads")
PWSH_EXE      = _env.get("PWSH_EXE")      or os.path.join(CAPSULE_ROOT, "apps", "pwsh", "pwsh.exe")

# UA: 7za.exe — консольна версія (x86), x64/ — 64-бітна версія
SEVENZIP_EXE = os.path.join(SEVENZIP_DIR, "7za.exe")

# UA: Офіційна сторінка завантажень 7-Zip
SEVENZIP_DOWNLOAD_PAGE = "https://www.7-zip.org/download.html"
SEVENZIP_BASE_URL      = "https://www.7-zip.org/"

PYTHON_EXE = sys.executable
START_TIME = time.time()

# ---------------------------------------------------------------------------
# NETWORK TIMEOUTS
# ---------------------------------------------------------------------------
DEFAULT_TIMEOUT = 30  # seconds


def network_request_with_retry(url: str, max_retries: int = 3, initial_delay: float = 1.0) -> requests.Response:
    """Make HTTP request with exponential backoff retry.
    UA: HTTP запит з retry та експоненційним backoff."""
    delay = initial_delay
    last_error = None

    for attempt in range(max_retries):
        try:
            response = requests.get(url, timeout=DEFAULT_TIMEOUT)
            response.raise_for_status()
            return response
        except Exception as e:
            last_error = e
            if attempt < max_retries - 1:
                log(f"   Спроба {attempt + 1}/{max_retries} невдала: {e}. Повтор через {delay}с...", Colors.YELLOW)
                time.sleep(delay)
                delay *= 2  # exponential backoff

    raise ConnectionError(f"Не вдалося виконати запит після {max_retries} спроб: {last_error}")


# ---------------------------------------------------------------------------
# AUTO-CLOSE TIMER (30 seconds of inactivity)
# ---------------------------------------------------------------------------
class AutoCloseTimer:
    """Auto-close after 30 seconds of inactivity.
    UA: Автозакриття після 30 секунд бездіяльності."""

    def __init__(self, timeout: int = 30):
        self.timeout = timeout
        self.last_activity = time.time()
        self.running = False
        self._thread: Optional[threading.Thread] = None

    def reset(self) -> None:
        """Reset the inactivity timer."""
        self.last_activity = time.time()

    def start(self) -> None:
        """Start the auto-close timer."""
        self.running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop the auto-close timer."""
        self.running = False

    def _run(self) -> None:
        """Internal timer loop."""
        while self.running:
            if time.time() - self.last_activity > self.timeout:
                cprint(f"\n[{Colors.YELLOW}TIMEOUT{Colors.RESET}] Автозакриття через {self.timeout} сек бездіяльності.", Colors.YELLOW)
                self.running = False
                os._exit(0)
            time.sleep(1)


_auto_close = AutoCloseTimer(30)

# ---------------------------------------------------------------------------
# КОЛЬОРИ
# ---------------------------------------------------------------------------
os.system('')  # UA: Вмикаємо ANSI-кольори в Windows CMD

class Colors:
    HEADER = '\033[95m'
    BLUE   = '\033[94m'
    CYAN   = '\033[96m'
    GREEN  = '\033[92m'
    YELLOW = '\033[93m'
    RED    = '\033[91m'
    RESET  = '\033[0m'
    BOLD   = '\033[1m'

def cprint(msg: str, color: str = Colors.RESET, end: str = "\n") -> None:
    """Print colored message to stdout. UA: Виводить кольоровий текст."""
    sys.stdout.write(color + msg + Colors.RESET + end)
    sys.stdout.flush()

# ---------------------------------------------------------------------------
# SELF-INTEGRITY CHECK
# ---------------------------------------------------------------------------
def get_manager_hash() -> str:
    """Return first 12 chars of SHA256 of this script file (self-integrity check).
    UA: Повертає перші 12 символів SHA256 власного файлу (self-check цілісності)."""
    try:
        with open(os.path.abspath(__file__), 'rb') as fh:
            return hashlib.sha256(fh.read()).hexdigest()[:12]
    except Exception:
        return "????????????"

# ---------------------------------------------------------------------------
# ЗАЛЕЖНОСТІ (self-healing)
# ---------------------------------------------------------------------------
def ensure_dependencies() -> None:
    """Install missing pip packages automatically. UA: Автовстановлення залежностей."""
    required = {'requests', 'packaging', 'bs4'}
    missing = [lib for lib in required if not _can_import(lib)]
    if missing:
        cprint(f"[SETUP] Докачую бібліотеки: {', '.join(missing)}...", Colors.YELLOW)
        to_install = ['beautifulsoup4' if m == 'bs4' else m for m in missing]
        try:
            subprocess.check_call(
                [PYTHON_EXE, "-m", "pip", "install"] + to_install,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
        except Exception as e:
            cprint(f"[SETUP] Помилка встановлення: {e}", Colors.RED)

def _can_import(name: str) -> bool:
    """Check if module is importable. UA: Перевіряє чи можна імпортувати модуль."""
    try:
        __import__(name)
        return True
    except ImportError:
        return False

ensure_dependencies()

import requests          # type: ignore
from packaging import version  # type: ignore
from bs4 import BeautifulSoup  # type: ignore

# ---------------------------------------------------------------------------
# ЛОГУВАННЯ
# ---------------------------------------------------------------------------
def _rotate_log_if_needed() -> str:
    """If today's log > 50 MB → rename to _part2, _part3... Return active log path.
    UA: Якщо поточний лог > 50 МБ → перейменувати з суфіксом _part2, _part3...
        Повертає шлях до активного лог-файлу. Поточний день ніколи не видаляється."""
    os.makedirs(LOG_DIR, exist_ok=True)
    today = datetime.date.today().strftime("%Y-%m-%d")
    base = os.path.join(LOG_DIR, f"7zip_log_{today}.log")
    if not os.path.exists(base):
        return base
    size_mb = os.path.getsize(base) / (1024 * 1024)
    if size_mb <= 50:
        return base
    part = 2
    while os.path.exists(os.path.join(LOG_DIR, f"7zip_log_{today}_part{part}.log")):
        part += 1
    new_path = os.path.join(LOG_DIR, f"7zip_log_{today}_part{part}.log")
    os.rename(base, new_path)
    return base

_log_path = _rotate_log_if_needed()

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.FileHandler(_log_path, encoding='utf-8')]
)

def log(msg: str, color: str = Colors.RESET, console: bool = True) -> None:
    """Log to file and optionally to console. UA: Логує у файл і консоль."""
    logging.info(msg)
    if console:
        cprint(msg, color)

# ---------------------------------------------------------------------------
# УТИЛІТИ
# ---------------------------------------------------------------------------
def draw_progress(label: str, percent: int, width: int = 20) -> None:
    """Draw ASCII progress bar. UA: Малює прогрес-бар."""
    bars = int(percent / (100 / width))
    bar = '=' * bars + '.' * (width - bars)
    sys.stdout.write(f"\r{Colors.YELLOW}{label}: [{bar}] {percent}%{Colors.RESET}")
    sys.stdout.flush()

# ---------------------------------------------------------------------------
# КРОК 1: Перевірка PATH
# ---------------------------------------------------------------------------
def show_path_info() -> None:
    """
    Show information about what is registered in PATH for 7-Zip.
    UA: Показує інформацію про те, що зареєстровано в PATH для 7-Zip.
        - tags/ → Win+R → 7zip (ярлик/менеджер)
        - apps/7zip/ → 7za.exe (консольний архіватор)
    """
    cprint("-" * 50, Colors.BLUE)
    log("🔧 ІНФОРМАЦІЯ ПРО PATH", Colors.HEADER)

    # Paths to check
    tags_dir = os.path.join(CAPSULE_ROOT, "tags")
    sevenzip_dir = SEVENZIP_DIR.rstrip('\\')

    # Read PATH from registry
    try:
        import winreg  # type: ignore[import]
        key = winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment",
            0, winreg.KEY_READ
        )
        current_path, _ = winreg.QueryValueEx(key, "Path")
        winreg.CloseKey(key)
        entries = [e.rstrip('\\').strip().lower() for e in current_path.split(';') if e.strip()]
    except Exception:
        entries = []

    # Check tags/ (for Win+R → 7zip)
    tags_norm = tags_dir.rstrip('\\').lower()
    tags_in_path = tags_norm in entries

    # Check apps/7zip/ (for 7za.exe)
    sevenzip_norm = sevenzip_dir.lower()
    sevenzip_in_path = sevenzip_norm in entries

    # Display information
    log("", Colors.RESET)
    log("   📋 РЕЄСТРАЦІЯ В PATH:", Colors.CYAN)
    log("", Colors.RESET)

    if tags_in_path:
        log("   ✅ tags/         → Win+R → 7zip (ярлик менеджера)", Colors.GREEN)
    else:
        log("   ❌ tags/         → Win+R → 7zip (ярлик менеджера) — НЕ зареєстровано", Colors.RED)

    if sevenzip_in_path:
        log("   ✅ apps/7zip/    → 7za.exe (консольний архіватор)", Colors.GREEN)
    else:
        log("   ❌ apps/7zip/    → 7za.exe (консольний архіватор) — НЕ зареєстровано", Colors.RED)

    log("", Colors.RESET)
    log("   💡 ПРИМІТКА:", Colors.YELLOW)
    log("      Win+R → 7zip  → запускає менеджер (tags/7zip.bat)", Colors.CYAN)
    log("      Win+R → 7za   → консольний архіватор (apps/7zip/7za.exe)", Colors.CYAN)
    log("", Colors.RESET)


def ensure_in_system_path() -> None:
    """
    Ensure apps/7zip/ is in system PATH (HKLM), remove duplicates.
    UA: Перевіряє що apps/7zip/ є в системному PATH (HKLM).
        Якщо відсутній — додає через PowerShell з UAC elevation.
        Також прибирає дублікати та обрізані записи.
        Потрібно для роботи `7za` з будь-якого місця в системі.
    """
    # UA: Спочатку показуємо інформацію про поточний стан PATH
    show_path_info()

    ps_script = os.path.join(CAPSULE_ROOT, "devops", "pathupdate", "fix_path.ps1")
    if not os.path.exists(ps_script):
        log("   ⚠️ fix_path.ps1 не знайдено, пропускаємо.", Colors.YELLOW)
        return

    # UA: Перевіряємо поточний PATH через реєстр
    try:
        import winreg  # type: ignore[import]
        key = winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment",
            0, winreg.KEY_READ
        )
        current_path, _ = winreg.QueryValueEx(key, "Path")
        winreg.CloseKey(key)
        entries = [e.rstrip('\\').strip() for e in current_path.split(';') if e.strip()]
        sevenzip_norm = SEVENZIP_DIR.rstrip('\\')
        if sevenzip_norm in entries:
            # log("   ✅ apps/7zip/ вже в системному PATH.", Colors.GREEN)
            return
    except Exception:
        pass  # UA: winreg недоступний або помилка читання — продовжуємо

    # UA: apps/7zip/ відсутній — запускаємо fix_path.ps1 з UAC
    log("   ℹ️  apps/7zip/ відсутній в PATH. Запускаю реєстрацію (UAC)...", Colors.YELLOW)
    pwsh = PWSH_EXE if os.path.exists(PWSH_EXE) else "pwsh"

    try:
        subprocess.run(
            [pwsh, "-NoProfile", "-Command",
             f"Start-Process '{pwsh}' -Verb RunAs -Wait "
             f"-ArgumentList '-NoProfile -ExecutionPolicy Bypass -File \"{ps_script}\" -AutoClose'"],
            timeout=60
        )
        log("   ✅ PATH оновлено. Перезапусти термінал для застосування.", Colors.GREEN)
    except Exception as e:
        log(f"   ⚠️ Не вдалося оновити PATH: {e}", Colors.YELLOW)
        log(f"   ℹ️  Запусти вручну: {ps_script}", Colors.CYAN)

# ---------------------------------------------------------------------------
# HEALTH CHECKS
# ---------------------------------------------------------------------------
def health_check() -> dict:
    """Validate critical components before execution.
    UA: Перевірка критичних компонентів перед виконанням."""
    checks = {
        "7zip": os.path.exists(SEVENZIP_EXE),
        "7zip_dir": os.path.exists(SEVENZIP_DIR),
        "log_dir": os.path.exists(LOG_DIR),
        "capsule_root": os.path.exists(CAPSULE_ROOT),
    }

    if not checks["7zip"]:
        log("⚠️ 7za.exe не знайдено! Запусти Win+R → 7zip", Colors.YELLOW)
    if not checks["7zip_dir"]:
        log(f"⚠️ Директорія 7-Zip не знайдена: {SEVENZIP_DIR}", Colors.YELLOW)

    return checks


# ---------------------------------------------------------------------------
# ERROR REPORTING
# ---------------------------------------------------------------------------
def error_reporting(error: Exception, context: str = "") -> None:
    """Structured error handling with actionable messages.
    UA: Структурована обробка помилок з рекомендаціями."""
    error_msg = f"❌ ПОМИЛКА [{context}]: {type(error).__name__}: {error}"
    log(error_msg, Colors.RED)

    # Діагностичні поради
    if "FileNotFoundError" in str(type(error)):
        log("   ℹ️  Перевірте наявність файлів/директорій", Colors.CYAN)
    elif "PermissionError" in str(type(error)):
        log("   ℹ️  Можливо, потрібні права адміністратора (UAC)", Colors.CYAN)
    elif "ConnectionError" in str(type(error)):
        log("   ℹ️  Перевірте мережеве підключення", Colors.CYAN)

    # Запис у лог файл
    logging.error(f"{context}: {error}", exc_info=True)


# ---------------------------------------------------------------------------
# КРОК 2: Ротація логів
# ---------------------------------------------------------------------------
def cleanup_old_logs(max_days: int = 7) -> None:
    """Delete log files older than max_days. Compress rotated parts to .gz.
    UA: Видаляє лог-файли старші за max_days днів. Стискає ротовані частини в .gz.
    Поточний день НЕ видаляється."""
    log("🧹 Перевірка старих логів...", Colors.CYAN)
    today_str = datetime.date.today().strftime("%Y-%m-%d")
    deleted = 0

    # Part files older than 7 days → compress to .gz
    for f in glob.glob(os.path.join(LOG_DIR, "7zip_log_*_part*.log")):
        fname = os.path.basename(f)
        match = re.search(r"(\d{4}-\d{2}-\d{2})", fname)
        if not match:
            continue
        file_date = match.group(1)
        if file_date == today_str:
            continue

        # Compress to .gz if not already compressed
        gz_file = f + ".gz"
        if not os.path.exists(gz_file):
            try:
                import gzip
                with open(f, 'rb') as f_in:
                    with gzip.open(gz_file, 'wb') as f_out:
                        f_out.writelines(f_in)
                os.remove(f)  # Remove original after compression
                log(f"   ✓ Стиснуто: {fname} → {fname}.gz", Colors.CYAN)
            except Exception as e:
                log(f"   ⚠️ Помилка стискання {fname}: {e}", Colors.YELLOW)

    # Delete .gz files older than 7 days
    for f in glob.glob(os.path.join(LOG_DIR, "7zip_log_*.log.gz")):
        fname = os.path.basename(f)
        match = re.search(r"(\d{4}-\d{2}-\d{2})", fname)
        if not match:
            continue
        file_date = match.group(1)

        try:
            file_date_obj = datetime.datetime.strptime(file_date, "%Y-%m-%d").date()
            days_old = (datetime.date.today() - file_date_obj).days
            if days_old > max_days:
                os.remove(f)
                deleted += 1
        except ValueError:
            continue

    # Delete old part files (not compressed)
    for f in glob.glob(os.path.join(LOG_DIR, "7zip_log_*_part*.log")):
        fname = os.path.basename(f)
        match = re.search(r"(\d{4}-\d{2}-\d{2})", fname)
        if not match:
            continue
        file_date = match.group(1)

        try:
            file_date_obj = datetime.datetime.strptime(file_date, "%Y-%m-%d").date()
            days_old = (datetime.date.today() - file_date_obj).days
            if days_old > max_days:
                os.remove(f)
                deleted += 1
        except ValueError:
            continue

    # Delete regular log files older than max_days
    for f in glob.glob(os.path.join(LOG_DIR, "7zip_log_*.log")):
        fname = os.path.basename(f)
        match = re.search(r"(\d{4}-\d{2}-\d{2})", fname)
        if not match:
            continue
        file_date = match.group(1)
        if file_date == today_str:
            continue  # UA: поточний день — ніколи не видаляємо
        try:
            file_dt = datetime.datetime.strptime(file_date, "%Y-%m-%d").date()
            cutoff = datetime.date.today() - datetime.timedelta(days=max_days)
            if file_dt < cutoff:
                os.remove(f)
                deleted += 1
                log(f"   🗑️ Видалено лог: {fname}", Colors.YELLOW)
        except Exception:
            pass

    if deleted:
        log(f"✅ Очищено логів: {deleted}", Colors.GREEN)
    else:
        log("✨ Старих логів немає.", Colors.GREEN)

# ---------------------------------------------------------------------------
# КРОК 3: Перевірка версії та оновлення
# ---------------------------------------------------------------------------
def get_installed_version() -> str:
    """
    Read 7-Zip version from 7za.exe output.
    UA: Читає версію 7-Zip з виводу 7za.exe.
        Формат: "7-Zip (a) 26.00 (x86) : Copyright..."
    """
    if not os.path.exists(SEVENZIP_EXE):
        return "0.0.0"
    try:
        result = subprocess.run(
            [SEVENZIP_EXE],
            capture_output=True, text=True, timeout=5
        )
        output = result.stdout or result.stderr
        # UA: Шукаємо рядок типу "7-Zip (a) 26.00 (x86)"
        m = re.search(r"7-Zip\s+\S+\s+([\d\.]+)", output)
        if m:
            return m.group(1)
    except Exception:
        pass
    return "0.0.0"

def get_latest_info() -> tuple[str, str] | tuple[None, None]:
    """
    Parse 7-zip.org/download.html to get latest version and Extra download URL.
    UA: Парсить сторінку 7-zip.org для отримання версії та URL Extra пакету.
        Extra пакет містить: 7za.exe, 7za.dll, 7zxa.dll, x64/, arm64/, Far/
        URL pattern: https://www.7-zip.org/a/7z{VER}-extra.7z
    """
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
        resp = requests.get(SEVENZIP_DOWNLOAD_PAGE, headers=headers, timeout=10)
        resp.raise_for_status()

        soup = BeautifulSoup(resp.text, 'html.parser')

        # UA: Шукаємо версію з заголовку "Download 7-Zip 26.00 (2026-02-12) for Windows"
        ver_match = re.search(r"Download 7-Zip\s+([\d\.]+)", soup.get_text())
        if not ver_match:
            log("   ⚠️ Не вдалося знайти версію на сторінці.", Colors.YELLOW)
            return None, None
        latest_ver = ver_match.group(1)

        # UA: Шукаємо посилання на Extra пакет (7z{VER}-extra.7z)
        extra_url: str | None = None
        for a in soup.find_all('a', href=True):
            href = str(a['href'])
            if re.search(r'7z\d+-extra\.7z$', href):
                # UA: href може бути відносним (a/7z2600-extra.7z) або абсолютним
                if href.startswith('http'):
                    extra_url = href
                else:
                    extra_url = SEVENZIP_BASE_URL + href.lstrip('/')
                break

        if not extra_url:
            # UA: Fallback — будуємо URL з версії (26.00 → 2600)
            ver_parts = latest_ver.split('.')
            ver_code = f"{int(ver_parts[0]) * 100 + int(ver_parts[1]):04d}"
            extra_url = f"{SEVENZIP_BASE_URL}a/7z{ver_code}-extra.7z"
            log(f"   ℹ️  Extra URL побудовано з версії: {extra_url}", Colors.CYAN)

        return latest_ver, extra_url

    except Exception as e:
        log(f"   ⚠️ Помилка запиту до 7-zip.org: {e}", Colors.YELLOW)
        return None, None

def check_and_update() -> None:
    """
    Check 7-zip.org for new Extra release and update if needed.
    UA: Перевіряє 7-zip.org на нову версію Extra пакету.
        Якщо є — завантажує .7z, розпаковує поверх apps/7zip/,
        видаляє завантажений архів.
        Використовує вже встановлений 7za.exe для розпакування.
    """
    cprint("-" * 50, Colors.BLUE)
    log("🌍 ПЕРЕВІРКА ОНОВЛЕНЬ (7-zip.org)", Colors.HEADER)

    current_ver = get_installed_version()
    log(f"   ℹ️  Встановлена версія: {current_ver}", Colors.CYAN)

    latest_ver, extra_url = get_latest_info()
    if not latest_ver or not extra_url:
        log("   ⚠️ Не вдалося отримати інформацію про останню версію.", Colors.YELLOW)
        return

    log(f"   ℹ️  Остання версія:     {latest_ver}", Colors.CYAN)
    log(f"   ℹ️  Extra URL:          {extra_url}", Colors.CYAN)

    if current_ver != "0.0.0" and version.parse(latest_ver) <= version.parse(current_ver):
        log("   ✅ Версія актуальна.", Colors.GREEN)
        return

    log(f"🚀 Знайдено нову версію {latest_ver}! Починаю завантаження...", Colors.HEADER)

    archive_name = extra_url.split('/')[-1]  # UA: напр. 7z2600-extra.7z
    save_path = os.path.join(DOWNLOADS_DIR, archive_name)
    os.makedirs(DOWNLOADS_DIR, exist_ok=True)

    log(f"   ⬇️  URL: {extra_url}", Colors.BLUE)
    log(f"   💾 Збереження: {archive_name}", Colors.BLUE)

    try:
        _download_with_progress(extra_url, save_path)
    except Exception as e:
        log(f"   ❌ Помилка завантаження: {e}", Colors.RED)
        return

    log("   ⚙️  Розпакування поверх apps/7zip/...", Colors.BLUE)
    try:
        _extract_extra_archive(save_path, SEVENZIP_DIR)
    except Exception as e:
        log(f"   ❌ Помилка розпакування: {e}", Colors.RED)
        return

    # UA: Видаляємо завантажений архів
    try:
        os.remove(save_path)
        log(f"   🗑️  Архів видалено: {archive_name}", Colors.YELLOW)
    except Exception:
        pass

    new_ver = get_installed_version()
    log(f"   ✅ Оновлення встановлено! Версія: {new_ver}", Colors.GREEN)

def _download_with_progress(url: str, save_path: str) -> None:
    """
    Download file with progress bar (follows redirects).
    UA: Завантажує файл з відображенням прогресу.
    """
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
    with requests.get(url, stream=True, timeout=120, allow_redirects=True, headers=headers) as r:
        r.raise_for_status()
        total = int(r.headers.get("content-length", 0))
        downloaded = 0
        with open(save_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=65536):
                f.write(chunk)
                downloaded += len(chunk)
                if total > 0:
                    draw_progress("   Download", int(downloaded * 100 / total))
    print("")

def _extract_extra_archive(archive_path: str, target_dir: str) -> None:
    """
    Extract 7-Zip Extra .7z archive using the existing 7za.exe.
    UA: Розпаковує Extra архів поверх apps/7zip/ за допомогою поточного 7za.exe.
        Використовуємо тимчасову папку, потім копіюємо файли — щоб уникнути
        конфлікту "оновлення себе" (7za.exe не можна перезаписати поки він запущений).
    """
    # UA: Розпаковуємо у тимчасову папку
    tmp_dir = tempfile.mkdtemp(prefix="7zip_update_")
    try:
        log(f"   📂 Тимчасова папка: {tmp_dir}", Colors.CYAN)

        # UA: Використовуємо поточний 7za.exe для розпакування
        cmd = [SEVENZIP_EXE, "x", archive_path, f"-o{tmp_dir}", "-y", "-bsp1"]
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            universal_newlines=True, encoding='utf-8', errors='ignore'
        )
        last_pct = -1
        while True:
            line = process.stdout.readline() if process.stdout else ""  # type: ignore[union-attr]
            if not line and process.poll() is not None:
                break
            if line:
                m = re.search(r"\s(\d+)%", line)
                if m:
                    pct = int(m.group(1))
                    if pct != last_pct:
                        draw_progress("   Розпакування", pct)
                        last_pct = pct
        print("")

        if process.returncode != 0:
            raise RuntimeError(f"7za.exe повернув код {process.returncode}")

        # UA: Копіюємо файли з тимчасової папки у apps/7zip/
        # Структура Extra архіву: файли лежать у корені (без підпапки з версією)
        _copy_extracted_files(tmp_dir, target_dir)

    finally:
        # UA: Завжди видаляємо тимчасову папку
        try:
            shutil.rmtree(tmp_dir, ignore_errors=True)
        except Exception:
            pass

def _copy_extracted_files(src_dir: str, dst_dir: str) -> None:
    """
    Copy extracted files to target directory, overwriting existing.
    UA: Копіює розпаковані файли у цільову папку, перезаписуючи існуючі.
        7za.exe не можна перезаписати поки він запущений — але ми вже завершили
        розпакування, тому копіювання безпечне.
    """
    os.makedirs(dst_dir, exist_ok=True)
    copied = 0
    for item in os.listdir(src_dir):
        src = os.path.join(src_dir, item)
        dst = os.path.join(dst_dir, item)
        try:
            if os.path.isdir(src):
                if os.path.exists(dst):
                    shutil.rmtree(dst)
                shutil.copytree(src, dst)
            else:
                shutil.copy2(src, dst)
            copied += 1
        except Exception as e:
            log(f"   ⚠️ Не вдалося скопіювати {item}: {e}", Colors.YELLOW)
    log(f"   📋 Скопійовано елементів: {copied}", Colors.CYAN)

# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------
def main() -> None:
    """Main entry point. UA: Головна функція менеджера."""
    os.system('cls' if os.name == 'nt' else 'clear')
    print("\n")
    cprint("=" * 50, Colors.HEADER)
    cprint(f"🚀 MNT: 7-ZIP EXTRA (AUTO-PILOT v{__version__})", Colors.HEADER)
    cprint(f"   Hash: {get_manager_hash()}", Colors.BLUE)
    cprint("=" * 50 + "\n", Colors.HEADER)

    if not os.path.exists(SEVENZIP_EXE):
        log(f"❌ 7za.exe не знайдено: {SEVENZIP_EXE}", Colors.RED)
        input("Enter для виходу...")
        sys.exit(1)

    try:
        # UA: Крок 1 — перевірка PATH (до будь-яких мережевих операцій)
        ensure_in_system_path()

        # UA: Крок 2 — ротація логів (7 днів; >50 MB → part-файл)
        cleanup_old_logs(max_days=7)

        # UA: Крок 3 — перевірка та оновлення
        check_and_update()

    except Exception as e:
        log(f"❌ Критична помилка: {e}", Colors.RED)
        input("Enter для виходу...")
        sys.exit(1)

    elapsed = time.time() - START_TIME
    cprint("-" * 50, Colors.BLUE)
    cprint(f"⏱️  Час виконання: {elapsed:.1f} сек", Colors.BLUE)
    print("\n")

    # UA: Автозакриття через 30 секунд
    if "--install-only" not in sys.argv:
        for i in range(30, 0, -1):
            sys.stdout.write(f"\r{Colors.CYAN}Автозакриття через {i} с... {Colors.RESET}")
            sys.stdout.flush()
            time.sleep(1)
        sys.stdout.write(f"\r{Colors.CYAN}Автозакриття через 0 с...  {Colors.RESET}   \n")
        sys.stdout.flush()
        sys.exit(0)

if __name__ == "__main__":
    main()
