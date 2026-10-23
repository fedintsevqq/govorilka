import gc
import socket
import sys
import time
import tkinter as tk

import pytest

from govorilka import autostart, single_instance
from govorilka.hotkeys import (
    Chord,
    ChordRecorder,
    HotkeyDetector,
    HotkeyWatcher,
    KeySnapshot,
    chord_to_string,
    format_chord,
    parse_chord,
)


def snap(mods="", keys=""):
    return KeySnapshot(frozenset(m for m in mods.split("+") if m), frozenset(k for k in keys.split("+") if k))


# ---------- разбор комбинации ----------


def test_parse_accepts_mac_and_windows_names():
    assert parse_chord("Option + Command") == parse_chord("alt+cmd") == parse_chord("opt+win")
    assert parse_chord("ctrl+shift+R") == Chord(frozenset({"ctrl", "shift"}), "r")
    assert chord_to_string(parse_chord("cmd+shift+alt+ctrl+f5")) == "ctrl+alt+shift+cmd+f5"


@pytest.mark.parametrize("bad", ["", "r", "f5", "ctrl+banana", "ctrl+a+b", "+"])
def test_parse_rejects_nonsense(bad):
    with pytest.raises(ValueError):
        parse_chord(bad)


def test_chord_is_shown_in_platform_words():
    chord = parse_chord("alt+cmd")
    assert format_chord(chord, mac=True) == "Option + Command"
    assert format_chord(chord, mac=False) == "Alt + Win"
    assert format_chord(parse_chord("ctrl+shift+r"), mac=False) == "Ctrl + Shift + R"


# ---------- срабатывание ----------


def run(detector, timeline):
    """timeline: [(время, снимок)] -> моменты срабатывания."""
    return [t for t, s in timeline if detector.update(s, t)]


def test_modifier_only_chord_needs_to_be_held():
    d = HotkeyDetector(parse_chord("ctrl+shift"), hold_s=0.5)
    quick_tap = [(0.0, snap("ctrl+shift")), (0.2, snap("ctrl+shift")), (0.25, snap())]
    assert run(d, quick_tap) == []
    held = [(1.0, snap("ctrl")), (1.05, snap("ctrl+shift")), (1.3, snap("ctrl+shift")), (1.6, snap("ctrl+shift")), (1.7, snap())]
    assert run(d, held) == [1.6]  # ровно один раз, пока держат


def test_fires_once_until_everything_is_released():
    d = HotkeyDetector(parse_chord("ctrl+shift"), hold_s=0.2)
    timeline = [(0.0, snap("ctrl+shift")), (0.3, snap("ctrl+shift")), (0.6, snap("ctrl+shift")), (0.7, snap("ctrl"))]
    timeline += [(0.8, snap("ctrl+shift")), (1.2, snap("ctrl+shift")), (1.3, snap()), (1.4, snap("ctrl+shift")), (1.7, snap("ctrl+shift"))]
    assert run(d, timeline) == [0.3, 1.7]


def test_other_shortcuts_with_the_same_modifiers_do_not_fire():
    d = HotkeyDetector(parse_chord("ctrl+shift"), hold_s=0.2)
    ctrl_shift_esc = [(0.0, snap("ctrl+shift")), (0.1, snap("ctrl+shift", "vk27")), (0.5, snap("ctrl+shift", "vk27")), (0.9, snap("ctrl+shift"))]
    assert run(d, ctrl_shift_esc) == []  # после Esc ждём полного отпускания
    ctrl_shift_alt = [(2.0, snap("ctrl+shift+alt")), (2.6, snap("ctrl+shift+alt")), (3.0, snap())]
    assert run(d, ctrl_shift_alt) == []


def test_chord_with_key_fires_on_press_and_only_with_exact_modifiers():
    d = HotkeyDetector(parse_chord("ctrl+shift+r"))
    assert run(d, [(0.0, snap("ctrl+shift")), (0.1, snap("ctrl+shift", "r")), (0.2, snap("ctrl+shift", "r"))]) == [0.1]
    assert run(d, [(1.0, snap()), (1.1, snap("ctrl", "r"))]) == []
    assert run(d, [(2.0, snap()), (2.1, snap("ctrl+shift+alt", "r"))]) == []
    assert run(d, [(3.0, snap()), (3.1, snap("ctrl+shift", "r"))]) == [3.1]  # снова после отпускания


# ---------- запись комбинации в настройках ----------


def test_recorder_returns_chord_after_release():
    r = ChordRecorder()
    assert r.feed(snap()) is None
    assert r.feed(snap("alt")) is None
    assert r.feed(snap("alt+cmd")) is None
    assert r.feed(snap("cmd")) is None  # одну клавишу отпустили, остальные ещё держат
    assert r.feed(snap()) == parse_chord("alt+cmd")


