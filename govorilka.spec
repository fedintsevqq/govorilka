# -*- mode: python ; coding: utf-8 -*-
# Сборка Говорилки: на Windows — один .exe, на macOS — Govorilka.app (потом упаковывается в .dmg).
# Модель GigaAM в сборку не входит: приложение скачает её при первой записи.
import os
import sys

from PyInstaller.utils.hooks import collect_data_files, copy_metadata

IS_MAC = sys.platform == "darwin"
VERSION = os.environ.get("GOVORILKA_VERSION", "0.0.0")

datas = collect_data_files("onnx_asr")  # предобработчики звука в .onnx лежат внутри пакета
# onnx_asr и huggingface_hub при импорте читают версии пакетов через importlib.metadata
for package in ("onnx-asr", "onnxruntime", "huggingface_hub", "numpy"):
    datas += copy_metadata(package)
binaries = []
if IS_MAC:
    binaries.append(("bin/govorilka-audiotap", "bin"))  # Swift-помощник, собирается заранее

a = Analysis(
    ["run_govorilka.py"],
    pathex=["."],
    binaries=binaries,
    datas=datas,
    hiddenimports=["govorilka.audio_mac" if IS_MAC else "govorilka.audio_windows"],
    excludes=["torch", "matplotlib", "IPython", "pytest"],
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
        target_arch="arm64",
    )
    coll = COLLECT(exe, a.binaries, a.datas, name="Govorilka")
    app = BUNDLE(
        coll,
        name="Govorilka.app",
        bundle_identifier="io.github.fedintsevqq.govorilka",
        version=VERSION,
        info_plist={
            "CFBundleDisplayName": "Говорилка",
            "CFBundleShortVersionString": VERSION,
            "LSMinimumSystemVersion": "14.0",
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
    )
