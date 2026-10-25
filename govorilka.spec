# -*- mode: python ; coding: utf-8 -*-
# Сборка Говорилки: на Windows — один .exe, на macOS — Govorilka.app (потом упаковывается в .dmg).
# Модели (GigaAM, Silero, wespeaker) в сборку не входят: приложение скачает их при первом запуске.
import os
import sys

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, copy_metadata

IS_MAC = sys.platform == "darwin"
VERSION = os.environ.get("GOVORILKA_VERSION", "0.0.0")
MAC_ARCH = os.environ.get("GOVORILKA_ARCH", "arm64")  # arm64 — Apple Silicon, x86_64 — Intel
MAC_MIN_OS = "13.0" if MAC_ARCH == "x86_64" else "14.0"  # под onnxruntime для своей архитектуры

# номер версии из тега релиза: приложение узнаёт по нему, не вышла ли новая (govorilka/version.py)
with open("govorilka/_build_version.py", "w", encoding="utf-8") as _version_file:
    _version_file.write(f'VERSION = "{VERSION}"\n')

datas = collect_data_files("onnx_asr")  # предобработчики звука в .onnx лежат внутри пакета
# livekit грузит нативную библиотеку WebRTC (эхоподавление) из своего пакета через importlib.resources
datas += collect_data_files("livekit", includes=["rtc/resources/*"])
# интерфейс: окна QML и иконки лежат рядом с кодом, из собранного приложения их читает Qt
datas += [("govorilka/qml", "govorilka/qml"), ("govorilka/assets", "govorilka/assets")]


def qt_qml_modules():
    """Модули QML, которые нужны нашим окнам. PyInstaller сам по QML-файлам их не находит."""
    import PySide6
    from PySide6.QtCore import QLibraryInfo

    # на Windows это PySide6/qml, на Mac — PySide6/Qt/qml: спрашиваем у самого Qt
    package = os.path.dirname(PySide6.__file__)
    base = QLibraryInfo.path(QLibraryInfo.LibraryPath.QmlImportsPath)
    wanted = ["QtQml", "QtQuick/Window", "QtQuick/Layouts", "QtQuick/Shapes", "QtQuick/Effects", "QtQuick/Dialogs",
              "QtQuick/Templates", "QtQuick/Controls/Basic", "QtQuick/Controls/impl"]
    found = []
    for module in wanted:
        folder = os.path.join(base, *module.split("/"))
        if not os.path.isdir(folder):
            continue
        for root, _dirs, files in os.walk(folder):
            for name in files:
                found.append((os.path.join(root, name), os.path.join("PySide6", os.path.relpath(root, package))))
    for top in ("QtQuick", "QtQuick/Controls"):  # файлы верхнего уровня (qmldir, плагин), без вложенных стилей
        folder = os.path.join(base, *top.split("/"))
        for name in os.listdir(folder):
            full = os.path.join(folder, name)
            if os.path.isfile(full):
                found.append((full, os.path.join("PySide6", os.path.relpath(folder, package))))
    return found


qml_files = qt_qml_modules()
# .dll/.dylib плагинов QML — бинарные файлы (на Mac их надо подписывать), остальное — данные
binaries_qml = [item for item in qml_files if item[0].lower().endswith((".dll", ".dylib", ".so"))]
datas += [item for item in qml_files if item not in binaries_qml]
# onnx_asr и huggingface_hub при импорте читают версии пакетов через importlib.metadata
for package in ("onnx-asr", "onnxruntime", "huggingface_hub", "numpy"):
    datas += copy_metadata(package)
# библиотеку WebRTC кладём как бинарный файл: на Mac только так она попадает под подпись приложения
binaries = collect_dynamic_libs("livekit") + binaries_qml
if IS_MAC:
    binaries.append(("bin/govorilka-audiotap", "bin"))  # Swift-помощник, собирается заранее

a = Analysis(
    ["run_govorilka.py"],
    pathex=["."],
    binaries=binaries,
    datas=datas,
    # модули, которые грузятся лениво внутри функций, — PyInstaller сам их не видит
    hiddenimports=[
        "govorilka.audio_mac" if IS_MAC else "govorilka.audio_windows",
        *([] if IS_MAC else ["govorilka.win_devices"]),
        "govorilka.selftest",
        "av",
        "onnxruntime",
        "soxr",
        "livekit.rtc",
        "livekit.rtc.resources",
        "govorilka.qt.app",
        "PySide6.QtQuick",
        "PySide6.QtQml",
        "PySide6.QtQuickControls2",
        "PySide6.QtSvg",
        "PySide6.QtWidgets",
    ],
    excludes=[
        "torch", "matplotlib", "IPython", "pytest", "tkinter", "PIL",
        # из Qt нам нужен только QML, трей и SVG: остальное только раздувает приложение
        "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.Qt3DCore", "PySide6.QtCharts",
        "PySide6.QtDataVisualization", "PySide6.QtMultimedia", "PySide6.QtPdf", "PySide6.QtDesigner",
        "PySide6.QtSql", "PySide6.QtTest", "PySide6.QtBluetooth", "PySide6.QtNetworkAuth",
    ],
    noarchive=False,
)
pyz = PYZ(a.pure)

if IS_MAC:
    exe = EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        name="Govorilka",
        console=False,
        target_arch=MAC_ARCH,
        icon="govorilka/assets/icon.icns",
    )
    coll = COLLECT(exe, a.binaries, a.datas, name="Govorilka")
    app = BUNDLE(
        coll,
        name="Govorilka.app",
        icon="govorilka/assets/icon.icns",
        bundle_identifier="io.github.fedintsevqq.govorilka",
        version=VERSION,
        info_plist={
            "CFBundleDisplayName": "Говорилка",
            "CFBundleShortVersionString": VERSION,
            "CFBundleVersion": VERSION,
            "LSApplicationCategoryType": "public.app-category.productivity",
            "LSMinimumSystemVersion": MAC_MIN_OS,
            "NSHighResolutionCapable": True,
            "NSMicrophoneUsageDescription": "Говорилка записывает ваш голос, чтобы расшифровать созвон.",
            "NSAppleEventsUsageDescription": "Говорилка открывает Терминал, чтобы передать транскрипт в Claude Code.",
        },
    )
else:
    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.datas,
        [],
        name="Govorilka",
        console=False,
        upx=False,
        icon="govorilka/assets/icon.ico",
    )
