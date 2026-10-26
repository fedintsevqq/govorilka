"""Специальные возможности macOS (Accessibility API) для автовставки разговора в чат нейросети.

Раньше вставка на Mac шла через AppleScript и System Events: это требовало два разрешения («Универсальный доступ»
и «Автоматизация»), а курсор в поле ввода не ставился вовсе. В чатах, которые не ставят курсор сами (DeepSeek),
⌘V уходил в пустоту. Теперь всё через Accessibility API, как на Windows через UI Automation:

1. узнать заголовок окна впереди (AXFocusedWindow → AXTitle) — по нему понимаем, что открыт нужный чат;
2. найти в окне поле ввода чата (AXTextArea, самое широкое; адресную строку браузера пропускаем) и поставить в
   него курсор (AXFocused = true). Chrome и приложения на Electron отдают содержимое страницы специальным
   возможностям только после флага AXManualAccessibility — ставим его;
3. нажать ⌘V событием клавиатуры (CGEventPost).

Нужно одно разрешение — «Универсальный доступ». Всё через ctypes, без pyobjc. Любая ошибка — исключение,
которое ловит autopaste: вставка лишь удобство, она не должна ронять приложение.
"""

from __future__ import annotations

import ctypes
import time
import ctypes.util
from collections.abc import Callable, Iterable

AS = "/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices"
CF = "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation"
CG = "/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics"
UTF8 = 0x08000100  # kCFStringEncodingUTF8
AX_VALUE_CGSIZE = 2  # kAXValueCGSizeType
KEY_V = 9  # kVK_ANSI_V
COMMAND = 1 << 20  # kCGEventFlagMaskCommand
HID_TAP = 0  # kCGHIDEventTap
MAX_NODES = 4000  # страница чата — сотни элементов; предел страхует от зависания на огромной странице
MAX_DEPTH = 60
INPUT_ROLES = ("AXTextArea", "AXTextField")
WIDE_INPUT = 300.0  # поле ввода чата широкое; поиск по сайту и поля логина обычно уже
BROWSER_UI = ("address", "адрес", "search bar", "строка поиска", "omnibox", "url")


class NotTrustedError(RuntimeError):
    """Нет разрешения «Универсальный доступ»."""


def pick_input(nodes: Iterable[tuple[object, str, float, str]]) -> object | None:
    """Из (элемент, роль, ширина, подпись) выбирает поле ввода чата: многострочное поле важнее однострочного,
    шире — лучше; адресную строку браузера и узкие поля пропускаем."""
    best, best_score = None, -1.0
    for element, role, width, label in nodes:
        if role not in INPUT_ROLES or width < WIDE_INPUT:
            continue
        lowered = label.lower()
        if any(marker in lowered for marker in BROWSER_UI):
            continue
        score = width + (100_000 if role == "AXTextArea" else 0)
        if score > best_score:
            best, best_score = element, score
    return best


class _Size(ctypes.Structure):
    _fields_ = [("width", ctypes.c_double), ("height", ctypes.c_double)]


class _Api:
    """Функции macOS с правильными типами (на arm64 и x86_64 неверный тип аргумента — падение процесса)."""

    def __init__(self) -> None:
        ax, cf, cg = ctypes.CDLL(AS), ctypes.CDLL(CF), ctypes.CDLL(CG)
        vp = ctypes.c_void_p
        self.trusted = ax.AXIsProcessTrusted
        self.trusted.restype = ctypes.c_bool
        self.create_app = ax.AXUIElementCreateApplication
        self.create_app.restype, self.create_app.argtypes = vp, [ctypes.c_int32]
        self.copy_attr = ax.AXUIElementCopyAttributeValue
        self.copy_attr.restype, self.copy_attr.argtypes = ctypes.c_int32, [vp, vp, ctypes.POINTER(vp)]
        self.set_attr = ax.AXUIElementSetAttributeValue
        self.set_attr.restype, self.set_attr.argtypes = ctypes.c_int32, [vp, vp, vp]
        self.value_get = ax.AXValueGetValue
        self.value_get.restype, self.value_get.argtypes = ctypes.c_bool, [vp, ctypes.c_int32, vp]
        self.str_create = cf.CFStringCreateWithCString
        self.str_create.restype, self.str_create.argtypes = vp, [vp, ctypes.c_char_p, ctypes.c_uint32]
        self.str_get = cf.CFStringGetCString
        self.str_get.restype, self.str_get.argtypes = ctypes.c_bool, [vp, ctypes.c_char_p, ctypes.c_long, ctypes.c_uint32]
        self.type_id = cf.CFGetTypeID
        self.type_id.restype, self.type_id.argtypes = ctypes.c_ulong, [vp]
        cf.CFStringGetTypeID.restype = ctypes.c_ulong
        cf.CFArrayGetTypeID.restype = ctypes.c_ulong
        self.string_type, self.array_type = cf.CFStringGetTypeID(), cf.CFArrayGetTypeID()
        self.count = cf.CFArrayGetCount
        self.count.restype, self.count.argtypes = ctypes.c_long, [vp]
        self.at = cf.CFArrayGetValueAtIndex
        self.at.restype, self.at.argtypes = vp, [vp, ctypes.c_long]
        self.release = cf.CFRelease
        self.release.restype, self.release.argtypes = None, [vp]
        self.true = ctypes.c_void_p.in_dll(cf, "kCFBooleanTrue").value
        self.key_event = cg.CGEventCreateKeyboardEvent
        self.key_event.restype, self.key_event.argtypes = vp, [vp, ctypes.c_uint16, ctypes.c_bool]
        self.set_flags = cg.CGEventSetFlags
        self.set_flags.restype, self.set_flags.argtypes = None, [vp, ctypes.c_uint64]
        self.post = cg.CGEventPost
        self.post.restype, self.post.argtypes = None, [ctypes.c_uint32, vp]
        self.set_timeout = ax.AXUIElementSetMessagingTimeout
        self.set_timeout.restype, self.set_timeout.argtypes = ctypes.c_int32, [vp, ctypes.c_float]
        self._names: dict[str, int] = {}

    def name(self, text: str) -> int:
        """CFString для имени атрибута (живут всё время работы: их немного)."""
        if text not in self._names:
            self._names[text] = self.str_create(None, text.encode(), UTF8)
        return self._names[text]

    def attr(self, element: int, name: str) -> int | None:
        """Значение атрибута (владеет вызывающий: надо release). None — нет такого атрибута."""
        out = ctypes.c_void_p()
        if self.copy_attr(element, self.name(name), ctypes.byref(out)) != 0 or not out.value:
            return None
        return out.value

    def text(self, value: int | None) -> str:
        if not value or self.type_id(value) != self.string_type:
            return ""
        buffer = ctypes.create_string_buffer(1024)
        return buffer.value.decode("utf-8", "replace") if self.str_get(value, buffer, 1024, UTF8) else ""

    def string_attr(self, element: int, name: str) -> str:
        value = self.attr(element, name)
        try:
            return self.text(value)
        finally:
            if value:
                self.release(value)

    def width(self, element: int) -> float:
        value = self.attr(element, "AXSize")
        if not value:
            return 0.0
        try:
            size = _Size()
            return size.width if self.value_get(value, AX_VALUE_CGSIZE, ctypes.byref(size)) else 0.0
        finally:
            self.release(value)


