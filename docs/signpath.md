# Бесплатная подпись для Windows: SignPath Foundation

[SignPath Foundation](https://signpath.org) бесплатно подписывает программы открытых проектов настоящим сертификатом.
Подписанный `.exe` не вызывает предупреждения SmartScreen о неизвестном издателе (репутация набирается быстрее, а с их
сертификатом — сразу от имени SignPath Foundation).

## Что нужно до заявки

1. **Открытая лицензия в репозитории** — есть: MIT (файл `LICENSE`).
2. **Политика подписи в README** — короткий раздел (текст ниже): кто подписывает, кто может выпускать релизы.
3. **Сборка в GitHub Actions** — уже есть (`.github/workflows/release.yml`). После одобрения добавляется шаг
   `signpath/github-action-submit-signing-request`, и `.exe` подписывается при каждом релизе.
4. **Многофакторная вход в GitHub** у всех, кто может выпускать релизы.

## Раздел для README (вставить после одобрения)

```markdown
## Политика подписи

Бесплатная подпись кода предоставлена [SignPath.io](https://about.signpath.io), сертификат — [SignPath Foundation](https://signpath.org).

- Авторы и проверяющие: участники репозитория fedintsevqq/govorilka
- Одобряют выпуск: владелец репозитория
- Программа не передаёт никаких данных, кроме проверки обновлений на github.com и скачивания моделей с huggingface.co по действию пользователя.
```

## Текст заявки (форма на signpath.org/apply, по-английски)

- **Project name:** Govorilka
- **Repository:** https://github.com/fedintsevqq/govorilka
- **License:** MIT
- **Description:** Govorilka is a free desktop app for Windows and macOS that transcribes calls and audio files
  locally. It records the microphone and the system audio, recognizes Russian speech with the open GigaAM model
  (onnx-asr), separates speakers, saves Markdown transcripts and can summarize a call with a local LLM
  (llama.cpp). Nothing leaves the computer: no accounts, no cloud processing, no telemetry.
- **Why code signing:** the Windows build is a single portable `.exe` downloaded from GitHub Releases. Without a
  signature SmartScreen warns every new user about an unknown publisher, which stops many non-technical users.
- **Build process:** GitHub Actions (`windows-latest`, PyInstaller), reproducible from the tagged source; artifacts are
  uploaded to GitHub Releases with SHA-256 digests.
- **Artifacts to sign:** `Govorilka-<version>-windows-x64.exe`.
- **Maintainers:** _(GitHub-логины тех, кто выпускает релизы)_

## Пока подписи нет

Установка одной командой обходит предупреждение: файл, скачанный PowerShell, не помечается как «из интернета».

```powershell
irm https://raw.githubusercontent.com/fedintsevqq/govorilka/main/install.ps1 | iex
```
