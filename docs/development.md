# Разработка

[← К описанию Говорилки](../README.md) · [Руководство пользователя](guide.md) · [Как помочь проекту](../CONTRIBUTING.md)

Интерфейс: PySide6 + QML (см. [docs/adr/0001-qt-ui.md](adr/0001-qt-ui.md)). Сборка выросла на 50 МБ из-за Qt.

Запуск из исходников, Windows:

```powershell
install.bat
Govorilka.bat
```

Mac (нужны Python 3.12 с python.org и Xcode Command Line Tools; на Intel поставится onnxruntime 1.23.2):

```bash
python3.12 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
mkdir -p bin && swiftc -O mac/audiotap.swift -o bin/govorilka-audiotap
.venv/bin/python -m govorilka
```

Тесты: `python -m pytest -q tests` (Qt работает без экрана, программная отрисовка). Самопроверки без окна, их же гоняет CI в собранных приложениях:

- `Govorilka --selftest tests/fixtures/speech_ru.wav` — файл через весь путь (декодер, детектор речи, распознавание, голоса, эхоподавление), результат в `selftest.txt`;
- `Govorilka --selftest-ui` — окна QML грузятся без ошибок и рисуют не пустоту, иконки на месте, результат в `selftest-ui.txt`.

Снимки интерфейса для проверки глазами: `python tools/shots.py <папка> [dark|light]`. Иконки пересобираются командой `python tools/make_icons.py` (нужен Pillow).

Сборка `.exe` и `.dmg` идёт в GitHub Actions ([release.yml](../.github/workflows/release.yml)). Каждый push в `main` собирает обе версии и проверяет их на реальной модели. Тег `v*` дополнительно публикует релиз:

```bash
git tag v0.3.0 && git push origin v0.3.0
```

Структура `govorilka/`:

- `hotkeys.py` — горячая клавиша: распознавание комбинации, запись новой, опрос клавиш Windows и Mac;
- `autostart.py` — запуск при входе в систему (реестр Windows, LaunchAgent на Mac);
- `single_instance.py` — не даёт запустить вторую копию;
- `controller.py` — логика приложения без интерфейса: запись, пауза, отметки, файлы, горячая клавиша, отправка нейросети;
- `qt/` — интерфейс: `backend.py` (мост к QML: сигналы из потоков, слоты, свойства), `models.py` (лента транскрипта), `tray.py` (значок в трее и строке меню), `icons.py` (набор иконок из SVG), `app.py` (запуск);
- `qml/` — окна и компоненты: `Main.qml`, `SettingsWindow.qml`, `RadialMenu.qml`, `RecordingPill.qml`, `Theme.qml` (цвета, размеры, движение) и мелкие детали вроде `Bezel.qml` и `PillButton.qml`;
- `assets/` — иконка приложения (`.png`, `.ico`, `.icns`) и значки для трея;
- `audio.py` — выбирает захват звука под ОС;
- `audio_windows.py` — микрофон и loopback через WASAPI;
- `audio_mac.py` — микрофон и системный звук через помощника `mac/audiotap.swift` (AVAudioEngine и ScreenCaptureKit);
- `vad.py` — детектор речи Silero (нейросеть 2 МБ) и запасной по громкости;
- `segmenter.py` — нарезка звука на фразы по паузам, длинную фразу режет в самом тихом месте;
- `speakers.py` — отпечатки голосов (wespeaker), пересчёт голосов по ходу записи и после неё;
- `refine.py` — уточнение подписей после созвона, перенос имён;
- `voices.py` — память голосов знакомых (по желанию, локально);
- `models.py` — скачивание моделей с процентом;
- `engines.py` — все модели приложения;
- `updates.py`, `version.py` — проверка обновлений на GitHub и номер версии (его записывает сборка);
- `asr_models.py` — какие модели распознавания поддерживаются: каталог, int8, свои с Hugging Face и из папки;
- `filejob.py` — расшифровка аудио- и видеофайлов через PyAV;
- `win_devices.py` — выбранные в Windows устройства (Core Audio);
- `recognizer.py` — GigaAM через onnx-asr;
- `pipeline.py` — поток на каждый источник: звук → фразы → текст;
- `echo.py` — фильтр эха колонок;
- `session.py` — одна запись: устройства, потоки, автосохранение;
- `transcript.py` — реплики и Markdown;
- `claude_bridge.py` — запуск Claude Code в терминале;
- `ai.py` — какие нейросети поддерживаются, проверка страны, текст для чатов;
- `desktop_apps.py` — поиск и запуск настольных ChatGPT и Claude;
- `names.py` — имена собеседников из самого разговора;
- `selftest.py` — самопроверка собранного приложения;
- `app.py` — точка входа: журнал, самопроверки, запуск интерфейса.

Модели и настройки собранное приложение хранит в `%LOCALAPPDATA%\Govorilka` (Windows) или `~/Library/Application Support/Govorilka` (Mac). Там же лежит `govorilka.log`.
