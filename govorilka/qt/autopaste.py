"""Вставить разговор в чат нейросети, как только он откроется в браузере (Windows и macOS).

Веб-чаты не умеют принимать длинный текст снаружи. Поэтому Говорилка кладёт разговор в буфер обмена, открывает чат
и ждёт, пока впереди окажется окно браузера с этим чатом (по заголовку вкладки: «ChatGPT», «Gemini», «DeepSeek»…).
Когда страница загрузилась, Говорилка ставит курсор в поле ввода чата (на Windows находит его через специальные
возможности системы, UI Automation: не все чаты ставят курсор сами, DeepSeek например) и нажимает Ctrl+V (на Mac ⌘V).
Enter не нажимает никогда: отправляете вы сами, посмотрев текст.

Вход в аккаунт. Если вместо чата открылась страница входа («Вход — Google Аккаунты», «Log in — OpenAI»), ждём:
после входа браузер вернётся в чат, и текст вставится тогда. Если чат открылся без входа и разговор уже вставился,
а потом человек ушёл входить — после возвращения в чат вставляем ещё раз (прежняя вставка пропала вместе со страницей).

Осторожно, чтобы не вставить не туда: только когда впереди именно окно этого чата, и не позже пяти минут.

macOS разрешает нажимать клавиши за пользователя только программам с «Универсальным доступом»
(Настройки → Конфиденциальность и безопасность → Универсальный доступ). Без него вставки не будет, разговор
остаётся в буфере, а окно подскажет, где включить.
"""

from __future__ import annotations

import ctypes
import logging
import subprocess
import sys
import threading
import time
from collections.abc import Callable

log = logging.getLogger(__name__)

WAIT_S = 300.0  # пять минут: хватит, чтобы войти в аккаунт, даже с кодом из почты
SETTLE_S = 2.5  # страница чата успевает загрузиться и поставить курсор в поле ввода
POLL_S = 1.0 if sys.platform == "darwin" else 0.4  # на Mac каждый опрос — запуск osascript, не чаще раза в секунду
VK_CONTROL, VK_V, KEYUP = 0x11, 0x56, 0x0002

# как узнать вкладку чата по заголовку окна браузера
TITLES: dict[str, tuple[str, ...]] = {
    "chatgpt": ("chatgpt",),
    "claude": ("claude",),
    "gemini": ("gemini",),
    "deepseek": ("deepseek",),
    "gigachat": ("gigachat", "гигачат"),
    "alice": ("алиса", "alice"),
}
# страница входа: после неё браузер вернётся в чат — тогда и вставим
LOGIN_MARKERS = ("вход", "войти", "log in", "login", "sign in", "sign up", "регистрац", "аккаунт", "account", "auth")


class NoPermissionError(RuntimeError):
    """macOS не дал нажать клавиши: нет «Универсального доступа»."""


def supported() -> bool:
    return sys.platform in ("win32", "darwin")


def matches(title: str, target: str) -> bool:
    lowered = title.lower()
    return any(marker in lowered for marker in TITLES.get(target, ()))


def is_login(title: str) -> bool:
    lowered = title.lower()
    return any(marker in lowered for marker in LOGIN_MARKERS)


# ---------- Windows ----------


def _windows_title() -> str:
    user32 = ctypes.windll.user32
    hwnd = user32.GetForegroundWindow()
    length = user32.GetWindowTextLengthW(hwnd)
    buffer = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buffer, length + 1)
    return buffer.value


# поле ввода чата: «Message DeepSeek», «Спросите ChatGPT», «Enter a prompt here»… Адресную строку браузера не трогаем
_INPUT_HINTS = ("message", "сообщ", "спрос", "ask", "prompt", "запрос", "введите", "напишите", "chat", "чат")
_BROWSER_UI = ("адресн", "address", "omnibox", "search bar", "строка поиска")
UIA_EDIT, UIA_CONTROL_TYPE, UIA_DESCENDANTS = 50004, 30003, 4
WIDE_INPUT = 500  # поле ввода чата широкое; поля логина и поиска уже


def _windows_focus_input(attempts: int = 8) -> bool:
    """Ставит курсор в поле ввода чата во окне впереди. True — нашли и поставили.
    Браузер отдаёт содержимое страницы специальным возможностям не сразу, а после первого запроса — поэтому повторяем."""
    import comtypes
    import comtypes.client

    comtypes.CoInitialize()
    try:
        comtypes.client.GetModule("UIAutomationCore.dll")
        from comtypes.gen.UIAutomationClient import CUIAutomation, IUIAutomation

        automation = comtypes.client.CreateObject(CUIAutomation, interface=IUIAutomation)
        root = automation.ElementFromHandle(ctypes.windll.user32.GetForegroundWindow())
        condition = automation.CreatePropertyCondition(UIA_CONTROL_TYPE, UIA_EDIT)
        for _ in range(attempts):
            best, best_score = None, -1
            found = root.FindAll(UIA_DESCENDANTS, condition)
            for i in range(found.Length):
                element = found.GetElement(i)
                name = (element.CurrentName or "").lower()
                if any(marker in name for marker in _BROWSER_UI) or not element.CurrentIsKeyboardFocusable:
                    continue
                box = element.CurrentBoundingRectangle
                width = box.right - box.left
                hinted = any(h in name for h in _INPUT_HINTS)
                if not hinted and width < WIDE_INPUT:
                    continue  # узкое поле без подсказки — скорее email на странице входа, туда разговор не вставляем
                score = width + (10_000 if hinted else 0)
                if score > best_score:
                    best, best_score = element, score
            if best is not None:
                best.SetFocus()
                return True
            time.sleep(0.4)
        return False
    finally:
        comtypes.CoUninitialize()


