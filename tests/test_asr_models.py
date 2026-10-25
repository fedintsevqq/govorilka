import json
import wave
from pathlib import Path

import numpy as np
import pytest

from govorilka import asr_models, models
from govorilka.config import DEFAULT_MODEL, MODELS_DIR, load_settings

FIXTURES = Path(__file__).parent / "fixtures"


def test_catalog_models_and_their_int8_versions_are_parsed():
    ref = asr_models.parse("gigaam-v3-e2e-rnnt")
    assert ref.load_name == "gigaam-v3-e2e-rnnt" and ref.quantization is None and not ref.local_only
    assert ref.local_dir == MODELS_DIR / "gigaam-v3-e2e-rnnt"
    light = asr_models.parse("gigaam-v3-e2e-rnnt@int8")
    assert light.quantization == "int8" and light.load_name == "gigaam-v3-e2e-rnnt"
    assert light.local_dir == MODELS_DIR / "gigaam-v3-e2e-rnnt@int8"  # рядом с обычной: файлы не смешиваются
    ids = [entry["id"] for entry in asr_models.catalog_entries()]
    assert ids == ["gigaam-v3-e2e-rnnt", "gigaam-v3-e2e-ctc", "gigaam-v3-e2e-rnnt@int8", "gigaam-v3-e2e-ctc@int8"]


def test_hugging_face_models_need_an_author_slash_repository_address():
    ref = asr_models.parse(asr_models.hf_key("istupakov/gigaam-v3-onnx", "int8"))
    assert ref.repo == "istupakov/gigaam-v3-onnx" and ref.quantization == "int8" and not ref.local_only
    assert ref.local_dir == MODELS_DIR / "istupakov--gigaam-v3-onnx@int8"
    for bad in ("hf:просто-слово", "hf:a/b/c", "hf:", "hf: /x"):
        with pytest.raises(asr_models.ModelError):
            asr_models.parse(bad)


def test_folder_models_keep_the_path_with_a_windows_drive_letter(tmp_path):
    key = asr_models.dir_key("nemo-conformer-ctc", "E:\\Модели\\my model")
    assert asr_models.is_valid(key)
    folder = tmp_path / "модель"
    folder.mkdir()
    ref = asr_models.parse(asr_models.dir_key("nemo-conformer-ctc", folder))
    assert ref.local_only and ref.local_dir == folder and ref.load_name == "nemo-conformer-ctc"
    with pytest.raises(asr_models.ModelError):
        asr_models.parse("dir:нет-такого-типа:/tmp/x")


def test_auto_type_is_read_from_config_json(tmp_path):
    with pytest.raises(asr_models.ModelError, match="config.json"):
        asr_models.parse(asr_models.dir_key("auto", tmp_path))  # файла нет: просим выбрать тип вручную
    (tmp_path / "config.json").write_text(json.dumps({"model_type": "nemo-conformer-ctc"}), encoding="utf-8")
    assert asr_models.parse(asr_models.dir_key("auto", tmp_path)).load_name == "nemo-conformer-ctc"
    (tmp_path / "config.json").write_text(json.dumps({"model_type": "gigaam"}), encoding="utf-8")
    with pytest.raises(asr_models.ModelError, match="не поддерживает"):
        asr_models.parse(asr_models.dir_key("auto", tmp_path))


def test_a_broken_or_unknown_model_key_in_settings_falls_back_to_the_default(tmp_path):
    path = tmp_path / "settings.json"
    for broken in ("чужая-модель", "hf:мусор", "dir:тип:"):
        path.write_text(json.dumps({"model": broken}), encoding="utf-8")
        assert load_settings(path).model == DEFAULT_MODEL
    good = asr_models.dir_key("nemo-conformer-ctc", tmp_path)
    path.write_text(json.dumps({"model": good}), encoding="utf-8")
    assert load_settings(path).model == good  # своя модель переживает перезапуск
    path.write_text(json.dumps({"model": "gigaam-v3-e2e-ctc@int8"}), encoding="utf-8")
    assert load_settings(path).model == "gigaam-v3-e2e-ctc@int8"


def test_a_missing_file_in_the_folder_is_named(tmp_path):
    key = asr_models.dir_key("nemo-conformer-ctc", tmp_path)
    with pytest.raises(asr_models.ModelError, match="model.onnx"):
        models.ensure_downloaded("asr", key, "модель", lambda _text: None)


def test_a_folder_model_is_never_downloaded_or_prefetched(tmp_path, monkeypatch):
    key = asr_models.dir_key("nemo-conformer-ctc", tmp_path)
    monkeypatch.setattr(models, "is_downloaded", lambda kind, name: True if kind != "asr" else False)
    assert [kind for kind, _n, _l in models.missing(key)] == []  # распознавание из папки человека не «докачивается»


def test_int8_download_size_counts_the_int8_files():
    from onnx_asr.loader import create_asr_resolver

    resolver = create_asr_resolver("gigaam-v3-e2e-rnnt", MODELS_DIR / "x", offline=True)
    fp32 = list(resolver.model_type._get_model_files(None).values())
    int8 = list(resolver.model_type._get_model_files("int8").values())
    assert any("encoder.onnx" in f for f in fp32) and any("encoder?int8" in f for f in int8)


# ---------- настоящая модель (нужны скачанные модели) ----------


def _models_present():
    return models.is_downloaded("asr", DEFAULT_MODEL)


@pytest.mark.skipif(not _models_present(), reason="модели не скачаны")
def test_the_same_model_loaded_as_a_custom_folder_gives_the_same_text():
    from govorilka.recognizer import Recognizer

    with wave.open(str(FIXTURES / "speech_ru.wav")) as w:
        audio = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768
    catalog = Recognizer(DEFAULT_MODEL).transcribe(audio)
    folder = asr_models.dir_key(DEFAULT_MODEL, models.local_dir(DEFAULT_MODEL))
    custom = Recognizer(folder).transcribe(audio)
    assert catalog and custom == catalog


@pytest.mark.skipif(not _models_present(), reason="модели не скачаны")
def test_a_folder_of_the_wrong_type_is_reported_with_the_missing_file():
    from govorilka.recognizer import Recognizer, RecognizerError

    wrong = asr_models.dir_key("nemo-conformer-ctc", models.local_dir(DEFAULT_MODEL))
    with pytest.raises(RecognizerError, match="model.onnx"):
        Recognizer(wrong)
