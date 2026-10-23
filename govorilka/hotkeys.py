"""Глобальная комбинация клавиш: запуск и остановка записи, когда окно свёрнуто.

Клавиши не перехватываются, а опрашиваются 20 раз в секунду (Windows: GetAsyncKeyState,
macOS: CGEventSource). Так не нужны хуки и права на перехват ввода, а комбинация из одних
модификаторов (Ctrl+Shift, Option+Command) работает так же, как с буквой.

Комбинация только из модификаторов срабатывает, когда её удержали HOLD_S секунд и
больше ничего не нажимали: обычные сочетания вроде Ctrl+Shift+Esc её не задевают.
Комбинация с клавишей (например, Ctrl+Shift+R) срабатывает сразу при нажатии клавиши.
"""

from __future__ import annotations

import ctypes
import logging
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

log = logging.getLogger(__name__)

IS_MAC = sys.platform == "darwin"
IS_WINDOWS = sys.platform == "win32"

MODIFIERS = ("ctrl", "alt", "shift", "cmd")  # порядок, в котором показываем и храним
ALIASES = {
    "control": "ctrl",
    "ctl": "ctrl",
    "option": "alt",
    "opt": "alt",
    "command": "cmd",
    "win": "cmd",
    "windows": "cmd",
    "super": "cmd",
    "meta": "cmd",
}
DEFAULT_HOTKEY = "alt+cmd" if IS_MAC else "ctrl+shift"
HOLD_S = 0.5  # сколько держать комбинацию из одних модификаторов
POLL_S = 0.05

_LETTERS = "abcdefghijklmnopqrstuvwxyz"
_DIGITS = "0123456789"
KEYS = (*_LETTERS, *_DIGITS, *(f"f{n}" for n in range(1, 25)), "space")


@dataclass(frozen=True)
class Chord:
    """Комбинация: набор модификаторов и, возможно, одна обычная клавиша."""

    mods: frozenset[str]
    key: str | None = None

    def __post_init__(self) -> None:
        if not self.mods:
            # без модификатора буква сработала бы при обычном наборе текста
            raise ValueError("В комбинации нужна хотя бы одна клавиша-модификатор")
        if not self.mods <= set(MODIFIERS):
            raise ValueError(f"Неизвестный модификатор: {sorted(self.mods - set(MODIFIERS))}")
        if self.key is not None and self.key not in KEYS:
            raise ValueError(f"Клавиша не поддерживается: {self.key}")


def parse_chord(text: str) -> Chord:
    """'ctrl+shift', 'Option + Command', 'ctrl+alt+r' -> Chord. ValueError, если не получилось."""
    mods: set[str] = set()
    key: str | None = None
    for raw in text.lower().replace(" ", "").split("+"):
        if not raw:
            continue
        name = ALIASES.get(raw, raw)
        if name in MODIFIERS:
            mods.add(name)
        elif name in KEYS and key is None:
            key = name
        else:
            raise ValueError(f"Не понял «{raw}» в комбинации «{text}»")
    return Chord(frozenset(mods), key)


def chord_to_string(chord: Chord) -> str:
    return "+".join([*(m for m in MODIFIERS if m in chord.mods), *([chord.key] if chord.key else [])])


def format_chord(chord: Chord, mac: bool = IS_MAC) -> str:
    names = (
        {"ctrl": "Control", "alt": "Option", "shift": "Shift", "cmd": "Command"}
        if mac
        else {"ctrl": "Ctrl", "alt": "Alt", "shift": "Shift", "cmd": "Win"}
    )
    parts = [names[m] for m in MODIFIERS if m in chord.mods]
    if chord.key:
        parts.append(chord.key.upper())
    return " + ".join(parts)


@dataclass(frozen=True)
class KeySnapshot:
    """Что зажато прямо сейчас: модификаторы и все остальные клавиши (неизвестные как 'vk123')."""

    mods: frozenset[str] = frozenset()
    keys: frozenset[str] = frozenset()


class Keyboard(Protocol):
    def snapshot(self) -> KeySnapshot: ...


# ---------- распознавание нажатий (без привязки к ОС, поэтому легко тестируется) ----------


class HotkeyDetector:
    def __init__(self, chord: Chord, hold_s: float = HOLD_S) -> None:
        self.chord = chord
        self.hold_s = hold_s
        self._started: float | None = None  # когда комбинация собралась целиком
        self._fired = False  # уже сработала, пока клавиши не отпущены
        self._dirty = False  # нажали что-то лишнее, ждём полного отпускания
        self._prev_keys: frozenset[str] = frozenset()

    def update(self, snap: KeySnapshot, now: float) -> bool:
        """True ровно в тот момент, когда комбинация сработала."""
        try:
            return self._update(snap, now)
        finally:
            self._prev_keys = snap.keys

    def _update(self, snap: KeySnapshot, now: float) -> bool:
        chord = self.chord
        if chord.key is not None:
            newly_pressed = chord.key in snap.keys and chord.key not in self._prev_keys
            return newly_pressed and snap.mods == chord.mods
        if not snap.mods and not snap.keys:  # всё отпущено: можно начинать заново
            self._started, self._fired, self._dirty = None, False, False
            return False
        if self._dirty:
            return False
        if snap.keys or not snap.mods <= chord.mods:  # нажали посторонние клавиши: это другая комбинация
            self._dirty, self._started = True, None
            return False
        if snap.mods != chord.mods:  # комбинацию ещё собирают или отпустили одну клавишу
            self._started = None
            return False
        if self._fired:
            return False
        if self._started is None:
            self._started = now
            return False
        if now - self._started >= self.hold_s:
            self._fired = True
            return True
        return False


