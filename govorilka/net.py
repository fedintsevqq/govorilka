"""Сетевые мелочи, общие для проверки доступности нейросетей и проверки обновлений."""

from __future__ import annotations

import ssl


def ssl_context() -> ssl.SSLContext | None:
    """Сертификаты берём у самой системы через truststore (он уже в сборке: так качаются модели). У собранного
    приложения на Mac у Python нет своего набора корневых сертификатов: без этого любой запрос падал бы с
    CERTIFICATE_VERIFY_FAILED. Запасной путь — certifi, дальше настройки Python по умолчанию."""
    try:
        import truststore

        return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    except Exception:  # noqa: BLE001
        pass
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:  # noqa: BLE001
        return None
