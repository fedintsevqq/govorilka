#!/bin/bash
# Один раз создаёт постоянный самоподписанный сертификат для подписи Говорилки (запускайте на любом Mac или Linux).
# Результат: файл govorilka-cert.p12.base64 для секрета MAC_CERT_P12 и пароль для секрета MAC_CERT_PASSWORD.
# Сам govorilka-cert.p12 храните в надёжном месте и в репозиторий не добавляйте: потеряете, подпись сменится,
# и разрешения macOS у пользователей слетят ещё раз.
#
#   bash tools/make_mac_cert.sh
#   gh secret set MAC_CERT_P12 < govorilka-cert.p12.base64
#   gh secret set MAC_CERT_PASSWORD      # вставьте пароль из вывода
set -euo pipefail
PASSWORD="$(openssl rand -hex 12)"
openssl req -x509 -newkey rsa:2048 -nodes -days 36500 -keyout govorilka-key.pem -out govorilka-cert.pem \
  -subj "/CN=Govorilka Self-Signed" -addext "keyUsage=critical,digitalSignature" -addext "extendedKeyUsage=critical,codeSigning"
openssl pkcs12 -export -inkey govorilka-key.pem -in govorilka-cert.pem -out govorilka-cert.p12 -passout "pass:$PASSWORD"
base64 < govorilka-cert.p12 | tr -d '\n' > govorilka-cert.p12.base64
rm -f govorilka-key.pem govorilka-cert.pem
echo "Пароль для MAC_CERT_PASSWORD: $PASSWORD"
echo "Файл для MAC_CERT_P12: govorilka-cert.p12.base64"
