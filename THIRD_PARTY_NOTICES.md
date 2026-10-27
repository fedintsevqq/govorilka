# Чужие компоненты в Говорилке

Говорилка — открытая программа под лицензией [MIT](LICENSE). В неё входят и ей пользуются чужие программы и модели
со своими лицензиями. Здесь перечислены авторы и условия. Сведения по пакетам взяты из их собственных описаний
(метаданных); по моделям — из карточек на Hugging Face на 27 октября 2026. Если нашли ошибку, напишите в
[Issues](https://github.com/fedintsevqq/govorilka/issues): исправим.

## Что входит в сам файл программы

| Компонент | Версия | Лицензия | Автор и источник |
|---|---|---|---|
| Qt и PySide6 | 6.11 | LGPL-3.0 | The Qt Company, https://www.qt.io, https://pyside.org |
| onnx-asr | 0.12.0 | MIT | Ilya Stupakov, https://github.com/istupakov/onnx-asr |
| ONNX Runtime | 1.31 (на Mac Intel 1.23) | MIT | Microsoft, https://onnxruntime.ai |
| NumPy | 2.5 | BSD-3-Clause | NumPy Developers, https://numpy.org |
| SciPy | 1.18 | BSD-3-Clause | SciPy Developers, https://scipy.org |
| python-soxr (libsoxr) | 1.1 | LGPL-2.1 или новее | Rob Sykes (libsoxr), dofuuz (обёртка), https://github.com/dofuuz/python-soxr |
| PyAV | 19.0 | BSD-3-Clause | PyAV Contributors, https://github.com/PyAV-Org/PyAV |
| FFmpeg и кодеки внутри PyAV | 9.0 | LGPL-2.1 и GPL-2.0 (см. ниже) | FFmpeg developers, https://ffmpeg.org |
| LiveKit (эхоподавление WebRTC) | 1.1 | Apache-2.0 | LiveKit, https://livekit.io |
| PyAudioWPatch (только Windows) | 0.2.12 | Apache-2.0 | s0d3s, https://github.com/s0d3s/PyAudioWPatch |
| comtypes (только Windows) | 1.4 | MIT | Thomas Heller, Enthought, https://github.com/enthought/comtypes |
| truststore | 0.10 | MIT | Seth Michael Larson, https://github.com/sethmlarson/truststore |
| Python | 3.12 | PSF License | Python Software Foundation, https://www.python.org |
| PyInstaller (упаковка) | 6.x | GPL-2.0 с исключением для собранных программ | https://pyinstaller.org |

### Qt и PySide6 (LGPL-3.0)

Говорилка использует Qt и PySide6 как отдельные библиотеки, без изменений. Вы вправе заменить их своей сборкой:
исходный код Говорилки открыт, программа собирается заново командой из раздела «Для разработки» в
[README](README.md), с любой совместимой версией PySide6. Тексты лицензий: https://www.gnu.org/licenses/lgpl-3.0.html.
Исходный код Qt: https://download.qt.io, PySide6: https://code.qt.io/cgit/pyside/pyside-setup.git.

### python-soxr (LGPL-2.1+)

Библиотека для пересчёта частоты дискретизации звука. Используется как есть, без изменений.
Текст лицензии: https://www.gnu.org/licenses/old-licenses/lgpl-2.1.html.
Исходный код: https://github.com/dofuuz/python-soxr и https://sourceforge.net/projects/soxr.

### FFmpeg внутри PyAV (LGPL-2.1+, в сборке для Windows есть и GPL-2.0)

Говорилка берёт PyAV только для чтения аудио и видео из файлов (раздел «Расшифровка файлов»). Колёса PyAV
для Windows содержат готовую сборку FFmpeg вместе с кодеками **x264 и x265, а они распространяются по GPL-2.0**.
Нашей программе эти кодеки не нужны, но они лежат в сборке рядом с FFmpeg. В сборке для macOS состав может
отличаться.

Источники этих компонентов: FFmpeg — https://ffmpeg.org/download.html; x264 — https://www.videolan.org/developers/x264.html;
x265 — https://bitbucket.org/multicoreware/x265_git; сборка колёс — https://github.com/PyAV-Org/PyAV.
Тексты лицензий: https://www.gnu.org/licenses/old-licenses/gpl-2.0.html и
https://www.gnu.org/licenses/old-licenses/lgpl-2.1.html. Если вам нужны исходные тексты именно тех версий, что
лежат в нашей сборке, напишите в [Issues](https://github.com/fedintsevqq/govorilka/issues): пришлём ссылку.

## Что скачивается отдельно при использовании

Эти компоненты не входят в файл программы. Говорилка скачивает их с Hugging Face и GitHub по вашей просьбе или при
первом запуске, проверяет контрольную сумму и складывает в свою папку данных.

### Движок итогов и подсказок

| Компонент | Лицензия | Автор и источник |
|---|---|---|
| llama.cpp (llama-server) | MIT | ggml authors, https://github.com/ggml-org/llama.cpp |

### Модели распознавания речи

| Модель | Лицензия | Автор и источник |
|---|---|---|
| GigaAM v3 (RNNT, CTC), ONNX-версия | MIT | SberDevices / Сбер, https://github.com/salute-developers/GigaAM; ONNX: https://huggingface.co/istupakov/gigaam-v3-onnx |
| GigaAM multilingual, ONNX-версия | MIT | Сбер; ONNX: https://huggingface.co/istupakov/gigaam-multilingual-large-ctc-onnx |
| T-One (t-tech/t-one) | Apache-2.0 | Т-Технологии, https://huggingface.co/t-tech/t-one |
| Parakeet TDT 0.6B v3, ONNX-версия | **CC BY 4.0** | © NVIDIA; ONNX: https://huggingface.co/istupakov/parakeet-tdt-0.6b-v3-onnx |
| FastConformer RU (stt_ru_fastconformer_hybrid_large_pc), ONNX-версия | **CC BY 4.0** | © NVIDIA; ONNX: https://huggingface.co/istupakov/stt_ru_fastconformer_hybrid_large_pc_onnx |
| Vosk-model-ru и vosk-model-small-ru | Apache-2.0 | Alpha Cephei, https://alphacephei.com/vosk |
| Silero VAD, ONNX-версия | MIT | Silero Team, https://github.com/snakers4/silero-vad; ONNX: https://huggingface.co/istupakov/silero-vad-onnx |
| WeSpeaker ResNet34-LM (модель голосов) | **CC BY 4.0** | © WeSpeaker authors, https://huggingface.co/Wespeaker/wespeaker-voxceleb-resnet34-LM |

Модели с лицензией **CC BY 4.0** (Parakeet, FastConformer RU, WeSpeaker) требуют указывать автора и источник, что
мы и делаем здесь. Лицензия: https://creativecommons.org/licenses/by/4.0/. Модели скачиваются без изменений. ONNX-версии
подготовил Ilya Stupakov (istupakov) из исходных моделей.

### Языковые модели для итогов и подсказок

| Модель | Лицензия | Автор и источник |
|---|---|---|
| Qwen3 4B Instruct 2507 (GGUF от Unsloth) | Apache-2.0 | Alibaba Cloud (Qwen), https://huggingface.co/unsloth/Qwen3-4B-Instruct-2507-GGUF |
| Qwen2.5 1.5B Instruct (GGUF) | Apache-2.0 | Alibaba Cloud (Qwen), https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct-GGUF |
| YandexGPT 5 Lite 8B Instruct (GGUF) | Лицензия Яндекса (yandexgpt-5-lite-8b) | Яндекс, https://huggingface.co/yandex/YandexGPT-5-Lite-8B-instruct-GGUF |

Условия YandexGPT 5 Lite 8B — собственная лицензия Яндекса, она лежит в репозитории модели; перед коммерческим
использованием прочтите её сами. Файл модели в наш установщик не входит: Говорилка скачивает его с Hugging Face по
вашей просьбе, условия лицензии смотрите на странице модели.

## Сервисы, к которым вы можете подключиться

В режиме «Через нейросеть в интернете» Говорилка отправляет текст провайдеру, которого выбрали вы: OpenAI, DeepSeek,
OpenRouter, Anthropic (Claude) или свой адрес. Названия ChatGPT, Claude, Gemini, DeepSeek, GigaChat и Алиса принадлежат
их владельцам и используются только для обозначения сервисов; Говорилка с ними не связана.