class NoInputError(RuntimeError):
    """Поле ввода чата не нашлось (страница ошибки, вход в аккаунт): вставлять некуда."""


def _windows_paste() -> None:
    try:
        found = _windows_focus_input()
    except Exception as exc:  # noqa: BLE001 — поиск поля не удался: вставляем туда, где курсор (чаты обычно ставят его сами)
        log.warning("Не искал поле ввода через UI Automation: %s", exc)
        found = True
    if not found:
        raise NoInputError("поле ввода чата не нашлось")
    user32 = ctypes.windll.user32
    user32.keybd_event(VK_CONTROL, 0, 0, 0)
    user32.keybd_event(VK_V, 0, 0, 0)
    user32.keybd_event(VK_V, 0, KEYUP, 0)
    user32.keybd_event(VK_CONTROL, 0, KEYUP, 0)


# ---------- macOS ----------

_MAC_TITLE = (
    'tell application "System Events" to tell (first application process whose frontmost is true) '
    'to if (count of windows) > 0 then get name of front window'
)
_MAC_PASTE = 'tell application "System Events" to keystroke "v" using command down'


def _osascript(script: str) -> str:
    result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=5)
    if result.returncode != 0:
        # -1719 / -25211 / «not allowed assistive access»: нет «Универсального доступа»;
        # -1743 «Not authorized to send Apple events»: нет «Автоматизации» (управление System Events)
        if any(mark in result.stderr for mark in ("assistive", "-1719", "-25211", "-1743", "not authorized", "Not authorized")):
            raise NoPermissionError(result.stderr.strip())
        raise OSError(result.stderr.strip())
    return result.stdout.strip()


def _mac_title() -> str:
    return _osascript(_MAC_TITLE)


def _mac_paste() -> None:
    _osascript(_MAC_PASTE)


# ---------- ожидание ----------


def paste_when_ready(
    target: str,
    title: Callable[[], str] | None = None,  # подменяются в тестах
    paste: Callable[[], None] | None = None,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    """Ждёт окно чата впереди и вставляет. Возвращает, сколько раз вставил (0 — не дождались).
    Блокирует: зовите из фонового потока. NoPermissionError — на Mac нет «Универсального доступа»."""
    mac = sys.platform == "darwin"
    title = title or (_mac_title if mac else _windows_title)
    paste = paste or (_mac_paste if mac else _windows_paste)
    deadline = clock() + WAIT_S
    seen_at: float | None = None
    pasted = 0
    login_after_paste = False
    while clock() < deadline:
        try:
            current = title()
        except OSError as exc:  # окно сменилось на ходу — просто спросим ещё раз
            log.debug("Не прочитал заголовок окна: %s", exc)
            current = ""
        if matches(current, target) and not is_login(current):
            seen_at = seen_at if seen_at is not None else clock()
            ready = clock() - seen_at >= SETTLE_S
            if ready and (pasted == 0 or login_after_paste):
                try:
                    paste()
                except NoInputError:  # страница ещё не та (ошибка, вход) — подождём, вдруг человек войдёт
                    seen_at = clock() + 5.0  # не долбим каждые полсекунды
                    sleep(POLL_S)
                    continue
                pasted += 1
                login_after_paste = False
                log.info("Вставил разговор в %s (%d-й раз)", target, pasted)
                if pasted >= 2:
                    return pasted  # после входа вставили — больше не трогаем
        else:
            seen_at = None  # другое окно или страница входа — в чужое окно не вставляем
            if pasted and is_login(current):
                login_after_paste = True  # ушли входить: вставленное пропадёт, после входа вставим снова
        sleep(POLL_S)
    if not pasted:
        log.info("Окно %s так и не оказалось впереди — разговор остался в буфере", target)
    return pasted


def start(target: str, on_no_permission: Callable[[], None] = lambda: None) -> None:
    """Запускает ожидание в фоне. on_no_permission зовётся из фонового потока (на Mac без «Универсального доступа»)."""
    if not supported() or target not in TITLES:
        return

    def run() -> None:
        try:
            paste_when_ready(target)
        except NoPermissionError:
            log.info("macOS не дал вставить: нет «Универсального доступа»")
            on_no_permission()
        except Exception:  # noqa: BLE001 — вставка лишь удобство: её сбой не должен ничего ронять
            log.exception("Автовставка не сработала")

    threading.Thread(target=run, name="autopaste", daemon=True).start()
