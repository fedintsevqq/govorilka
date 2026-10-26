#!/bin/bash
# Установка Говорилки на Mac одной командой (Терминал):
#
#   curl -fsSL https://raw.githubusercontent.com/fedintsevqq/govorilka/main/install.sh | bash
#
# Скрипт берёт последний релиз с GitHub под ваш чип (Apple Silicon или Intel), сверяет SHA-256 образа с тем, что
# опубликовал GitHub, копирует Говорилку в «Программы» и запускает её. Файл, скачанный через curl, macOS не помечает
# как «из интернета», поэтому не будет окна «не удаётся проверить разработчика».
set -euo pipefail

# всё тело — в функции, а вызов — последней строкой: если загрузка через curl оборвётся на середине, bash не выполнит
# половину скрипта (например, удаление старой версии без копирования новой)
main() {
REPO="fedintsevqq/govorilka"
APP="${GOVORILKA_APP:-/Applications/Govorilka.app}"  # GOVORILKA_APP и GOVORILKA_NO_OPEN — для проверки в CI
case "$(uname -m)" in
  arm64) SUFFIX="macos-apple-silicon.dmg" ;;
  *) SUFFIX="macos-intel.dmg" ;;
esac

WORK="$(mktemp -d)"
MOUNT="$WORK/mount"
cleanup() {
  hdiutil detach "$MOUNT" -quiet -force 2>/dev/null || true
  rm -rf "$WORK"
}
trap cleanup EXIT

echo "Говорилка: ищу последнюю версию..."
AUTH=()
[[ -n "${GITHUB_TOKEN:-}" ]] && AUTH=(-H "Authorization: Bearer $GITHUB_TOKEN")  # необязательно: в CI, чтобы не упереться в лимит
curl -fsSL -H "User-Agent: Govorilka-installer" "${AUTH[@]}" "https://api.github.com/repos/$REPO/releases/latest" -o "$WORK/release.json"

# plutil понимает JSON и есть на любом Mac: без python и jq
count=$(plutil -extract assets raw -o - "$WORK/release.json")
name="" url="" digest=""
for ((i = 0; i < count; i++)); do
  candidate=$(plutil -extract "assets.$i.name" raw -o - "$WORK/release.json")
  if [[ "$candidate" == *"$SUFFIX" ]]; then
    name="$candidate"
    url=$(plutil -extract "assets.$i.browser_download_url" raw -o - "$WORK/release.json")
    digest=$(plutil -extract "assets.$i.digest" raw -o - "$WORK/release.json" 2>/dev/null || true)
    break
  fi
done
[[ -n "$name" ]] || { echo "В последнем релизе нет файла $SUFFIX" >&2; exit 1; }
[[ "$digest" == sha256:* ]] || { echo "GitHub не опубликовал контрольную сумму: скачайте файл вручную со страницы релиза" >&2; exit 1; }

echo "Скачиваю $name..."
curl -fL --progress-bar -H "User-Agent: Govorilka-installer" "$url" -o "$WORK/govorilka.dmg"
actual=$(shasum -a 256 "$WORK/govorilka.dmg" | awk '{print $1}')
[[ "$actual" == "${digest#sha256:}" ]] || { echo "Контрольная сумма не совпала: файл повреждён или подменён. Попробуйте ещё раз" >&2; exit 1; }

mkdir -p "$MOUNT"
hdiutil attach -nobrowse -readonly -noautoopen -mountpoint "$MOUNT" "$WORK/govorilka.dmg" -quiet
if [[ -z "${GOVORILKA_NO_OPEN:-}" ]]; then
  osascript -e 'quit app "Govorilka"' 2>/dev/null || true
  sleep 1
fi
rm -rf "$APP"
ditto "$MOUNT/Govorilka.app" "$APP"
xattr -dr com.apple.quarantine "$APP" 2>/dev/null || true

# SHA-256 уже сверен с GitHub; подпись проверяем для журнала (у старых версий она была временной)
codesign --verify --deep --strict "$APP" 2>/dev/null || echo "Внимание: подпись программы не прошла строгую проверку" >&2
if [[ -n "${GOVORILKA_NO_OPEN:-}" ]]; then
  echo "Готово: $APP"
  exit 0
fi
echo "Готово: Говорилка в «Программах». Запускаю..."
open "$APP"
}

main "$@"
