"""Сетевые мелочи, общие для загрузок, проверки обновлений, нейросетей по API и своего llama-server."""

from __future__ import annotations

import ipaddress
import logging
import ssl
import urllib.error
import urllib.request
from urllib.parse import urlparse

log = logging.getLogger(__name__)


def ssl_context() -> ssl.SSLContext | None:
    """Сертификаты берём у самой системы через truststore. У собранного приложения на Mac у Python нет своего набора
    корневых сертификатов: без этого любой запрос падал бы с CERTIFICATE_VERIFY_FAILED. Запасной путь — certifi,
    дальше настройки Python по умолчанию."""
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


def use_system_certificates() -> None:
    """Системные сертификаты для всех библиотек, а не только для наших запросов: модели распознавания качает
    huggingface_hub, и за корпоративным прокси с проверкой TLS он без этого падал бы с CERTIFICATE_VERIFY_FAILED.
    Вызывать при запуске, до первого запроса."""
    try:
        import truststore

        truststore.inject_into_ssl()
    except Exception:  # noqa: BLE001 — без truststore работают сертификаты certifi
        log.info("Системные сертификаты не подключены, работают встроенные")


def is_loopback(url: str) -> bool:
    """Адрес на этом же компьютере: 127.0.0.1, ::1 или localhost. Сравниваем разобранное имя хоста, а не начало
    строки: «http://127.0.0.1.example.com» — чужой сервер."""
    host = (urlparse(url).hostname or "").lower()
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Переадресацию не выполняем: urllib переносит на новый адрес все заголовки, включая ключ API."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001, ANN201, PLR0913
        raise urllib.error.HTTPError(req.full_url, code, f"сервер перенаправляет на {urlparse(newurl).hostname}", headers, fp)


def api_opener(context: ssl.SSLContext | None) -> urllib.request.OpenerDirector:
    """Для запросов с ключом API: без переадресаций (ключ не уйдёт на другой адрес)."""
    return urllib.request.build_opener(urllib.request.HTTPSHandler(context=context), _NoRedirect())


def local_opener() -> urllib.request.OpenerDirector:
    """Для своего llama-server на 127.0.0.1: мимо системного прокси (иначе запрос уходил бы на прокси и не доходил)
    и без переадресаций."""
    return urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