def test_recorder_keeps_the_letter_and_ignores_lone_letters():
    r = ChordRecorder()
    r.feed(snap("ctrl+shift"))
    r.feed(snap("ctrl+shift", "r"))
    assert r.feed(snap()) == parse_chord("ctrl+shift+r")
    r.feed(snap("", "r"))
    assert r.feed(snap()) is None  # без модификатора не годится


# ---------- фоновый опрос ----------


class ScriptedKeyboard:
    def __init__(self, snapshots):
        self._snapshots = list(snapshots)
        self.calls = 0

    def snapshot(self):
        self.calls += 1
        return self._snapshots.pop(0) if len(self._snapshots) > 1 else self._snapshots[0]


def wait_for(condition, timeout=2.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if condition():
            return True
        time.sleep(0.01)
    return False


def test_watcher_triggers_callback_and_can_be_disabled():
    fired = []
    clock = iter(x * 0.2 for x in range(1000))
    watcher = HotkeyWatcher(lambda: fired.append(1), keyboard=ScriptedKeyboard([snap("ctrl+shift")]), clock=lambda: next(clock))
    watcher.set_chord(parse_chord("ctrl+shift"))
    watcher.start()
    assert wait_for(lambda: fired)
    watcher.set_chord(None)
    seen = len(fired)
    time.sleep(0.2)
    watcher.stop()
    assert len(fired) == seen == 1


def test_watcher_reports_broken_keyboard_once():
    class Broken:
        def snapshot(self):
            raise OSError("нет доступа")

    errors = []
    watcher = HotkeyWatcher(lambda: None, errors.append, keyboard=Broken())
    watcher.set_chord(parse_chord("ctrl+shift"))
    watcher.start()
    assert wait_for(lambda: errors)
    time.sleep(0.3)
    watcher.stop()
    assert len(errors) == 1 and "нет доступа" in errors[0]


@pytest.mark.skipif(sys.platform != "win32", reason="читает настоящее состояние клавиш Windows")
def test_windows_sees_injected_keys_and_hotkey_fires():
    """Нажимаем Ctrl+Shift+F24 (на F24 нет ничьих действий) и смотрим, что опрос это видит."""
    import ctypes

    from govorilka.hotkeys import WindowsKeyboard

    user32 = ctypes.windll.user32
    keyboard = WindowsKeyboard()
    if keyboard.snapshot() != KeySnapshot():
        pytest.skip("на клавиатуре прямо сейчас кто-то печатает — тест читает настоящие клавиши")
    pressed = []
    try:
        for vk in (0x11, 0x10, 0x87):  # Ctrl, Shift, F24
            user32.keybd_event(vk, 0, 0, 0)
            pressed.append(vk)
        time.sleep(0.15)
        seen = keyboard.snapshot()
        detector = HotkeyDetector(parse_chord("ctrl+shift+f24"))
        fired = detector.update(seen, 0.0)
    finally:
        for vk in reversed(pressed):
            user32.keybd_event(vk, 0, 2, 0)  # KEYEVENTF_KEYUP
    time.sleep(0.15)
    if seen == KeySnapshot():
        pytest.skip("в этой сессии Windows программные нажатия клавиш не видны (например, служба без рабочего стола)")
    assert {"ctrl", "shift"} <= seen.mods and "f24" in seen.keys
    assert fired or seen.mods != {"ctrl", "shift"}  # если человек нажал что-то своё, комбинация законно не сработала
    assert "f24" not in keyboard.snapshot().keys


# ---------- автозапуск ----------


def test_launch_agent_plist_runs_at_login_in_background(tmp_path):
    import plistlib

    data = plistlib.loads(autostart.launch_agent_plist(["/Applications/Govorilka.app/Contents/MacOS/Govorilka", "--background"]))
    assert data["RunAtLoad"] is True
    assert data["ProgramArguments"][-1] == "--background"
    assert data["Label"] == autostart.LAUNCH_AGENT_ID


def test_mac_autostart_writes_and_removes_agent(tmp_path):
    path = tmp_path / "Library" / "LaunchAgents" / "x.plist"
    autostart._set_mac(True, ["/app", "--background"], path)
    assert path.exists()
    autostart._set_mac(False, ["/app", "--background"], path)
    autostart._set_mac(False, ["/app", "--background"], path)  # повторно — не ошибка
    assert not path.exists()


@pytest.mark.skipif(sys.platform != "win32", reason="реестр Windows")
def test_windows_autostart_roundtrip_in_private_key():
    import winreg

    key = r"Software\GovorilkaTest\Run"
    try:
        assert autostart.is_enabled(run_key=key) is False
        autostart.set_enabled(True, [r"C:\Program Files\Govorilka\Govorilka.exe", "--background"], run_key=key)
        assert autostart.is_enabled(run_key=key) is True
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as handle:
            value, _ = winreg.QueryValueEx(handle, autostart.APP_NAME)
        assert value == '"C:\\Program Files\\Govorilka\\Govorilka.exe" --background'
        autostart.set_enabled(False, run_key=key)
        assert autostart.is_enabled(run_key=key) is False
    finally:
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key)
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, r"Software\GovorilkaTest")
        except OSError:
            pass


