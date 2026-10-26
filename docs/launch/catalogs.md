# Каталоги программ

Каждый каталог даёт ссылку и немного поискового трафика. Заявка — форма на сайте, нужен аккаунт.

| Каталог | Зачем | Где подать |
|---|---|---|
| AlternativeTo | люди ищут «альтернативу Otter / Granola / MacWhisper» | alternativeto.net → Add application |
| GitHub Topics | поиск по темам на GitHub | темы репозитория (делаю сам по вашему разрешению) |
| Awesome-списки | awesome-russian-nlp, awesome-whisper, awesome-macos | pull request в репозиторий списка |
| Product Hunt | англоязычная аудитория, но даёт ссылку и отзывы | producthunt.com/posts/new |
| Softpedia | большой каталог, сам находит программы | softpedia.com → Submit |
| MacUpdate | каталог для Mac | macupdate.com → Submit app |
| Uptodown | каталог для Windows | uptodown.com → Developers |
| winget | `winget install Govorilka` на Windows | pull request в microsoft/winget-pkgs (манифест готов) |
| Homebrew | `brew install --cask govorilka` на Mac | свой репозиторий-«кран» (формула готова) |

## Описание для каталогов

**Коротко (до 80 знаков):** Расшифровка созвонов на русском на вашем компьютере, без облака

**Подробно:**

Говорилка превращает созвон в текст прямо во время разговора. Пишет ваш микрофон и звук компьютера отдельно,
различает собеседников, после звонка составляет итоги и задачи. Расшифровывает аудио и видео любой длины. Есть
диктовка в любое окно по горячей клавише.

Распознавание работает на компьютере, без интернета и без бота в звонке: модель GigaAM v3 для русского языка,
ещё девять моделей в каталоге. Итоги пишет локальная модель (Qwen, YandexGPT) через llama.cpp.

Бесплатно, открытый код (MIT). Windows 10/11, macOS 13+ (Apple Silicon и Intel).

**Категории:** Productivity, Audio, Speech recognition, Meeting notes.
**Метки:** transcription, speech-to-text, russian, offline, meeting-notes, dictation, gigaam, whisper, open-source.

## Англоязычное описание (Product Hunt, AlternativeTo)

**Tagline:** Offline meeting transcription for Russian speech — free and open source

Govorilka turns calls into text as you talk: your microphone and the other side separately, speakers told apart,
summaries and action items after the call. Everything runs on your computer — no cloud, no bot in the meeting.
Built around GigaAM v3, the strongest open speech model for Russian; nine more models (Whisper, Parakeet) are in the
catalog. Dictation into any app by a hotkey. Windows and macOS, MIT license. The interface is in Russian.
