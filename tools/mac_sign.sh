#!/bin/bash
# Подпись Govorilka.app в CI. Использование: tools/mac_sign.sh dist/Govorilka.app
#
# Если в секретах репозитория есть постоянный самоподписанный сертификат (MAC_CERT_P12, MAC_CERT_PASSWORD), приложение
# подписывается им, и «требование подписи» (designated requirement) остаётся одним и тем же в каждой версии: macOS
# не сбрасывает разрешения (микрофон, запись экрана, мониторинг ввода) при обновлении и переустановке.
# Без секретов подпись «пустая» (ad-hoc): разрешения привязаны к хешу сборки и слетают после каждого обновления.
# Сертификат создаёт tools/make_mac_cert.sh один раз.
set -uo pipefail
APP="$1"
IDENTIFIER="io.github.fedintsevqq.govorilka"
TMP="${RUNNER_TEMP:-/tmp}"

adhoc() {
  echo "::warning::подпись ad-hoc: разрешения macOS будут слетать после каждого обновления (нет рабочего сертификата MAC_CERT_P12)"
  codesign --force --deep --sign - "$APP"
}

if [ -z "${MAC_CERT_P12:-}" ]; then
  adhoc
  exit 0
fi

KEYCHAIN="$TMP/build.keychain-db"
PASSWORD="$(uuidgen)"
echo "$MAC_CERT_P12" | base64 --decode > "$TMP/cert.p12"
security create-keychain -p "$PASSWORD" "$KEYCHAIN" &&
security set-keychain-settings -lut 3600 "$KEYCHAIN" &&
security unlock-keychain -p "$PASSWORD" "$KEYCHAIN" &&
security import "$TMP/cert.p12" -k "$KEYCHAIN" -P "${MAC_CERT_PASSWORD:-}" -T /usr/bin/codesign &&
security set-key-partition-list -S apple-tool:,apple: -s -k "$PASSWORD" "$KEYCHAIN" >/dev/null &&
security list-keychains -d user -s "$KEYCHAIN" login.keychain-db
HASH="$(security find-identity -p codesigning "$KEYCHAIN" | awk '/"Govorilka/{print $2; exit}')"
if [ -z "$HASH" ]; then
  echo "::warning::сертификат Govorilka не найден в связке ключей"
  adhoc
  exit 0
fi

# изнутри наружу: сначала библиотеки и помощник, потом само приложение с постоянным требованием
find "$APP" -type f \( -name "*.dylib" -o -name "*.so" \) -exec codesign --force --sign "$HASH" {} + || true
HELPER="$(find "$APP" -name govorilka-audiotap -type f | head -1)"
if [ -n "$HELPER" ]; then
  codesign --force --sign "$HASH" --identifier "$IDENTIFIER.audiotap" "$HELPER" || true
fi
REQUIREMENT="designated => identifier \"$IDENTIFIER\" and certificate leaf = H\"$HASH\""
if codesign --force --deep --sign "$HASH" --identifier "$IDENTIFIER" -r="$REQUIREMENT" "$APP" &&
   codesign --verify --deep --strict "$APP" &&
   ! codesign -d -r- "$APP" 2>&1 | grep -q cdhash; then
  echo "Подписано постоянным сертификатом: разрешения macOS переживут обновление"
else
  echo "::warning::подпись сертификатом не удалась"
  adhoc
fi