def test_launch_command_for_source_runs_the_module():
    command = autostart.launch_command()
    assert command[-1] == "--background" and "govorilka" in command


# ---------- один экземпляр ----------


def test_second_instance_is_refused_and_asks_first_to_show(tmp_path):
    shown = []
    first = single_instance.acquire(lambda: shown.append(1), folder=tmp_path)
    assert first is not None
    assert single_instance.acquire(lambda: shown.append(2), folder=tmp_path) is None
    assert wait_for(lambda: shown == [1])
    first.close()
    third = single_instance.acquire(lambda: None, folder=tmp_path)  # после выхода первой копии место свободно
    assert third is not None
    third.close()


def test_background_second_instance_does_not_raise_the_window(tmp_path):
    shown = []
    first = single_instance.acquire(lambda: shown.append(1), folder=tmp_path)
    assert single_instance.acquire(lambda: None, folder=tmp_path, notify_existing=False) is None
    time.sleep(0.2)
    assert shown == []
    first.close()


def test_foreign_program_on_the_old_port_does_not_block_startup(tmp_path):
    with socket.socket() as squatter:  # кто-то занял порт, который раньше был фиксированным
        squatter.bind(("127.0.0.1", 47653)) if _port_free(47653) else None
        lock = single_instance.acquire(lambda: None, folder=tmp_path)
        assert lock is not None
        lock.close()


def _port_free(port):
    with socket.socket() as s:
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def test_copy_in_another_process_holds_the_lock(tmp_path):
    import subprocess
    import textwrap

    holder = subprocess.Popen(
        [
            sys.executable,
            "-c",
            textwrap.dedent(
                f"""
                import sys, time
                from pathlib import Path
                from govorilka import single_instance
                lock = single_instance.acquire(lambda: None, folder=Path(r"{tmp_path}"))
                print("locked" if lock else "busy", flush=True)
                time.sleep(30)
                """
            ),
        ],
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert holder.stdout.readline().strip() == "locked"
        assert single_instance.acquire(lambda: None, folder=tmp_path, notify_existing=False) is None
    finally:
        holder.kill()
        holder.wait()
    # программа «упала» — замок снят системой, можно запускаться
    lock = single_instance.acquire(lambda: None, folder=tmp_path)
    assert lock is not None
    lock.close()


# ---------- окна ----------


@pytest.fixture
def root():
    try:
        window = tk.Tk()
    except tk.TclError:
        pytest.skip("нет дисплея")
    window.withdraw()
    yield window
    window.destroy()
    # переменные Tk удаляем здесь, в главном потоке: иначе сборщик мусора доберётся до них в потоке записи
    # следующего теста, и Tk будет ждать главный цикл, которого в тестах нет (тормоза и предупреждение)
    gc.collect()


def test_indicator_shows_updates_and_hides(root):
    from govorilka.indicator import RECORDING, RecordingIndicator

    indicator = RecordingIndicator(root)
    indicator.show("Запись 00:00", RECORDING)
    root.update()
    indicator.update_text("Запись 00:05")
    assert indicator._label.cget("text") == "Запись 00:05"
    indicator.hide()
    root.update()
    assert not indicator._window.winfo_viewable()


def test_settings_dialog_records_a_new_hotkey(root):
    from dataclasses import replace

    from govorilka import settings_dialog
    from govorilka.config import Settings

    saved = []
    dialog = settings_dialog.SettingsDialog(root, replace(Settings(), hotkey="ctrl+shift"), False, lambda s, a: saved.append((s, a)))
    keyboard = ScriptedKeyboard([snap(), snap("alt"), snap("alt+cmd"), snap()])
    dialog._recorder = settings_dialog.ChordRecorder()
    dialog._deadline = time.monotonic() + 5
    for _ in range(4):
        dialog._poll_capture(keyboard)
    assert dialog._chord == parse_chord("alt+cmd")
    dialog.hotkey_enabled_var.set(True)
    dialog.autostart_var.set(True)
    dialog._save()
    settings, autostart_on = saved[0]
    assert settings.hotkey == "alt+cmd" and autostart_on is True
