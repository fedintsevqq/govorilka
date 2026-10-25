"""Набор иконок Говорилки: тонкие линии 1.6 px на сетке 24×24, свои, без сторонних наборов.

QML просит картинку по адресу `image://icon/<имя>/<цвет>`, а провайдер рисует SVG в нужном размере
и цвете. Иконки остаются чёткими на любом масштабе экрана и сразу подходят к светлой и тёмной теме.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QByteArray, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtQuick import QQuickImageProvider
from PySide6.QtSvg import QSvgRenderer

log = logging.getLogger(__name__)

STROKE = 'fill="none" stroke="{c}" stroke-opacity="{o}" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"'
FILL = 'fill="{c}" fill-opacity="{o}" stroke="none"'

# имя: (тело SVG, залитая ли иконка)
ICONS: dict[str, tuple[str, bool]] = {
    "mic": ('<rect x="9" y="3" width="6" height="11.5" rx="3"/><path d="M5.5 11a6.5 6.5 0 0 0 13 0M12 17.5V21M8.5 21h7"/>', False),
    "record": ('<circle cx="12" cy="12" r="6.5"/>', True),
    "stop": ('<rect x="6.5" y="6.5" width="11" height="11" rx="2.6"/>', True),
    "pause": ('<rect x="6.8" y="5.5" width="3.4" height="13" rx="1.3"/><rect x="13.8" y="5.5" width="3.4" height="13" rx="1.3"/>', True),
    "play": ('<path d="M8.3 5.6c0-.8.9-1.3 1.6-.9l9 5.6c.7.4.7 1.4 0 1.8l-9 5.6c-.7.4-1.6-.1-1.6-.9z"/>', True),
    "star": (
        '<path d="M12 3.9l2.45 5 5.5.8-3.98 3.88.94 5.48L12 16.48l-4.91 2.58.94-5.48-3.98-3.88 5.5-.8z"/>',
        False,
    ),
    "sparkle": (
        '<path d="M12 3.2c.55 4.4 3.45 7.3 7.9 7.9-4.45.6-7.35 3.5-7.9 7.9-.55-4.4-3.45-7.3-7.9-7.9 4.45-.6 7.35-3.5 7.9-7.9z"/>'
        '<path d="M19 17.5v3M17.5 19h3"/>',
        False,
    ),
    "file": (
        '<path d="M13.5 3.5H7.8a2.3 2.3 0 0 0-2.3 2.3v12.4a2.3 2.3 0 0 0 2.3 2.3h8.4a2.3 2.3 0 0 0 2.3-2.3V8.5z"/>'
        '<path d="M13.5 3.5v5h5M9 13h6M9 16.5h4"/>',
        False,
    ),
    "open": ('<path d="M12 15.5V4M7.8 8.2 12 4l4.2 4.2M5 20h14"/>', False),
    "save": ('<path d="M12 4v11.5M7.8 11.3 12 15.5l4.2-4.2M5 20h14"/>', False),
    "folder": (
        '<path d="M3.5 7.6A2.3 2.3 0 0 1 5.8 5.3h3.6l2.1 2.2h6.7a2.3 2.3 0 0 1 2.3 2.3v8a2.3 2.3 0 0 1-2.3 2.3H5.8'
        'a2.3 2.3 0 0 1-2.3-2.3z"/>',
        False,
    ),
    "settings": (
        '<path d="M4 7.5h8.5M16.5 7.5H20M4 16.5h3.5M11.5 16.5H20"/><circle cx="14.5" cy="7.5" r="2"/>'
        '<circle cx="9.5" cy="16.5" r="2"/>',
        False,
    ),
    "window": ('<rect x="3.5" y="5" width="17" height="14" rx="2.6"/><path d="M3.5 9.2h17M6.6 7.1h.01M8.9 7.1h.01"/>', False),
    "copy": (
        '<rect x="8.5" y="8.5" width="11.5" height="11.5" rx="2.4"/>'
        '<path d="M15.5 8.5V6.4A2.4 2.4 0 0 0 13.1 4H6.4A2.4 2.4 0 0 0 4 6.4v6.7a2.4 2.4 0 0 0 2.4 2.4h2.1"/>',
        False,
    ),
    "close": ('<path d="M6.5 6.5l11 11M17.5 6.5l-11 11"/>', False),
    "trash": (
        '<path d="M4.5 7h15M9.5 7V5.2c0-.7.5-1.2 1.2-1.2h2.6c.7 0 1.2.5 1.2 1.2V7"/>'
        '<path d="M6.5 7l.8 11.2c.1 1 .9 1.8 1.9 1.8h5.6c1 0 1.8-.8 1.9-1.8L17.5 7M10.3 11v5.2M13.7 11v5.2"/>',
        False,
    ),
    "check": ('<path d="M5 12.6l4.3 4.2L19 7.2"/>', False),
    "chevron": ('<path d="M9.5 6l6 6-6 6"/>', False),
    "keyboard": (
        '<rect x="3" y="6" width="18" height="12" rx="2.6"/><path d="M7 10h.01M10.3 10h.01M13.7 10h.01M17 10h.01M7.5 14h9"/>',
        False,
    ),
    "wave": ('<path d="M4 11v2M8 8v8M12 5v14M16 8.5v7M20 11v2"/>', False),
    "person": ('<circle cx="12" cy="8.3" r="3.6"/><path d="M5.2 20a6.8 6.8 0 0 1 13.6 0"/>', False),
    "people": (
        '<circle cx="9" cy="8.6" r="3.2"/><path d="M3.3 19.5a5.7 5.7 0 0 1 11.4 0"/>'
        '<path d="M15.5 5.6a3.2 3.2 0 0 1 0 6.1M17.5 14.3a5.7 5.7 0 0 1 3.2 5.2"/>',
        False,
    ),
    "edit": ('<path d="M4.5 19.5h3.8L18.6 9.2a2.2 2.2 0 0 0-3.8-3.8L4.5 15.7z"/><path d="M13.6 6.6l3.8 3.8"/>', False),
    "speaker": (
        '<path d="M4.5 9.6v4.8h3.3L12.5 18V6L7.8 9.6z"/><path d="M15.8 9.2a4 4 0 0 1 0 5.6M18.4 6.6a7.6 7.6 0 0 1 0 10.8"/>',
        False,
    ),
    "echo": (
        '<path d="M4.5 9.6v4.8h3.3L12.5 18V6L7.8 9.6z"/><path d="M16 9.5l4 5M20 9.5l-4 5"/>',
        False,
    ),
    "power": ('<path d="M12 4v7"/><path d="M7.3 6.9a7 7 0 1 0 9.4 0"/>', False),
    "bolt": ('<path d="M13 3.5 5.5 13.2h5.8L10.6 20.5l7.9-10h-5.8z"/>', False),
}


def svg(name: str, color: str, opacity: float = 1.0) -> bytes:
    """SVG Tiny (так его понимает QtSvg): цвет hex, прозрачность отдельным атрибутом."""
    body, filled = ICONS[name]
    style = (FILL if filled else STROKE).format(c=color, o=f"{opacity:.3f}")
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><g {style}>{body}</g></svg>'.encode()


def render(name: str, color: QColor | str, size: int) -> QImage:
    color = QColor(color)
    image = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    renderer = QSvgRenderer(QByteArray(svg(name, color.name(QColor.NameFormat.HexRgb), color.alphaF())))
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    renderer.render(painter, QRectF(0, 0, size, size))
    painter.end()
    return image


class IconProvider(QQuickImageProvider):
    """`image://icon/mic/#ffffff` → иконка микрофона белым цветом нужного размера."""

    def __init__(self) -> None:
        super().__init__(QQuickImageProvider.ImageType.Image)

    def requestImage(self, icon_id: str, size: QSize, requested: QSize) -> QImage:  # noqa: N802 — имя из Qt
        name, _, color = icon_id.partition("/")
        if name not in ICONS:
            log.warning("В QML запрошена несуществующая иконка «%s»", name)
            blank = QImage(24, 24, QImage.Format.Format_ARGB32_Premultiplied)
            blank.fill(Qt.GlobalColor.transparent)
            size.setWidth(24)
            size.setHeight(24)
            return blank
        side = max(requested.width(), requested.height(), 16) if requested.isValid() else 48
        color = color or "ffffff"
        image = render(name, QColor(color if color.startswith("#") else "#" + color), side)
        size.setWidth(side)
        size.setHeight(side)
        return image