class ChordRecorder:
    """Запоминает комбинацию, которую зажал пользователь, и отдаёт её, когда все клавиши отпущены."""

    def __init__(self) -> None:
        self._mods: set[str] = set()
        self._key: str | None = None
        self._active = False

    def feed(self, snap: KeySnapshot) -> Chord | None:
        if snap.mods or snap.keys:
            self._active = True
            self._mods |= snap.mods
            known = sorted(k for k in snap.keys if k in KEYS)
            if known and self._key is None:
                self._key = known[0]
            return None
        if not self._active:
            return None
        mods, key = frozenset(self._mods), self._key
        self._mods, self._key, self._active = set(), None, False
        return Chord(mods, key) if mods else None  # одна буква без модификатора не годится


# ---------- опрос клавиатуры ----------

_WIN_MODS = {"ctrl": (0x11,), "shift": (0x10,), "alt": (0x12,), "cmd": (0x5B, 0x5C)}
_WIN_IGNORED = {0x10, 0x11, 0x12, 0x5B, 0x5C, 0xA0, 0xA1, 0xA2, 0xA3, 0xA4, 0xA5, 0x14, 0x90, 0x91}
_WIN_NAMES = {
    **{ord(c.upper()): c for c in _LETTERS},
    **{ord(c): c for c in _DIGITS},
    **{0x6F + n: f"f{n}" for n in range(1, 25)},  # F1 = 0x70
    0x20: "space",
}


class WindowsKeyboard:
    def __init__(self) -> None:
        self._get = ctypes.windll.user32.GetAsyncKeyState  # type: ignore[attr-defined]
        self._get.restype = ctypes.c_short
        self._get.argtypes = [ctypes.c_int]

    def _down(self, vk: int) -> bool:
        return bool(self._get(vk) & 0x8000)

    def snapshot(self) -> KeySnapshot:
        mods = frozenset(name for name, vks in _WIN_MODS.items() if any(self._down(v) for v in vks))
        keys = frozenset(
            _WIN_NAMES.get(vk, f"vk{vk}") for vk in range(0x08, 0xFF) if vk not in _WIN_IGNORED and self._down(vk)
        )
        return KeySnapshot(mods, keys)


_MAC_FLAGS = {"shift": 0x20000, "ctrl": 0x40000, "alt": 0x80000, "cmd": 0x100000}
_MAC_MOD_CODES = {54, 55, 56, 57, 58, 59, 60, 61, 62, 63}  # модификаторы, caps lock, fn
_MAC_CODES = {
    **dict(zip("asdfhgzxcvbqweryt", (0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 11, 12, 13, 14, 15, 16, 17))),
    **dict(zip("123465", (18, 19, 20, 21, 22, 23))),
    **dict(zip("9780", (25, 26, 28, 29))),
    **dict(zip("ouiplkjnm", (31, 32, 34, 35, 37, 40, 38, 45, 46))),
    "space": 49,
    **{"f1": 122, "f2": 120, "f3": 99, "f4": 118, "f5": 96, "f6": 97, "f7": 98, "f8": 100, "f9": 101},
    **{"f10": 109, "f11": 103, "f12": 111, "f13": 105, "f14": 107, "f15": 113, "f16": 106, "f17": 64},
    **{"f18": 79, "f19": 80, "f20": 90},
}
_MAC_NAMES = {code: name for name, code in _MAC_CODES.items()}


_CORE_GRAPHICS = "/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics"
INPUT_MONITORING_URL = "x-apple.systempreferences:com.apple.preference.security?Privacy_ListenEvent"


def input_monitoring_status() -> bool | None:
    """macOS: разрешила ли система читать клавиатуру («Мониторинг ввода»). None — не Mac или проверить нельзя.

    Без этого разрешения macOS может отдавать «ничего не нажато», и горячая клавиша молча не работает.
    """
    if not IS_MAC:
        return None
    try:
        preflight = ctypes.CDLL(_CORE_GRAPHICS).CGPreflightListenEventAccess
    except (OSError, AttributeError):
        return None
    preflight.restype = ctypes.c_bool
    return bool(preflight())


def request_input_monitoring() -> None:
    """Показывает системный запрос и добавляет Говорилку в список «Мониторинг ввода»."""
    if not IS_MAC:
        return
    try:
        request = ctypes.CDLL(_CORE_GRAPHICS).CGRequestListenEventAccess
        request.restype = ctypes.c_bool
        request()
    except (OSError, AttributeError):
        log.warning("Не получилось запросить «Мониторинг ввода»")


