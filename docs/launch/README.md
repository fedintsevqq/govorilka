# Запуск Говорилки: как найти первых пользователей

Говорилка — на русском и для русской речи, поэтому главные площадки русскоязычные. Порядок важен: сначала
«витрина» (сайт, GitHub, установка одной командой), потом рассказ, потом каталоги.

## 1. Витрина

| Шаг | Что даёт | Статус |
|---|---|---|
| Сайт на GitHub Pages (`site/`) | одна понятная ссылка для постов | работает: https://fedintsevqq.github.io/govorilka/ |
| Описание, темы и сайт у репозитория | находят по поиску GitHub (transcription, russian, offline…) | сделано |
| Картинка-превью репозитория | красивая карточка, когда ссылкой делятся в Telegram | загрузить `site/img/recording-dark.png` в Settings → Social preview (только вручную) |
| Homebrew (`packaging/homebrew/`) | `brew install --cask fedintsevqq/tap/govorilka` | работает: github.com/fedintsevqq/homebrew-tap, CI ставит на Apple Silicon и Intel |
| winget (`packaging/winget/`) | `winget install fedintsevqq.Govorilka` | pull request microsoft/winget-pkgs#462252, ждёт подписи CLA, потом проверка Microsoft |

При каждом новом релизе: обновить версию и SHA-256 в `packaging/homebrew/govorilka.rb` (и в homebrew-tap) и
отправить новый манифест в winget (`wingetcreate update fedintsevqq.Govorilka -v <версия> -u <ссылка> --submit`).

## 2. Рассказ — тексты готовы, публикуете вы

Публикации идут от вашего имени и с вашего аккаунта: тут решаете вы. Тексты — в этой папке.

| Площадка | Файл | Когда | Чего ждать |
|---|---|---|---|
| Хабр | `habr.md` | первым, в будний день утром по Москве | главный источник: разработчики, менеджеры, QA |
| vc.ru | `vc.md` | через 2–3 дня после Хабра | предприниматели и менеджеры |
| Telegram | `telegram.md` | параллельно, по одному каналу в день | быстрые установки |
| Свои знакомые и рабочие чаты | `telegram.md`, первый пост | сразу | первые отзывы и баги |

## 3. Каталоги — тексты готовы, нужны аккаунты

`catalogs.md`: AlternativeTo, Softpedia, MacUpdate, Uptodown, Product Hunt, awesome-списки на GitHub.

## Как понять, что работает

- Скачивания: `gh api repos/fedintsevqq/govorilka/releases --jq '.[].assets[] | .name + " " + (.download_count|tostring)'`
- Звёзды и откуда приходят: GitHub → Insights → Traffic (рефереры показывают, какая площадка сработала).
- Отзывы и баги: Issues на GitHub, комментарии к статьям.

## Что заметно поможет

1. **Подпись кода.** Предупреждения Windows и macOS отпугивают обычных людей сильнее всего. Заявка в SignPath
   подана (`docs/signpath.md`); сертификат Apple — $99 в год.
2. **Короткое видео на 30–60 секунд**: созвон → текст по ходу → итоги. В постах видео работает лучше скриншотов.
3. **Отвечать на каждый комментарий в первые сутки** после статьи: это поднимает её в ленте.
