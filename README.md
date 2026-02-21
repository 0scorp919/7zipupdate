# 7zipupdate — 7-Zip Extra Manager

Менеджер автооновлення 7-Zip Extra (консольна версія) у Autonomous Capsule.

**Поточна версія:** `7zip_manager.py` v1.3

## Запуск

```
Win+R → 7zip
```

Або напряму через портативний лаунчер (без хардкодованих шляхів):

```
devops\7zipupdate\7zip_launcher.bat
```

Або через системний лаунчер (якщо `tags/` у PATH):

```
tags\7zip.bat
```

## Алгоритм роботи

1. **Перевірка системного PATH** — `apps/7zip/` у HKLM PATH, UAC → `fix_path.ps1 -AutoClose`
2. **Очищення логів** — видалення файлів старших за 7 днів; поточний день ніколи не видаляється; якщо активний лог > 50 MB → ротація у `_part2`, `_part3`...
3. **Перевірка оновлення** — парсинг `7-zip.org/download.html`
4. **Оновлення** (якщо знайдено нову версію):
   - Завантаження `.7z` Extra архіву з прогрес-баром
   - Розпакування у тимчасову папку (`%TEMP%\7zip_update_XXXX\`)
   - Копіювання файлів у `apps/7zip/`
   - Видалення тимчасової папки та завантаженого архіву
5. **Автозакриття** через 30 секунд

## Структура файлів

```
devops/7zipupdate/
  7zip_manager.py     — головний менеджер
  7zip_launcher.bat   — портативний лаунчер (auto-detect CAPSULE_ROOT, без хардкодів)
  .env.example        — пояснення чому .env не потрібен
  .gitignore          — виключення для Git
  README.md           — ця документація

tags/
  7zip.bat            — системний лаунчер (UAC elevation → 7zip_manager.py, хардкод шляхів)

apps/7zip/
  7za.exe           — 7-Zip Extra (x86)
  7za.dll
  7zxa.dll
  x64/              — 64-бітна версія
  arm64/            — ARM64 версія
  Far/              — плагін Far Manager

logs/7ziplog/
  7zip_log_YYYY-MM-DD.log         — щоденні логи
  7zip_log_YYYY-MM-DD_part2.log   — ротація при >50 MB
```

> **Примітка:** `.env` не потрібен — 7-Zip Extra є CLI-інструментом без даних користувача.
> Резервна копія не створюється з тієї ж причини.

## Портативний лаунчер (GitHub-ready)

`7zip_launcher.bat` — лаунчер для публікації разом з проектом на GitHub.

**Відмінність від `tags/7zip.bat`:**

- `tags/7zip.bat` — системний лаунчер капсули, містить хардкодовані шляхи `C:\!Oleksii_Rovnianskyi\...`
- `7zip_launcher.bat` — портативний, auto-detect від `%~dp0` (два рівні вгору → CAPSULE_ROOT)

**Алгоритм auto-detect:**

```bat
:: %~dp0 = devops\7zipupdate\
:: Два рівні вгору = CAPSULE_ROOT
for %%A in ("%LAUNCHER_DIR%\..") do set "DEVOPS_DIR=%%~fA"
for %%A in ("%DEVOPS_DIR%\..") do set "CAPSULE_ROOT=%%~fA"
```

**Що включається в GitHub репозиторій:**
- `7zip_manager.py` — менеджер (CAPSULE_ROOT auto-detect через `SCRIPT_DIR`)
- `7zip_launcher.bat` — портативний лаунчер (CAPSULE_ROOT auto-detect через `%~dp0`)
- `.env.example` — шаблон (без реальних даних)
- `.gitignore` — виключення
- `README.md` — документація

**Що НЕ включається (gitignored):**
- `.env` — не потрібен для цього проекту
- `*.log`, `*.7z`, `*.bak`, `__pycache__/`

## Портативність

Менеджер використовує `SCRIPT_DIR → CAPSULE_ROOT` auto-detect:

```python
SCRIPT_DIR   = os.path.dirname(os.path.abspath(__file__))
CAPSULE_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, "..", ".."))
```

Хардкод абсолютних шляхів заборонено — проект працює з будь-якого розташування.

## Визначення версії

**Встановлена версія** — парсинг stdout `7za.exe`:

```
7-Zip (a) 26.00 (x86) : Copyright (c) 1999-2026 Igor Pavlov : 2026-02-12
```

Regex: `r"7-Zip\s+\S+\s+([\d\.]+)"` → `26.00`

**Остання версія** — парсинг `7-zip.org/download.html`:

```html
<B>Download 7-Zip 26.00 (2026-02-12) for Windows</B>
```

Regex: `r"Download 7-Zip\s+([\d\.]+)"` → `26.00`

**Extra URL** — пошук посилання або fallback:

```
26.00 → 2600 → https://www.7-zip.org/a/7z2600-extra.7z
```

## Стратегія оновлення

**Чому тимчасова папка?**
`7za.exe` не можна перезаписати поки він запущений (Windows file lock).

Алгоритм:
1. Розпакувати архів у `%TEMP%\7zip_update_XXXX\`
2. Скопіювати файли у `apps/7zip/` (Python вже завершив використання `7za.exe`)
3. Видалити тимчасову папку та завантажений архів

**Що оновлюється:**
- `7za.exe`, `7za.dll`, `7zxa.dll` (x86)
- `x64/7za.exe`, `x64/7za.dll`, `x64/7zxa.dll`
- `arm64/7za.exe`, `arm64/7za.dll`, `arm64/7zxa.dll`
- `Far/` (плагін Far Manager)
- `history.txt`, `License.txt`, `readme.txt`

**Що НЕ оновлюється:**
- `README.md` (наш файл, не з архіву)

## Залежності

Python-бібліотеки (self-healing pip install):
- `requests` — завантаження архіву та парсинг сторінки
- `beautifulsoup4` — парсинг HTML `7-zip.org`
- `packaging` — коректне порівняння версій

Системні (вже є в капсулі):
- `devops/pathupdate/fix_path.ps1` — реєстрація PATH

## Логи

- **Розташування:** `logs/7ziplog/7zip_log_YYYY-MM-DD.log`
- **Ротація за датою:** 7 днів (поточний день ніколи не видаляється)
- **Ротація за розміром:** > 50 MB → `_part2`, `_part3`... (поточний день не видаляється)
- **Формат:** `YYYY-MM-DD HH:MM:SS [INFO] повідомлення`
- **Дублювання:** файл + stdout (ANSI кольори в консолі)

## Аргументи CLI

```
python 7zip_manager.py [--install-only]
```

- `--install-only` — оновлення без автозакриття (для автоматизації)

## Troubleshooting

**Версія показує `0.0.0`:**
`7za.exe` не знайдено або не запускається. Перевір `apps/7zip/7za.exe`.

**Не вдалося знайти версію на сторінці:**
Структура `7-zip.org` змінилась. Перевір regex у `get_latest_info()`.

**Помилка розпакування:**
`7za.exe` повернув ненульовий код. Перевір лог у `logs/7ziplog/`.

**Файл заблоковано при копіюванні:**
Інший процес використовує `7za.exe`. Закрий всі менеджери і повтори.

**PATH не оновлюється:**
Запусти `Win+R → 7zip` з правами адміністратора (UAC).
Або вручну: `devops/pathupdate/fix_path.ps1`

## CHANGELOG

- **v1.4** (2026-02-21) — Додано портативний лаунчер для публікації на GitHub:
  - `7zip_launcher.bat` — auto-detect CAPSULE_ROOT від `%~dp0` (без хардкодованих шляхів)
  - Оновлено `README.md`: секція "Портативний лаунчер", оновлена структура файлів
- **v1.3** (2026-02-21) — Аудит перед публікацією в GitHub:
  - Хардкод `USER_ROOT` замінено на `SCRIPT_DIR → CAPSULE_ROOT` auto-detect (портативність)
  - Додано `_rotate_log_if_needed()` — >50 MB → part-файл (стандарт капсули)
  - `cleanup_old_logs()` приведено до шаблону: видалення за датою, поточний день ніколи не видаляється
  - `ensure_in_system_path()` — додано `-AutoClose` до `fix_path.ps1` (стандарт v1.7.5)
  - Додано `.gitignore`
- **v1.2** (2026-02-20) — Порядок кроків `main()`: PATH → logs → update; стиль cprint/log; `.env.example`
- **v1.1** (2026-02-20) — `__version__` + `get_manager_hash()`; ротація логів; таймер виконання
- **v1.0** (2026-02-20) — Початкова версія