def format_snapshot(snap: KeySnapshot, mac: bool = IS_MAC) -> str:
    """Что сейчас нажато, словами: для живой подсказки в настройках."""
    if not snap.mods and not snap.keys:
        return "ничего"
    names = (
        {"ctrl": "Control", "alt": "Option", "shift": "Shift", "cmd": "Command"}
        if mac
        else {"ctrl": "Ctrl", "alt": "Alt", "shift": "Shift", "cmd": "Win"}
    )
    parts = [names[m] for m in MODIFIERS if m in snap.mods]
    parts += sorted(k.upper() if k in KEYS else f"клавиша {k.removeprefix('vk')}" for k in snap.keys)
    return " + ".join(parts)


class MacKeyboard:
    """CoreGraphics напрямую через ctypes: для опроса состояния клавиш Swift-помощник не нужен."""

    def __init__(self) -> None:
        cg = ctypes.CDLL(_CORE_GRAPHICS)
        self._flags = cg.CGEventSourceFlagsState
        self._flags.restype = ctypes.c_uint64
        self._flags.argtypes = [ctypes.c_int32]
        self._key = cg.CGEventSourceKeyState
        self._key.restype = ctypes.c_bool
        self._key.argtypes = [ctypes.c_int32, ctypes.c_uint16]

    def snapshot(self) -> KeySnapshot:
        flags = self._flags(0)  # kCGEventSourceStateCombinedSessionState
        mods = frozenset(name for name, bit in _MAC_FLAGS.items() if flags & bit)
        keys = frozenset(
            _MAC_NAMES.get(code, f"vk{code}")
            for code in range(0, 128)
            if code not in _MAC_MOD_CODES and self._key(0, code)
        )
        return KeySnapshot(mods, keys)


# Windows: какое сочетание переключает язык ввода ("1" Alt+Shift — по умолчанию, "2" Ctrl+Shift, "3" нет)
_WINDOWS_SWITCH = {"1": frozenset({"alt", "shift"}), "2": frozenset({"ctrl", "shift"})}


def windows_language_switch() -> frozenset[str] | None:
    """Модификаторы, которыми в Windows переключают язык ввода. None — не Windows, переключение выключено или не узнать."""
    if not IS_WINDOWS:
        return None
    import winreg

    value = None
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Keyboard Layout\Toggle") as key:
            for name in ("Language Hotkey", "Hotkey"):
                try:
                    value = str(winreg.QueryValueEx(key, name)[0])
                    break
                except FileNotFoundError:
                    continue
    except OSError:
        pass
    return _WINDOWS_SWITCH.get(value or "1")


def conflicts_with_language_switch(chord: Chord, switch: frozenset[str] | None) -> bool:
    """Комбинация из одних модификаторов совпадает с переключением языка: при записи сменится и раскладка."""
    return chord.key is None and switch is not None and chord.mods == switch


def system_keyboard() -> Keyboard:
    if IS_WINDOWS:
        return WindowsKeyboard()
    if IS_MAC:
        return MacKeyboard()
    raise RuntimeError("Горячая клавиша поддерживается только на Windows и macOS")


# ---------- фоновый опрос ----------


class HotkeyWatcher:
    """Поток, который следит за клавишами и вызывает on_trigger, когда комбинация сработала."""

    def __init__(
        self,
        on_trigger: Callable[[], None],
        on_error: Callable[[str], None] = lambda _: None,
        keyboard: Keyboard | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        import time

        self._on_trigger = on_trigger
        self._on_error = on_error
        self._keyboard = keyboard
        self._clock = clock or time.monotonic
        self._detector: HotkeyDetector | None = None
        self._paused = threading.Event()  # пока в настройках записывают новую комбинацию, старая не должна срабатывать
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="hotkey-watcher", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join(timeout=2)

    def pause(self) -> None:
        self._paused.set()

    def resume(self) -> None:
        with self._lock:  # начинаем с чистого листа: клавиши, зажатые во время паузы, не считаются нажатием
            if self._detector is not None:
                self._detector = HotkeyDetector(self._detector.chord, self._detector.hold_s)
        self._paused.clear()

    def set_chord(self, chord: Chord | None) -> None:
        """None выключает горячую клавишу."""
        with self._lock:
            self._detector = HotkeyDetector(chord) if chord else None

    def _run(self) -> None:
        reported = False
        while not self._stop.wait(POLL_S):
            with self._lock:
                detector = self._detector
            if detector is None or self._paused.is_set():
                continue
            try:
                if self._keyboard is None:
                    self._keyboard = system_keyboard()
                fired = detector.update(self._keyboard.snapshot(), self._clock())
            except Exception as exc:  # noqa: BLE001 — опрос не должен умирать молча, но и не должен падать
                log.exception("Не получается читать состояние клавиш")
                if not reported:
                    reported = True
                    self._on_error(f"Горячая клавиша не работает: {exc}")
                self._stop.wait(2.0)
                continue
            if fired:
                try:
                    self._on_trigger()
                except Exception:
                    log.exception("Ошибка в обработчике горячей клавиши")
