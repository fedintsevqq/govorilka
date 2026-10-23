"""Рисует иконки Говорилки и собирает файлы для Windows (.ico), macOS (.icns) и трея.

Запуск из корня проекта:  python tools/make_icons.py
Результат кладётся в govorilka/assets/ и коммитится: сборка не рисует иконки заново.

Знак: «стеклянная» плитка в форме squircle-квадрата (как у значков macOS) с градиентом ирис → лазурь,
в центре — белая волна голоса из пяти столбиков разной высоты, сверху лёгкий блик.
Для строки меню Mac и трея Windows — отдельные монохромные значки (микрофон), три состояния.
"""

from __future__ import annotations

import math
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image  # noqa: E402
from PySide6.QtCore import QPointF, QRectF, Qt  # noqa: E402
from PySide6.QtGui import QBrush, QColor, QGuiApplication, QImage, QLinearGradient, QPainter, QPainterPath, QPen, QRadialGradient  # noqa: E402

from govorilka.qt.icons import render  # noqa: E402

ASSETS = Path(__file__).resolve().parent.parent / "govorilka" / "assets"
IRIS, AZURE = QColor("#7A6BFF"), QColor("#34B2FF")
BARS = (0.34, 0.62, 1.0, 0.7, 0.42)  # высоты столбиков волны голоса


def squircle(rect: QRectF, n: float = 5.0) -> QPainterPath:
    """Суперэллипс |x|ⁿ+|y|ⁿ=1: углы мягче, чем у обычного скругления, как у иконок Apple."""
    path = QPainterPath()
    cx, cy, a, b = rect.center().x(), rect.center().y(), rect.width() / 2, rect.height() / 2
    steps = 360
    for i in range(steps + 1):
        t = 2 * math.pi * i / steps
        c, s = math.cos(t), math.sin(t)
        x = cx + a * math.copysign(abs(c) ** (2 / n), c)
        y = cy + b * math.copysign(abs(s) ** (2 / n), s)
        path.moveTo(x, y) if i == 0 else path.lineTo(x, y)
    path.closeSubpath()
    return path


def app_icon(size: int = 1024) -> QImage:
    image = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    p = QPainter(image)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    inset = size * 0.085  # поля под тень, как в шаблоне macOS
    body = QRectF(inset, inset, size - 2 * inset, size - 2 * inset)
    plate = squircle(body)

    # мягкая тень под плиткой
    for i in range(14, 0, -1):
        shadow = QColor(8, 6, 40, int(7 * (15 - i) / 3))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(shadow)
        grow = i * size * 0.0042
        p.drawPath(squircle(body.adjusted(-grow, -grow * 0.2, grow, grow * 1.8)))

    gradient = QLinearGradient(body.topLeft(), body.bottomRight())
    gradient.setColorAt(0.0, IRIS)
    gradient.setColorAt(1.0, AZURE)
    p.setBrush(QBrush(gradient))
    p.setPen(Qt.PenStyle.NoPen)
    p.drawPath(plate)

    p.save()
    p.setClipPath(plate)
    glow = QRadialGradient(QPointF(body.center().x(), body.top() + body.height() * 0.12), body.width() * 0.75)
    glow.setColorAt(0.0, QColor(255, 255, 255, 120))
    glow.setColorAt(0.55, QColor(255, 255, 255, 20))
    glow.setColorAt(1.0, QColor(255, 255, 255, 0))
    p.setBrush(QBrush(glow))
    p.drawRect(body)
    shade = QLinearGradient(body.topLeft(), body.bottomLeft())
    shade.setColorAt(0.55, QColor(0, 0, 40, 0))
    shade.setColorAt(1.0, QColor(10, 0, 60, 70))
    p.setBrush(QBrush(shade))
    p.drawRect(body)
    p.restore()

    # внутренний контур-блик по краю плитки
    rim = QPen(QColor(255, 255, 255, 70), size * 0.004)
    p.setPen(rim)
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawPath(squircle(body.adjusted(size * 0.003, size * 0.003, -size * 0.003, -size * 0.003)))

    # волна голоса: пять скруглённых столбиков с белым градиентом
    bar_w, gap = body.width() * 0.082, body.width() * 0.058
    total = len(BARS) * bar_w + (len(BARS) - 1) * gap
    x = body.center().x() - total / 2
    max_h = body.height() * 0.5
    for i, height in enumerate(BARS):
        h = max_h * height
        rect = QRectF(x + i * (bar_w + gap), body.center().y() - h / 2, bar_w, h)
        fill = QLinearGradient(rect.topLeft(), rect.bottomLeft())
        fill.setColorAt(0.0, QColor(255, 255, 255, 255))
        fill.setColorAt(1.0, QColor(255, 255, 255, 215))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(fill))
        p.drawRoundedRect(rect, bar_w / 2, bar_w / 2)
    p.end()
    return image


def tray_icon(state: str, size: int = 64, template: bool = False) -> QImage:
    """Микрофон для трея. template — чёрный силуэт: macOS сама красит его под тему строки меню."""
    color = QColor("#000000") if template else QColor("#F1F2F5")
    image = render("mic", color, size)
    if state in ("recording", "paused"):
        dot = QColor("#FF4D61") if state == "recording" else QColor("#FFC14D")
        p = QPainter(image)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        radius = size * 0.2
        center = QPointF(size - radius - size * 0.02, radius + size * 0.02)
        p.setPen(Qt.PenStyle.NoPen if template else QPen(QColor(0, 0, 0, 90), 1.0))
        p.setBrush(dot)
        p.drawEllipse(center, radius, radius)
        p.end()
    return image


def status_disc(color: str, size: int = 64) -> QImage:
    """Цветной диск с белым микрофоном: состояние записи заметно и в светлой, и в тёмной строке меню."""
    image = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    p = QPainter(image)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(color))
    margin = size * 0.04
    p.drawEllipse(QRectF(margin, margin, size - 2 * margin, size - 2 * margin))
    glyph = render("mic", QColor("#FFFFFF"), int(size * 0.62))
    p.drawImage(int((size - glyph.width()) / 2), int((size - glyph.height()) / 2), glyph)
    p.end()
    return image


def main() -> None:
    _ = QGuiApplication(["icons"])
    ASSETS.mkdir(parents=True, exist_ok=True)
    big = app_icon(1024)
    big.save(str(ASSETS / "icon.png"))

    sizes = [16, 24, 32, 48, 64, 128, 256, 512, 1024]
    frames = {}
    for size in sizes:
        scaled = big.scaled(size, size, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
        path = ASSETS / f"_tmp_{size}.png"
        scaled.save(str(path))
        frames[size] = Image.open(path).convert("RGBA")
    pil = frames[1024]
    pil.save(ASSETS / "icon.ico", sizes=[(s, s) for s in (16, 24, 32, 48, 64, 128, 256)])
    pil.save(ASSETS / "icon.icns", sizes=[(s, s) for s in (16, 32, 64, 128, 256, 512, 1024)])
    for size in sizes:
        (ASSETS / f"_tmp_{size}.png").unlink(missing_ok=True)

    for state in ("idle", "recording", "paused"):
        tray_icon(state, 64).save(str(ASSETS / f"tray-{state}.png"))
        tray_icon(state, 64, template=True).save(str(ASSETS / f"tray-{state}-template.png"))
    status_disc("#FF4D61").save(str(ASSETS / "tray-recording-disc.png"))
    status_disc("#F5A524").save(str(ASSETS / "tray-paused-disc.png"))
    print("Готово:", ", ".join(sorted(f.name for f in ASSETS.iterdir())))


if __name__ == "__main__":
    main()