_api: _Api | None = None


def _get_api() -> _Api:
    global _api
    if _api is None:
        _api = _Api()
    return _api


def _frontmost_pid() -> int:
    """Процесс приложения впереди: NSWorkspace.sharedWorkspace.frontmostApplication.processIdentifier."""
    from govorilka import objc_bridge

    ctypes.cdll.LoadLibrary("/System/Library/Frameworks/AppKit.framework/AppKit")
    objc = objc_bridge.runtime()
    workspace = objc_bridge.send(objc, objc.objc_getClass(b"NSWorkspace"), "sharedWorkspace")
    app = objc_bridge.send(objc, workspace, "frontmostApplication")
    if not app:
        return 0
    return int(objc_bridge.send(objc, app, "processIdentifier", ctypes.c_int32))


AX_TIMEOUT_S = 0.5  # зависший браузер не держит поиск поля по 6 секунд на каждый вопрос
SEARCH_BUDGET_S = 3.0  # весь поиск поля ввода: дольше — окно впереди уже могло смениться


def _with_front_window(action: Callable[[_Api, int, int], object], build_tree: bool = False) -> object:
    api = _get_api()
    if not api.trusted():
        raise NotTrustedError("нет «Универсального доступа»")
    pid = _frontmost_pid()
    if not pid:
        return None
    app = api.create_app(pid)
    try:
        api.set_timeout(app, AX_TIMEOUT_S)
        if build_tree:
            # Chrome и Electron строят дерево специальных возможностей страницы только по этой просьбе. Просим лишь
            # при поиске поля ввода в окне чата: у других программ (Slack, VS Code) это нагружало бы процессор.
            # AXEnhancedUserInterface не трогаем: он ломает анимацию окон и программы вроде Rectangle.
            api.set_attr(app, api.name("AXManualAccessibility"), api.true)
        window = api.attr(app, "AXFocusedWindow")
        if not window:
            return None
        try:
            return action(api, app, window)
        finally:
            api.release(window)
    finally:
        api.release(app)


def front_window_title() -> str:
    """Заголовок окна, которое сейчас впереди (у браузера — заголовок вкладки)."""
    result = _with_front_window(lambda api, _app, window: api.string_attr(window, "AXTitle"))
    return result if isinstance(result, str) else ""


def focus_chat_input() -> bool:
    """Ставит курсор в поле ввода чата в окне впереди. True — нашли и поставили."""

    def find(api: _Api, _app: int, window: int) -> bool:
        arrays: list[int] = []  # дочерние элементы живут, пока живы их массивы: освобождаем в конце
        try:
            nodes: list[tuple[object, str, float, str]] = []
            stack, seen = [(window, 0)], 0
            deadline = time.monotonic() + SEARCH_BUDGET_S
            while stack and seen < MAX_NODES and time.monotonic() < deadline:
                element, depth = stack.pop()
                seen += 1
                role = api.string_attr(element, "AXRole")
                if role in INPUT_ROLES:
                    label = " ".join(api.string_attr(element, n) for n in ("AXDescription", "AXTitle", "AXPlaceholderValue"))
                    nodes.append((element, role, api.width(element), label))
                    continue
                if depth >= MAX_DEPTH:
                    continue
                children = api.attr(element, "AXChildren")
                if not children:
                    continue
                if api.type_id(children) != api.array_type:
                    api.release(children)
                    continue
                arrays.append(children)
                stack.extend((api.at(children, i), depth + 1) for i in range(api.count(children)))
            target = pick_input(nodes)
            if target is None:
                return False
            return api.set_attr(target, api.name("AXFocused"), api.true) == 0
        finally:
            for array in arrays:
                api.release(array)

    return bool(_with_front_window(find, build_tree=True))


def press_paste() -> None:
    """⌘V событием клавиатуры."""
    api = _get_api()
    if not api.trusted():
        raise NotTrustedError("нет «Универсального доступа»")
    for down in (True, False):
        event = api.key_event(None, KEY_V, down)
        if not event:
            raise OSError("macOS не создала событие клавиатуры")
        try:
            api.set_flags(event, COMMAND)
            api.post(HID_TAP, event)
        finally:
            api.release(event)
