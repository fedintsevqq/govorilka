import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Dialogs
import QtQuick.Window

// Настройки. Одно окно с карточками-разделами; «Сохранить» применяет всё сразу (клавиша, автозапуск, тема).
Window {
    id: win
    objectName: "settings"
    width: 640
    height: 780
    minimumWidth: 560
    minimumHeight: 520
    title: "Настройки — Говорилка"
    color: Theme.bg
    visible: false
    transientParent: null

    property var draft: ({})
    property var original: ({})
    // своя модель: проверенный ключ, открыта ли панель выбора, идёт ли проверка и её итог
    property string customKey: ""
    property bool customOpen: false
    property bool customChecking: false
    property string customMessage: ""
    property bool customOk: false
    Connections {
        target: backend
        function onCustomModelChecked(ok, message, key) {
            win.customChecking = false
            win.customOk = ok
            win.customMessage = message
            if (ok) {
                win.customKey = key
                win.setValue("model", key)
                win.customOpen = false
            }
        }
    }   // как было при открытии: сохраняем только то, что поменяли здесь
    property bool autostart: false
    property string hotkeyLabel: ""
    property string dictationLabel: ""
    property string dictationWarning: ""
    property string hotkeyWarning: ""
    readonly property bool hasDesktopApp: ["chatgpt", "claude", "claude_code"].indexOf(draft.ai_target) >= 0
    readonly property bool claudeCode: draft.ai_target === "claude_code"   // папка нужна только Claude Code
    signal tourRequested()

    function open() {
        draft = JSON.parse(JSON.stringify(backend.settings))
        original = JSON.parse(JSON.stringify(backend.settings))
        autostart = backend.autostart
        hotkeyLabel = draft.hotkeyLabel
        hotkeyWarning = ""
        dictationLabel = draft.dictationLabel
        dictationWarning = ""
        promptField.text = draft.claude_prompt
        profileField.text = draft.assistant_profile || ""
        apiUrlField.text = draft.assistant_api_url || ""
        apiModelField.text = draft.assistant_api_model || ""
        apiKeyField.text = ""
        apiBox.currentIndex = Math.max(0, backend.apiPresets.findIndex(p => p.id === draft.assistant_api_preset))
        folderField.text = draft.transcripts_dir
        claudeField.text = draft.claude_workdir
        customKey = backend.isCustomModel(draft.model) ? draft.model : ""
        customOpen = false
        customChecking = false
        customMessage = ""
        catalogList.expanded = false
        backend.refreshCatalog()
        aiBox.currentIndex = Math.max(0, aiBox.find(draft.ai_target))
        openBox.currentIndex = draft.ai_open_in === "browser" ? 1 : 0
        unloadBox.currentIndex = Math.max(0, [5, 10, 30, 0].indexOf(draft.unload_after_min))
        themeBox.currentIndex = ["system", "dark", "light"].indexOf(draft.theme)
        actionBox.currentIndex = draft.hotkey_action === "record" ? 1 : 0
        show()
        raise()
        requestActivate()
        backend.watchKeys("settings", true)
    }
    function setValue(name, value) { draft[name] = value; draftChanged() }
    onClosing: backend.watchKeys("settings", false)
    onVisibleChanged: if (!visible) backend.watchKeys("settings", false)

    Backdrop { anchors.fill: parent }

    Connections {
        target: backend
        function onCapturedHotkey(chord, label, warning) {
            win.setValue("hotkey", chord)
            win.hotkeyLabel = label
            win.hotkeyWarning = warning
        }
        function onCapturedDictationHotkey(chord, label, warning) {
            win.setValue("dictation_hotkey", chord)
            win.dictationLabel = label
            win.dictationWarning = warning
        }
    }

    // ---------- поля ----------
    component FieldBox: Rectangle {
        id: box
        property alias text: input.text
        property alias placeholder: input.placeholderText
        property bool trailingButton: false
        signal browse()
        Layout.fillWidth: true
        implicitHeight: 42
        radius: 14
        color: Theme.core
        border.color: input.activeFocus ? Qt.rgba(Theme.accent.r, Theme.accent.g, Theme.accent.b, 0.6) : Theme.shellLine
        TextField {
            id: input
            anchors.fill: parent
            anchors.rightMargin: box.trailingButton ? 40 : 0
            leftPadding: 14
            color: Theme.text
            placeholderTextColor: Theme.textTertiary
            font.pixelSize: 14
            selectByMouse: true
            background: Item {}
        }
        IconButton {
            visible: box.trailingButton
            iconName: "folder"
            size: 32
            anchors { right: parent.right; rightMargin: 5; verticalCenter: parent.verticalCenter }
            tip: "Выбрать папку"
            onClicked: box.browse()
        }
    }
    component ComboField: ComboBox {
        id: combo
        Layout.fillWidth: true
        implicitHeight: 42
        font.pixelSize: 14
        background: Rectangle {
            radius: 14
            color: Theme.core
            border.color: combo.visualFocus || combo.popup.visible ? Qt.rgba(Theme.accent.r, Theme.accent.g, Theme.accent.b, 0.6) : Theme.shellLine
        }
        contentItem: Text {
            leftPadding: 14
            rightPadding: 40   // не заезжать под стрелку
            elide: Text.ElideRight
            verticalAlignment: Text.AlignVCenter
            text: combo.displayText
            font: combo.font
            color: Theme.text
        }
        indicator: Icon { x: combo.width - width - 14; y: (combo.height - height) / 2; name: "chevron"; size: 14; color: Theme.textTertiary; rotation: 90 }
        delegate: ItemDelegate {
            width: combo.width
            height: 38
            contentItem: Text { text: modelData.label !== undefined ? modelData.label : modelData; font.pixelSize: 14; color: Theme.text; verticalAlignment: Text.AlignVCenter }
            background: Rectangle { radius: 10; color: highlighted ? Theme.hover : "transparent" }
            highlighted: combo.highlightedIndex === index
        }
        popup: Popup {
            y: combo.height + 4
            width: combo.width
            implicitHeight: Math.min(contentItem.implicitHeight + 12, 280)
            padding: 6
            contentItem: ListView { clip: true; implicitHeight: contentHeight; model: combo.popup.visible ? combo.delegateModel : null; currentIndex: combo.highlightedIndex }
            background: Rectangle { radius: 16; color: Theme.glass; border.color: Theme.shellLine }
        }
    }
    component Section: Bezel {
        id: sec
        property string heading: ""
        default property alias body: bodyColumn.data
        Layout.fillWidth: true
        implicitHeight: bodyColumn.implicitHeight + 64
        ColumnLayout {
            id: bodyColumn
            anchors { left: parent.left; right: parent.right; top: parent.top; margins: 20; topMargin: 16 }
            spacing: 10
            Eyebrow { text: sec.heading; tint: Theme.textTertiary }
        }
    }
    component Label: Text { font.pixelSize: 13; font.weight: Font.Medium; color: Theme.textSecondary; Layout.topMargin: 4 }

    FolderDialog {
        id: folderDialog
        property var target
        onAccepted: if (target) target.text = selectedFolder.toString().replace(/^file:\/\/\//, "")
    }

    function showCatalog() { catalogList.expanded = true; scroll.contentItem.contentY = Math.max(0, catalogList.mapToItem(scroll.contentItem.contentItem, 0, 0).y - 60) }   // для снимков
    function openAt(section) {   // открыть настройки сразу на нужном разделе
        open()
        if (section === "summary") Qt.callLater(() => { scroll.contentItem.contentY = Math.max(0, summarySection.y - 12) })
    }
    function showAssistant() { scroll.contentItem.contentY = Math.max(0, assistantSection.y - 12) }   // для снимков
    function showUpdates() { scroll.contentItem.contentY = Math.max(0, updatesSection.y - 12) }   // для снимков интерфейса

    ScrollView {
        id: scroll
        anchors { fill: parent; bottomMargin: 76 }
        contentWidth: availableWidth
        clip: true
        ScrollBar.vertical.policy: ScrollBar.AsNeeded

        ColumnLayout {
            // на большом экране колонка не растягивается во всю ширину: строки остаются читаемыми
            width: Math.min(scroll.availableWidth - 32, 760)
            x: Math.max(16, (scroll.availableWidth - width) / 2)
            spacing: 14

            Item { Layout.preferredHeight: 4 }
            Text { text: "Настройки"; font.pixelSize: 28; font.weight: Font.Bold; font.letterSpacing: -0.8; color: Theme.text; Layout.leftMargin: 6 }

            Section {
                heading: "Горячая клавиша"
                ToggleRow {
                    title: "Управлять записью с клавиатуры"
                    hint: "Работает, когда окно свёрнуто или закрыто в трей. Удерживайте комбинацию полсекунды."
                    checked: win.draft.hotkey_enabled === true
                    onToggled: (v) => win.setValue("hotkey_enabled", v)
                }
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 10
                    opacity: win.draft.hotkey_enabled ? 1 : 0.45
                    Rectangle {
                        Layout.fillWidth: true
                        implicitHeight: 44
                        radius: 14
                        color: Theme.core
                        readonly property bool mine: backend.capturing && backend.captureTarget === "hotkey"
                        border.color: mine ? Qt.rgba(Theme.accent.r, Theme.accent.g, Theme.accent.b, 0.7) : Theme.shellLine
                        Row {
                            anchors.verticalCenter: parent.verticalCenter
                            x: 14
                            spacing: 6
                            visible: !parent.mine
                            Repeater { model: win.hotkeyLabel.split(" + "); KeyCap { required property string modelData; text: modelData } }
                        }
                        Text { visible: parent.mine; x: 14; anchors.verticalCenter: parent.verticalCenter; text: "Нажмите комбинацию и отпустите…"; font.pixelSize: 14; color: Theme.accent }
                    }
                    PillButton {
                        readonly property bool mine: backend.capturing && backend.captureTarget === "hotkey"
                        text: mine ? "Отмена" : "Изменить…"
                        tip: mine ? "Оставить прежнюю комбинацию" : "Задать новую комбинацию: нажмите нужные клавиши и отпустите"
                        implicitHeight: 44
                        onClicked: mine ? backend.stopCapture() : backend.startCapture()
                    }
                }
                Text { text: "Сейчас нажато: " + (backend.liveKeys || "ничего"); font.pixelSize: 12; color: Theme.textTertiary }
                Text {
                    visible: win.hotkeyWarning !== ""
                    Layout.fillWidth: true
                    wrapMode: Text.WordWrap
                    text: win.hotkeyWarning
                    font.pixelSize: 13
                    color: Theme.pause
                }
                Text {
                    visible: backend.inputMonitoringMissing
                    Layout.fillWidth: true
                    wrapMode: Text.WordWrap
                    text: "macOS не даёт Говорилке видеть клавиши. Включите её в «Системные настройки → Конфиденциальность и безопасность → Мониторинг ввода» и перезапустите."
                    font.pixelSize: 13
                    color: Theme.record
                }
                Label { text: "По комбинации" }
                ComboField {
                    id: actionBox
                    model: ["Открыть круговое меню у курсора", "Сразу начать или остановить запись"]
                    onActivated: win.setValue("hotkey_action", currentIndex === 1 ? "record" : "menu")
                }
            }

            Section {
                heading: "Диктовка"
                ToggleRow {
                    title: "Диктовка в любое поле"
                    hint: "Зажмите комбинацию и говорите. Отпустите — текст появится там, где стоит курсор: в браузере, Claude, Discord, Telegram, Word. Внизу экрана в это время видна плашка с голосом."
                    checked: win.draft.dictation_enabled === true
                    onToggled: (v) => win.setValue("dictation_enabled", v)
                }
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 10
                    opacity: win.draft.dictation_enabled ? 1 : 0.45
                    Rectangle {
                        Layout.fillWidth: true
                        implicitHeight: 44
                        radius: 14
                        color: Theme.core
                        readonly property bool mine: backend.capturing && backend.captureTarget === "dictation"
                        border.color: mine ? Qt.rgba(Theme.accent.r, Theme.accent.g, Theme.accent.b, 0.7) : Theme.shellLine
                        Row {
                            anchors.verticalCenter: parent.verticalCenter
                            x: 14
                            spacing: 6
                            visible: !parent.mine
                            Repeater { model: win.dictationLabel.split(" + "); KeyCap { required property string modelData; text: modelData } }
                        }
                        Text { visible: parent.mine; x: 14; anchors.verticalCenter: parent.verticalCenter; text: "Нажмите комбинацию и отпустите…"; font.pixelSize: 14; color: Theme.accent }
                    }
                    PillButton {
                        readonly property bool mine: backend.capturing && backend.captureTarget === "dictation"
                        text: mine ? "Отмена" : "Изменить…"
                        tip: mine ? "Оставить прежнюю комбинацию" : "Задать комбинацию диктовки: нажмите нужные клавиши и отпустите"
                        implicitHeight: 44
                        onClicked: mine ? backend.stopCapture() : backend.startDictationCapture()
                    }
                }
                Text {
                    visible: win.dictationWarning !== ""
                    Layout.fillWidth: true
                    wrapMode: Text.WordWrap
                    text: win.dictationWarning
                    font.pixelSize: 13
                    color: Theme.pause
                }
                Text {
                    Layout.fillWidth: true
                    wrapMode: Text.WordWrap
                    text: Qt.platform.os === "osx"
                          ? "Чтобы текст вставлялся сам, macOS нужен «Универсальный доступ» для Говорилки. Без него текст окажется в буфере: нажмите ⌘V."
                          : "Прежнее содержимое буфера обмена вернётся на место сразу после вставки."
                    font.pixelSize: 12
                    color: Theme.textTertiary
                }
            }

            Section {
                heading: "Запись"
                Label { text: "Модель распознавания" }
                // Каталог: сначала видна только выбранная модель, «Все модели» раскрывают остальные.
                ColumnLayout {
                    id: catalogList
                    Layout.fillWidth: true
                    spacing: 8
                    property bool expanded: false
                    readonly property bool customChosen: backend.isCustomModel(win.draft.model || "")
                    Repeater {
                        model: backend.catalog
                        ModelCard {
                            required property var modelData
                            info: modelData
                            selected: win.draft.model === modelData.key
                            visible: catalogList.expanded || selected
                            downloading: backend.modelDownloading === modelData.key
                            fraction: backend.modelDownloadFraction
                            busy: backend.modelDownloading !== ""
                            onChoose: { win.customOpen = false; win.setValue("model", modelData.key) }
                            onDownload: backend.downloadModel(modelData.key)
                            onRemove: backend.deleteModel(modelData.key)
                        }
                    }
                    Rectangle {   // выбрана своя модель
                        visible: catalogList.customChosen
                        Layout.fillWidth: true
                        implicitHeight: 52
                        radius: 14
                        color: Qt.rgba(Theme.accent.r, Theme.accent.g, Theme.accent.b, Theme.dark ? 0.12 : 0.08)
                        border.color: Qt.rgba(Theme.accent.r, Theme.accent.g, Theme.accent.b, 0.55)
                        Text {
                            anchors { left: parent.left; right: parent.right; verticalCenter: parent.verticalCenter; leftMargin: 16; rightMargin: 16 }
                            elide: Text.ElideMiddle
                            text: backend.modelLabel(win.draft.model || "")
                            font.pixelSize: 14
                            font.weight: Font.DemiBold
                            color: Theme.text
                        }
                    }
                    RowLayout {
                        spacing: 8
                        PillButton {
                            text: catalogList.expanded ? "Свернуть" : "Все модели (" + backend.catalog.length + ")"
                            iconName: "chevron"
                            tip: "Другие модели: для звонков, других языков, слабых компьютеров"
                            onClicked: catalogList.expanded = !catalogList.expanded
                        }
                        PillButton {
                            text: "Своя модель…"
                            tip: "Модель с Hugging Face или из папки на компьютере"
                            onClicked: win.customOpen = !win.customOpen
                        }
                    }
                    Text {
                        visible: catalogList.expanded
                        Layout.fillWidth: true
                        wrapMode: Text.WordWrap
                        text: "Точность — сколько слов из 100 модель пишет верно на русской речи, скорость — насколько быстро она работает на обычном процессоре (замеры onnx-asr). У моделей с пунктуацией точность чуть ниже из-за того, как её считают, а читать их текст удобнее. Выбранная модель скачается при первой записи, если не скачать её заранее."
                        font.pixelSize: 12
                        lineHeight: 1.2
                        color: Theme.textTertiary
                    }
                }

                // ----- своя модель: с Hugging Face или из папки на компьютере -----
                ColumnLayout {
                    id: customPanel
                    Layout.fillWidth: true
                    visible: win.customOpen
                    spacing: 8
                    property string kind: "hf"
                    Text {
                        Layout.fillWidth: true
                        wrapMode: Text.WordWrap
                        text: "Говорилка понимает модели в формате ONNX: NeMo (FastConformer, Parakeet, Canary), Vosk/Kaldi, T-One, Whisper и GigaAM. Перед выбором модель проверяется: загружается и прогоняется через неё тишина."
                        font.pixelSize: 12
                        lineHeight: 1.2
                        color: Theme.textTertiary
                    }
                    Flow {
                        Layout.fillWidth: true
                        spacing: 7
                        ChipToggle { text: "С Hugging Face"; iconName: "open"; checked: customPanel.kind === "hf"; onClicked: customPanel.kind = "hf"; tip: "Скачать модель по адресу вида автор/репозиторий. В репозитории должен быть config.json с model_type" }
                        ChipToggle { text: "Папка на компьютере"; iconName: "folder"; checked: customPanel.kind === "dir"; onClicked: customPanel.kind = "dir"; tip: "Использовать готовые файлы модели из папки: ничего не копируется и не скачивается" }
                    }
                    FieldBox {
                        id: customField
                        trailingButton: customPanel.kind === "dir"
                        placeholder: customPanel.kind === "hf" ? "автор/репозиторий, например istupakov/gigaam-v3-onnx" : "Папка с файлами модели"
                        onBrowse: { folderDialog.target = customField; folderDialog.open() }
                    }
                    ComboField {
                        id: customType
                        visible: customPanel.kind === "dir"
                        model: backend.customTypes
                        textRole: "label"
                    }
                    Text {
                        visible: customPanel.kind === "dir"
                        Layout.fillWidth: true
                        wrapMode: Text.WordWrap
                        text: "Нужные файлы: " + backend.customTypes[customType.currentIndex].files
                        font.pixelSize: 12
                        color: Theme.textTertiary
                    }
                    ToggleRow {
                        visible: customPanel.kind === "hf"
                        title: "Облегчённая версия (int8)"
                        hint: "Файлы в 3–4 раза меньше. Если в репозитории её нет, проверка скажет об этом"
                        checked: customInt8.value
                        onToggled: (v) => customInt8.value = v
                        QtObject { id: customInt8; property bool value: false }
                    }
                    RowLayout {
                        spacing: 10
                        PillButton {
                            text: win.customChecking ? "Проверяю…" : "Проверить и выбрать"
                            iconName: "check"
                            variant: "primary"
                            enabled: !win.customChecking && customField.text.trim() !== ""
                            tip: "Скачать (если нужно), загрузить модель и проверить, что она работает"
                            onClicked: {
                                win.customChecking = true
                                win.customMessage = ""
                                backend.checkCustomModel(customPanel.kind, customField.text,
                                                         backend.customTypes[customType.currentIndex].id, customInt8.value)
                            }
                        }
                        LoadRing { visible: win.customChecking; fraction: backend.loadFraction; size: 22 }
                        Text {
                            visible: win.customChecking && backend.loading
                            text: backend.loadFraction >= 0 ? Math.round(backend.loadFraction * 100) + "%" : "загружаю…"
                            font.pixelSize: 12
                            color: Theme.textSecondary
                        }
                    }
                    Text {
                        visible: win.customMessage !== ""
                        Layout.fillWidth: true
                        wrapMode: Text.WordWrap
                        text: win.customMessage
                        font.pixelSize: 13
                        lineHeight: 1.2
                        color: win.customOk ? Theme.success : Theme.danger
                    }
                }
                Label { text: "Освобождать память, когда запись не идёт" }
                ComboField {
                    id: unloadBox
                    model: ["Через 5 минут", "Через 10 минут", "Через 30 минут", "Никогда"]
                    onActivated: win.setValue("unload_after_min", [5, 10, 30, 0][currentIndex])
                }
                Text {
                    Layout.fillWidth: true
                    wrapMode: Text.WordWrap
                    text: "Модели занимают около гигабайта. Если не записывать, Говорилка выгрузит их и будет занимать около 200 МБ. Следующая запись начнётся на пару секунд позже."
                    font.pixelSize: 12
                    lineHeight: 1.2
                    color: Theme.textTertiary
                }
                Label { text: "Куда сохранять транскрипты" }
                FieldBox { id: folderField; trailingButton: true; onBrowse: { folderDialog.target = folderField; folderDialog.open() } }
                ToggleRow { title: "Микрофон"; hint: "Ваш голос — «Я»"; checked: win.draft.capture_mic === true; onToggled: (v) => win.setValue("capture_mic", v) }
                ToggleRow { title: "Звук компьютера"; hint: "Собеседники из Zoom, Teams, Телемоста, браузера"; checked: win.draft.capture_system === true; onToggled: (v) => win.setValue("capture_system", v) }
                ToggleRow { title: "Убирать эхо колонок"; hint: "Эхоподавление: без наушников слова собеседника не попадут в ваш микрофон"; checked: win.draft.echo_filter === true; onToggled: (v) => win.setValue("echo_filter", v) }
                ToggleRow { title: "Различать голоса"; hint: "«Собеседник 1», «Собеседник 2»… у каждого свой цвет. После созвона подписи уточняются по всем фразам сразу. Нажмите на имя в ленте, чтобы подписать человека."; checked: win.draft.split_speakers === true; onToggled: (v) => win.setValue("split_speakers", v) }
                ToggleRow {
                    title: "Запоминать голоса знакомых"
                    hint: "Подписав человека именем, вы больше не будете делать это снова: Говорилка узнает его в следующих созвонах. Хранится только числовой отпечаток голоса (не запись), локально на этом компьютере, никуда не отправляется."
                    checked: win.draft.remember_voices === true
                    onToggled: (v) => win.setValue("remember_voices", v)
                }
                RowLayout {
                    Layout.fillWidth: true
                    visible: backend.knownVoices > 0
                    Text { text: "В памяти голосов: " + backend.knownVoices; font.pixelSize: 13; color: Theme.textSecondary; Layout.fillWidth: true }
                    PillButton { text: "Забыть всех"; tip: "Стереть все запомненные голоса с этого компьютера"; variant: "danger"; implicitHeight: 36; onClicked: backend.forgetVoices() }
                }
            }

            Section {
                heading: "Запуск и вид"
                ToggleRow { title: "Запускать при входе в систему"; hint: "Говорилка стартует свёрнутой в трей и ждёт горячую клавишу"; checked: win.autostart; onToggled: (v) => win.autostart = v }
                ToggleRow { title: "Плашка «● Запись» поверх окон"; hint: "Видно, что запись идёт, даже когда окно скрыто"; checked: win.draft.show_indicator === true; onToggled: (v) => win.setValue("show_indicator", v) }
                ToggleRow { title: "Меньше движения"; hint: "Вместо подъёма и пружин — короткое растворение"; checked: win.draft.reduce_motion === true; onToggled: (v) => win.setValue("reduce_motion", v) }
                Label { text: "Тема" }
                ComboField {
                    id: themeBox
                    model: ["Как в системе", "Тёмная", "Светлая"]
                    onActivated: win.setValue("theme", ["system", "dark", "light"][currentIndex])
                }
            }

            Section {
                visible: backend.accessAvailable
                heading: "Разрешения macOS"
                AccessRows { Layout.fillWidth: true }
            }

            Section {
                id: assistantSection
                heading: "Подсказки в звонке"
                readonly property var preset: backend.apiPresets[apiBox.currentIndex] || backend.apiPresets[0]
                readonly property bool api: win.draft.assistant_source === "api"
                ToggleRow {
                    title: "Подсказывать ответы на вопросы собеседника"
                    hint: "Во время записи нейросеть слышит вопросы собеседника и сразу предлагает ответ в отдельном окне поверх всех. Включить и выключить можно и кнопкой «Подсказки» в пульте записи."
                    checked: win.draft.assistant_enabled === true
                    onToggled: (v) => win.setValue("assistant_enabled", v)
                }
                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 10
                    opacity: win.draft.assistant_enabled ? 1 : 0.5
                    Label { text: "Кто отвечает" }
                    Flow {
                        Layout.fillWidth: true
                        spacing: 7
                        ChipToggle { text: "Модель на компьютере"; iconName: "power"; checked: !assistantSection.api; onClicked: win.setValue("assistant_source", "local"); tip: "Разговор не покидает компьютер. Нужна память и время на ответ" }
                        ChipToggle { text: "Через API"; iconName: "open"; checked: assistantSection.api; onClicked: win.setValue("assistant_source", "api"); tip: "OpenAI, DeepSeek, OpenRouter, Claude или свой адрес по вашему ключу: быстрее и умнее" }
                    }
                    Text {
                        visible: !assistantSection.api
                        Layout.fillWidth: true
                        wrapMode: Text.WordWrap
                        text: "Отвечает " + backend.summaryModel.title + " — та же модель, что пишет итоги (выбирается в разделе «Итоги»). Она работает вместе с распознаванием, поэтому памяти нужно примерно на 1,5 ГБ больше. Ответ — через 3–10 секунд, на слабом компьютере дольше."
                        font.pixelSize: 12
                        lineHeight: 1.2
                        color: Theme.textTertiary
                    }
                    ColumnLayout {
                        visible: assistantSection.api
                        Layout.fillWidth: true
                        spacing: 8
                        ComboField {
                            id: apiBox
                            model: backend.apiPresets
                            textRole: "label"
                            onActivated: win.setValue("assistant_api_preset", backend.apiPresets[currentIndex].id)
                        }
                        Text { Layout.fillWidth: true; wrapMode: Text.WordWrap; text: assistantSection.preset.hint; font.pixelSize: 12; color: Theme.textTertiary }
                        FieldBox { id: apiUrlField; visible: assistantSection.preset.id === "custom"; placeholder: "https://адрес-сервиса/v1" }
                        FieldBox { id: apiModelField; placeholder: "Модель: " + (assistantSection.preset.model || "название модели") + " (пусто — по умолчанию)" }
                        RowLayout {
                            Layout.fillWidth: true
                            spacing: 8
                            Rectangle {
                                Layout.fillWidth: true
                                implicitHeight: 42
                                radius: 14
                                color: Theme.core
                                border.color: apiKeyField.activeFocus ? Qt.rgba(Theme.accent.r, Theme.accent.g, Theme.accent.b, 0.6) : Theme.shellLine
                                TextField {
                                    id: apiKeyField
                                    anchors.fill: parent
                                    leftPadding: 14
                                    echoMode: backend.liveFlags.indexOf("nopassword") >= 0 ? TextInput.Normal : TextInput.Password
                                    color: Theme.text
                                    placeholderText: backend.assistantKeySaved ? "Ключ сохранён. Введите новый, чтобы заменить" : "Ключ API"
                                    placeholderTextColor: Theme.textTertiary
                                    font.pixelSize: 14
                                    background: Item {}
                                }
                            }
                            PillButton {
                                text: "Сохранить ключ"
                                enabled: apiKeyField.text.trim() !== ""
                                tip: "Ключ хранится в «Диспетчере учётных данных» Windows или «Связке ключей» Mac, не в файле настроек"
                                onClicked: { backend.setAssistantKey(apiKeyField.text); apiKeyField.text = "" }
                            }
                        }
                        RowLayout {
                            spacing: 8
                            PillButton {
                                text: "Проверить подключение"
                                enabled: backend.assistantKeySaved
                                tip: "Сохраните настройки, потом проверьте: короткий запрос к провайдеру"
                                onClicked: backend.checkAssistantApi()
                            }
                            PillButton { visible: backend.assistantKeySaved; text: "Удалить ключ"; onClicked: backend.setAssistantKey("") }
                        }
                        Text {
                            Layout.fillWidth: true
                            wrapMode: Text.WordWrap
                            text: "Вопрос собеседника и последние реплики разговора уходят провайдеру. Сам звук и файл транскрипта не уходят."
                            font.pixelSize: 12
                            color: Theme.pause
                        }
                    }
                    Label { text: "Что нейросети знать о вас и теме" }
                    Rectangle {
                        Layout.fillWidth: true
                        implicitHeight: 100
                        radius: 14
                        color: Theme.core
                        border.color: profileField.activeFocus ? Qt.rgba(Theme.accent.r, Theme.accent.g, Theme.accent.b, 0.6) : Theme.shellLine
                        ScrollView {
                            anchors.fill: parent
                            anchors.margins: 4
                            TextArea {
                                id: profileField
                                wrapMode: TextEdit.Wrap
                                font.pixelSize: 13
                                color: Theme.text
                                selectByMouse: true
                                placeholderText: "Например: тимлид тестирования в банке, 6 лет опыта"
                                placeholderTextColor: Theme.textTertiary
                                leftPadding: 10; rightPadding: 10; topPadding: 8
                                background: Item {}
                            }
                        }
                    }
                    ToggleRow {
                        title: "Отвечать сразу на вопросы"
                        hint: "Выключите, чтобы подсказка появлялась только по кнопке «Ответить» в окне подсказок"
                        checked: win.draft.assistant_auto === true
                        onToggled: (v) => win.setValue("assistant_auto", v)
                    }
                    ToggleRow {
                        title: "Не показывать окно при демонстрации экрана"
                        hint: Qt.platform.os === "osx"
                              ? "На мониторе окно видно вам, а в трансляции его нет. Начиная с macOS 15 часть программ для звонков всё же может его показать: проверьте на своей, начав демонстрацию."
                              : "На мониторе окно видно вам, а в Zoom, Teams, Телемосте и записи экрана его нет (Windows 10 версии 2004 и новее). Фото экрана телефоном это не остановит."
                        checked: win.draft.assistant_hide === true
                        onToggled: (v) => win.setValue("assistant_hide", v)
                    }
                }
            }

            Section {
                id: summarySection
                heading: "Итоги"
                Text {
                    Layout.fillWidth: true
                    wrapMode: Text.WordWrap
                    text: "Модель, которая пишет итоги созвона на вкладке «Итоги». Она работает на этом компьютере без интернета, скачивается один раз. "
                          + (backend.ramGb > 0 ? "Памяти у компьютера: " + backend.ramGb + " ГБ." : "")
                    font.pixelSize: 12
                    lineHeight: 1.2
                    color: Theme.textTertiary
                }
                Repeater {
                    model: backend.llmCatalog
                    ModelCard {
                        required property var modelData
                        info: modelData
                        selected: win.draft.summary_model === modelData.key
                        downloading: backend.llmDownloading === modelData.key
                        fraction: backend.llmDownloadFraction
                        busy: backend.llmDownloading !== ""
                        onChoose: win.setValue("summary_model", modelData.key)
                        onDownload: backend.downloadLlm(modelData.key)
                        onRemove: backend.deleteLlm(modelData.key)
                    }
                }
            }

            Section {
                id: updatesSection
                heading: "Обновления"
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 12
                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 2
                        Text {
                            text: "Говорилка " + (backend.appVersion === "dev" ? (backend.isTrialBuild ? "(пробная сборка: обновления для неё отключены)" : "(запуск из исходников)") : backend.appVersion)
                            font.pixelSize: Theme.fontBody
                            font.weight: Font.DemiBold
                            color: Theme.text
                        }
                        Text {
                            Layout.fillWidth: true
                            wrapMode: Text.WordWrap
                            visible: text !== ""
                            text: backend.updateStatus === "available"
                                  ? "Доступна версия " + backend.updateInfo.version + (backend.canInstallUpdate ? ". Нажмите «Установить»: Говорилка сама обновится и перезапустится." : ". Скачайте файл, закройте Говорилку и запустите его.")
                                  : backend.updateMessage
                            font.pixelSize: 12
                            color: backend.updateStatus === "error" ? Theme.danger : backend.updateStatus === "latest" ? Theme.success : Theme.textSecondary
                        }
                    }
                    PillButton {
                        text: backend.updateStatus === "checking" ? "Проверяю…" : "Проверить обновление"
                        iconName: "bolt"
                        enabled: backend.updateStatus !== "checking"
                        tip: "Спросить у GitHub, вышла ли новая версия"
                        onClicked: backend.checkForUpdates()
                    }
                }
                Text {
                    visible: backend.installHint !== "" && backend.updateStatus === "available"
                    Layout.fillWidth: true
                    wrapMode: Text.WordWrap
                    text: backend.installHint
                    font.pixelSize: 12
                    color: Theme.pause
                }
                Text {
                    visible: backend.installError !== ""
                    Layout.fillWidth: true
                    wrapMode: Text.WordWrap
                    text: backend.installError
                    font.pixelSize: 12
                    color: Theme.danger
                }
                RowLayout {
                    visible: backend.updateStatus === "available"
                    spacing: 8
                    PillButton {
                        text: backend.installing ? "Устанавливаю…" : backend.canInstallUpdate ? "Установить" : "Скачать"
                        variant: "primary"
                        iconName: "open"
                        enabled: !backend.installing
                        tip: backend.canInstallUpdate ? "Скачать, проверить и установить новую версию; Говорилка перезапустится сама" : "Скачать новую версию"
                        onClicked: backend.installUpdate()
                    }
                    PillButton { text: "Что нового"; tip: "Страница релиза на GitHub"; onClicked: backend.openReleasePage() }
                    PillButton { text: "Пропустить эту версию"; tip: "Больше не напоминать об этой версии"; onClicked: backend.skipUpdate() }
                }
                ToggleRow {
                    title: "Проверять обновления сами"
                    hint: "Раз в сутки Говорилка анонимно спрашивает у github.com, не вышла ли новая версия. Ничего о вас не отправляется, кроме обычного адреса компьютера"
                    checked: win.draft.check_updates === true
                    onToggled: (v) => win.setValue("check_updates", v)
                }
            }

            Section {
                heading: "Нейросеть"
                Label { text: "Куда отправлять разговор" }
                ComboField {
                    id: aiBox
                    model: backend.aiChoices
                    textRole: "label"
                    onActivated: win.setValue("ai_target", backend.aiChoices[currentIndex].id)
                    function find(id) { for (let i = 0; i < backend.aiChoices.length; ++i) if (backend.aiChoices[i].id === id) return i; return 0 }
                }
                Label { visible: win.hasDesktopApp; text: "Где открывать ChatGPT, Claude и Claude Code" }
                ComboField {
                    id: openBox
                    visible: win.hasDesktopApp
                    model: backend.aiOpenChoices
                    textRole: "label"
                    onActivated: win.setValue("ai_open_in", backend.aiOpenChoices[currentIndex].id)
                }
                Text {
                    Layout.fillWidth: true
                    wrapMode: Text.WordWrap
                    text: "Говорилка открывает чат и сама вставляет в него разговор, даже если вы ещё не вошли в аккаунт: вставка случится после входа. Проверьте текст и нажмите Enter. Claude Code читает файл сам."
                    font.pixelSize: 12
                    lineHeight: 1.2
                    color: Theme.textTertiary
                }
                Label { visible: win.claudeCode; text: "Где запускать Claude Code (пусто — в папке транскриптов)" }
                FieldBox { id: claudeField; visible: win.claudeCode; trailingButton: true; onBrowse: { folderDialog.target = claudeField; folderDialog.open() } }
                Label { text: "Что попросить ({file} — транскрипт)" }
                Rectangle {
                    Layout.fillWidth: true
                    implicitHeight: 128
                    radius: 14
                    color: Theme.core
                    border.color: promptField.activeFocus ? Qt.rgba(Theme.accent.r, Theme.accent.g, Theme.accent.b, 0.6) : Theme.shellLine
                    ScrollView {
                        anchors.fill: parent
                        anchors.margins: 4
                        TextArea {
                            id: promptField
                            wrapMode: TextEdit.Wrap
                            font.pixelSize: 13
                            color: Theme.text
                            selectByMouse: true
                            leftPadding: 10; rightPadding: 10; topPadding: 8
                            background: Item {}
                        }
                    }
                }
            }
            Item { Layout.preferredHeight: 20 }
        }
    }

    // нижняя панель с кнопками, стеклом поверх прокрутки
    Rectangle {
        anchors { left: parent.left; right: parent.right; bottom: parent.bottom }
        height: 76
        color: Theme.glass
        Rectangle { anchors { left: parent.left; right: parent.right; top: parent.top } height: 1; color: Theme.hairline }
        RowLayout {
            anchors { fill: parent; leftMargin: 24; rightMargin: 24 }
            spacing: 10
            PillButton { text: "Как пользоваться"; tip: "Все возможности Говорилки: что умеет и что появилось нового"; iconName: "bolt"; onClicked: { win.close(); backend.showFeatures("all") } }
            Item { Layout.fillWidth: true }
            PillButton { text: "Отмена"; tip: "Закрыть без изменений (Esc)"; onClicked: win.close() }
            PillButton {
                variant: "primary"
                text: "Сохранить"
                tip: "Применить все настройки сразу"
                iconName: "check"
                onClicked: {
                    win.draft.transcripts_dir = folderField.text
                    win.draft.claude_workdir = claudeField.text
                    win.draft.claude_prompt = promptField.text
                    win.draft.assistant_profile = profileField.text
                    win.draft.assistant_api_url = apiUrlField.text
                    win.draft.assistant_api_model = apiModelField.text
                    // только изменённое: тумблеры главного окна и знакомство могли поменять настройки,
                    // пока это окно было открыто, и старая копия не должна их откатить
                    const changed = {}
                    for (const key in win.draft)
                        if (JSON.stringify(win.draft[key]) !== JSON.stringify(win.original[key])) changed[key] = win.draft[key]
                    backend.applySettings(changed, win.autostart)
                    win.close()
                }
            }
        }
    }
    // Esc в открытом списке закрывает только список, а не всё окно
    Shortcut {
        sequences: ["Escape"]
        enabled: !actionBox.popup.visible && !apiBox.popup.visible && !themeBox.popup.visible && !aiBox.popup.visible && !unloadBox.popup.visible && !customType.popup.visible
        onActivated: win.close()
    }
}
