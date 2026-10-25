"""Краткая выжимка из отчёта о сбое macOS (.ips): тип сбоя и нативные стеки упавшего и главного потоков.

    python3 tools/ips_summary.py ~/Library/Logs/DiagnosticReports/Govorilka-….ips

Отчёт .ips — две части JSON: заголовок в первой строке и сам отчёт дальше. Нужен в CI, где сбой в Python видно только
частично (faulthandler показывает, где сбой проявился, а не кто испортил память).
"""

from __future__ import annotations

import json
import sys


def frames(thread: dict, images: list[dict], limit: int = 40) -> list[str]:
    lines = []
    for frame in thread.get("frames", [])[:limit]:
        image = images[frame["imageIndex"]] if frame.get("imageIndex", -1) < len(images) else {}
        name = image.get("name") or image.get("path", "?").rsplit("/", 1)[-1]
        symbol = frame.get("symbol", f"+{frame.get('imageOffset', 0)}")
        lines.append(f"    {name:40} {symbol}")
    return lines


def main(path: str) -> None:
    text = open(path, encoding="utf-8").read()
    body = json.loads(text[text.index("\n") + 1:])
    print("exception:", body.get("exception"), body.get("termination", {}).get("indicator", ""))
    for note in body.get("asi", {}).values() if isinstance(body.get("asi"), dict) else []:
        print("asi:", note)
    images = body.get("usedImages", [])
    crashed = body.get("faultingThread", 0)
    threads = body.get("threads", [])
    for index, thread in enumerate(threads):
        if index == crashed or index == 0 or thread.get("name") in ("QSGRenderThread",):
            label = "CRASHED" if index == crashed else thread.get("name") or thread.get("queue") or ""
            print(f"--- thread {index} {label}")
            print("\n".join(frames(thread, images)))


if __name__ == "__main__":
    main(sys.argv[1])
