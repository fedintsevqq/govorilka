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
    readonly property var modelItems: {
        const items = backend.models.slice(0, -1)
        if (customKey !== "") items.push({ id: customKey, label: backend.modelLabel(customKey) })
        items.push(backend.models[backend.models.length - 1])
        return items
    }
    function modelIndex(key) {
        for (let i = 0; i < modelItems.length; ++i) if (modelItems[i].id === key) return i
        return 0
    }
    Connections {
        target: backend
        function onCustomModelChecked(ok, message, key) {
            win.customChecking = false
            win.customOk = ok
            win.customMessage = message
            if (ok) {
                win.customKey = key
                win.setValue("model", key)
                modelBox.currentIndex = win.modelIndex(key)
            }
        }
    }   // как было при открытии: сохраняем только то, что поменяли здесь
    property bool autostart: false
    property string hotkeyLabel: ""
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
        promptField.text = draft.claude_prompt
        folderField.text = draft.transcripts_dir
        claudeField.text = draft.claude_workdir
        customKey = backend.isCustomModel(draft.model) ? draft.model : ""
        customOpen = false
        customChecking = false
        customMessage = ""
        modelBox.currentIndex = modelIndex(draft.model)
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
                        border.color: backend.capturing ? Qt.rgba(Theme.accent.r, Theme.accent.g, Theme.accent.b, 0.7) : Theme.shellLine
                        Row {
                            anchors.verticalCenter: parent.verticalCenter
                            x: 14
                            spacing: 6
                            visible: !backend.capturing
                            Repeater { model: win.hotkeyLabel.split(" + "); KeyCap { required property string modelData; text: modelData } }
                        }
                        Text { visible: backend.capturing; x: 14; anchors.verticalCenter: parent.verticalCenter; text: "Нажмите комбинацию и отпустите…"; font.pixelSize: 14; color: Theme.accent }
                    }
                    PillButton {
                        text: backend.capturing ? "Отмена" : "Изменить…"
                        tip: backend.capturing ? "Оставить прежнюю комбинацию" : "Задать новую комбинацию: нажмите нужные клавиши и отпустите"
                        implicitHeight: 44
                        onClicked: backend.capturing ? backend.stopCapture() : backend.startCapture()
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
                heading: "Запись"
                Label { text: "Модель распознавания" }
                ComboField {
                    id: modelBox
                    model: win.modelItems
                    textRole: "label"
                    onActivated: {
                        const id = win.modelItems[currentIndex].id
                        if (id === "__custom__") {
                            win.customOpen = true                       // откроем выбор своей модели; пока не проверена, прежняя остаётся
                            currentIndex = win.modelIndex(win.draft.model)
                        } else {
                            win.customOpen = false
                            win.setValue("model", id)
                        }
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
            PillButton { text: "Как пользоваться"; tip: "Снова показать знакомство с тренировкой горячей клавиши"; iconName: "bolt"; onClicked: { win.close(); win.tourRequested() } }
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
        enabled: !actionBox.popup.visible && !modelBox.popup.visible && !themeBox.popup.visible && !aiBox.popup.visible && !unloadBox.popup.visible && !customType.popup.visible
        onActivated: win.close()
    }
}
