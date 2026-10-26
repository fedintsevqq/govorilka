"""Проверка, что все модели по-прежнему скачиваются: python tools/check_models.py [--write] [--e2e].

Без флагов — быстрая проверка без скачивания самих файлов (заголовки и один байт):
- модели итогов и подсказок из catalog/models.json: каждый адрес отвечает, ведёт на разрешённый сервер, файл там того
  же размера, а у Hugging Face и ModelScope ещё и та же SHA-256;
- движок llama.cpp под все платформы: архив на месте;
- модели распознавания, детектор речи и модель голосов: репозитории на Hugging Face существуют.
--write  — пересобрать catalog/models.json из встроенного каталога (после правки summary.py).
--e2e    — скачать движок и самую лёгкую модель итогов по-настоящему и составить итоги короткого разговора.

Код выхода 0 — всё живо. Запускается в CI каждый день: если ссылка умерла, мы узнаем раньше людей и поправим
catalog/models.json — приложения подхватят новый адрес без новой версии.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
CATALOG_FILE = ROOT / "catalog" / "models.json"


def _no_redirect() -> urllib.request.OpenerDirector:
    class Stop(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):  # noqa: ANN002, ANN003, ANN201
            return None

    from govorilka import net

    return urllib.request.build_opener(urllib.request.HTTPSHandler(context=net.ssl_context()), Stop())


def _head(url: str) -> dict[str, str]:
    """Заголовки первого ответа (до переадресации): Hugging Face и ModelScope кладут туда размер и SHA-256."""
    request = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "Govorilka-check"})
    try:
        with _no_redirect().open(request, timeout=30) as response:
            return {k.lower(): v for k, v in response.headers.items()}
    except urllib.error.HTTPError as exc:
        if exc.code in (301, 302, 303, 307, 308):
            return {k.lower(): v for k, v in exc.headers.items()}
        raise


def _first_byte(url: str, allowed: tuple[str, ...]) -> tuple[int, int]:
    """Один байт по Range через все переадресации: (код ответа, полный размер файла)."""
    from govorilka import installer, net, summary

    request = urllib.request.Request(url, headers={"User-Agent": "Govorilka-check", "Range": "bytes=0-0"})
    with urllib.request.urlopen(request, timeout=60, context=net.ssl_context()) as response:  # noqa: S310
        installer._check_url(response.geturl(), allowed)  # noqa: SLF001
        span = summary._content_range(response)  # noqa: SLF001
        return response.status, span[1] if span else int(response.headers.get("Content-Length") or 0)


def check_llm(document: dict) -> list[str]:
    from govorilka import model_catalog, summary

    problems = []
    models, engine = model_catalog.parse(document)
    for model in models:
        for url in model.urls:
            where = f"{model.key} @ {url.split('/')[2]}"
            try:
                head = _head(url)
                linked_sha = (head.get("x-linked-etag") or "").strip('"').lower()
                if linked_sha and linked_sha != model.sha256:
                    problems.append(f"{where}: SHA-256 на сервере {linked_sha}, в каталоге {model.sha256}")
                status, total = _first_byte(url, summary.MODEL_HOSTS)
                if status != 206:
                    problems.append(f"{where}: сервер не отдаёт файл кусками (ответ {status})")
                if total != model.size:
                    problems.append(f"{where}: размер {total}, в каталоге {model.size}")
                print(f"OK  {where}")
            except Exception as exc:  # noqa: BLE001
                problems.append(f"{where}: {exc}")
    builds = (engine[1] if engine else summary.ENGINES).items()
    build_id = engine[0] if engine else summary.ENGINE_BUILD
    for (system, arch), build in builds:
        for url in build.urls or (f"https://github.com/ggml-org/llama.cpp/releases/download/{build_id}/{build.asset}",):
            try:
                status, total = _first_byte(url, summary.GITHUB + summary.MODEL_HOSTS)
                if status not in (200, 206) or total <= 0:
                    problems.append(f"движок {system}-{arch}: ответ {status}, размер {total}")
                print(f"OK  движок {system}-{arch}")
            except Exception as exc:  # noqa: BLE001
                problems.append(f"движок {system}-{arch} @ {url}: {exc}")
    return problems


def check_asr() -> list[str]:
    from huggingface_hub import HfApi

    from govorilka import asr_models, models

    problems = []
    names = [("asr", entry.key) for entry in asr_models.CATALOG]
    names += [("vad", models.VAD_NAME), ("se", models.SPEAKER_REPO)]
    for kind, name in names:
        try:
            repo = models._resolver(kind, name, offline=False).repo_id  # noqa: SLF001
            info = HfApi().model_info(repo, timeout=30)
            if not info.siblings:
                problems.append(f"{name}: в репозитории {repo} нет файлов")
            print(f"OK  {kind} {name} ({repo})")
        except Exception as exc:  # noqa: BLE001
            problems.append(f"{kind} {name}: {exc}")
    return problems


def e2e() -> list[str]:
    """Настоящая загрузка движка и самой лёгкой модели и итоги короткого разговора."""
    from govorilka import summary

    model = min(summary.CATALOG, key=lambda m: m.size)
    engine = summary.ensure_engine(print)
    weights = summary.ensure_model(model, print)
    server = summary.LlamaServer(engine, weights)
    try:
        server.wait_ready()
        text = summary.summarize(server, "Я: Давайте выпустим релиз в пятницу.\nСобеседник 1: Хорошо, я подготовлю сборку к четвергу.")
    finally:
        server.stop()
    print(text)
    return [] if "###" in text else [f"итоги не похожи на итоги: {text[:200]!r}"]


def main(argv: list[str]) -> int:
    if "--write" in argv:
        from govorilka import model_catalog

        CATALOG_FILE.parent.mkdir(exist_ok=True)
        CATALOG_FILE.write_text(json.dumps(model_catalog.builtin_document(), ensure_ascii=False, indent=2) + "\n",
                                encoding="utf-8", newline="\n")
        print(f"записан {CATALOG_FILE}")
        return 0
    if "--e2e" in argv:
        os.environ.setdefault("GOVORILKA_DATA_DIR", tempfile.mkdtemp(prefix="govorilka-e2e-"))
        problems = e2e()
    else:
        problems = check_llm(json.loads(CATALOG_FILE.read_text(encoding="utf-8"))) + check_asr()
    for problem in problems:
        print(f"FAIL {problem}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
