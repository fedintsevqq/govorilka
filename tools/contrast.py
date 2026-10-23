"""Проверка контраста цветов интерфейса по WCAG 2.2: python tools/contrast.py

Текст должен давать не меньше 4.5:1 (крупный жирный — 3:1), значки и границы элементов — не меньше 3:1.
Цвета берутся из govorilka/qml/Theme.qml, поэтому тема и проверка не расходятся. Код выхода 1, если что-то не проходит.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

THEME = Path(__file__).resolve().parent.parent / "govorilka" / "qml" / "Theme.qml"


def parse_hex(value: str) -> tuple[float, float, float]:
    value = value.lstrip("#")
    return tuple(int(value[i : i + 2], 16) / 255 for i in (0, 2, 4))  # type: ignore[return-value]


def luminance(rgb: tuple[float, float, float]) -> float:
    def channel(c: float) -> float:
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (channel(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def ratio(a: tuple[float, float, float], b: tuple[float, float, float]) -> float:
    la, lb = sorted((luminance(a), luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def load_palette(path: Path = THEME) -> dict[str, dict[str, str]]:
    """Достаёт из Theme.qml пары «тёмный / светлый» для свойств вида `dark ? "#111" : "#eee"`."""
    text = path.read_text(encoding="utf-8")
    palette: dict[str, dict[str, str]] = {"dark": {}, "light": {}}
    for name, dark, light in re.findall(r'readonly property color (\w+):\s*dark\s*\?\s*"(#[0-9A-Fa-f]{6})"\s*:\s*"(#[0-9A-Fa-f]{6})"', text):
        palette["dark"][name], palette["light"][name] = dark, light
    for name, value in re.findall(r'readonly property color (\w+):\s*"(#[0-9A-Fa-f]{6})"', text):
        palette["dark"].setdefault(name, value)
        palette["light"].setdefault(name, value)
    others = re.search(r"readonly property var others: dark\s*\?\s*\[(.*?)\]\s*:\s*\[(.*?)\]", text, re.S)
    if others:
        for mode, group in zip(("dark", "light"), others.groups()):
            for i, color in enumerate(re.findall(r"#[0-9A-Fa-f]{6}", group)):
                palette[mode][f"others{i}"] = color
    return palette


# (название, цвет переднего плана, фон, минимум)
TEXT, ICON = 4.5, 3.0
CHECKS = [
    ("основной текст на фоне", "text", "bg", TEXT),
    ("основной текст на карточке", "text", "core", TEXT),
    ("вторичный текст на фоне", "textSecondary", "bg", TEXT),
    ("вторичный текст на карточке", "textSecondary", "core", TEXT),
    ("вторичный текст на приподнятой карточке", "textSecondary", "coreRaised", TEXT),
    ("третичный текст (время, подсказки) на фоне", "textTertiary", "bg", TEXT),
    ("третичный текст на карточке", "textTertiary", "core", TEXT),
    ("третичный текст на приподнятой карточке", "textTertiary", "coreRaised", TEXT),
    ("«Я» на карточке", "me", "core", TEXT),
    ("запись (красный) на карточке", "record", "core", TEXT),
    ("пауза (янтарный) на карточке", "pause", "core", TEXT),
    ("отметка ★ на карточке", "mark", "core", TEXT),
    ("успех на карточке", "success", "core", TEXT),
    ("акцент A на карточке", "accentA", "core", ICON),
    ("акцент B на карточке", "accentB", "core", ICON),
    ("текст на акцентной кнопке, левый край", "fgAccent", "accentA", TEXT),
    ("текст на акцентной кнопке, правый край", "fgAccent", "accentB", TEXT),
    ("значок на красном диске записи", "fgRecord", "record", ICON),
    ("значок на янтарном диске паузы", "fgPause", "pause", ICON),
    ("выключенный переключатель на карточке", "switchOff", "core", ICON),
] + [(f"собеседник {i + 1} на карточке", f"others{i}", "core", TEXT) for i in range(8)]


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    palette = load_palette()
    failed = 0
    for mode in ("dark", "light"):
        colors = palette[mode]
        print(f"\n=== {mode} ===")
        for title, fg, bg, minimum in CHECKS:
            if fg not in colors or bg not in colors:
                print(f"  ? {title}: нет цвета {fg if fg not in colors else bg} в Theme.qml")
                failed += 1
                continue
            value = ratio(parse_hex(colors[fg]), parse_hex(colors[bg]))
            ok = value >= minimum
            failed += not ok
            print(f"  {'ok ' if ok else 'НЕТ'} {value:5.2f}  (нужно {minimum})  {title}   {colors[fg]} на {colors[bg]}")
    print(f"\nне проходят: {failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
