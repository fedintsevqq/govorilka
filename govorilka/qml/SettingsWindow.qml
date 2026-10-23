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
    property bool autostart: false
    property string hotkeyLabel: ""
    property string hotkeyWarning: ""

    function open() {
        draft = JSON.parse(JSON.stringify(backend.settings))
        autostart = backend.autostart
        hotkeyLabel = draft.hotkeyLabel
        hotkeyWarning = ""
        promptField.text = draft.claude_prompt
        folderField.text = draft.transcripts_dir
        claudeField.text = draft.claude_workdir
        modelBox.currentIndex = Math.max(0, modelBox.find(draft.model))
        themeBox.currentIndex = ["system", "dark", "light"].indexOf(draft.theme)
        actionBox.currentIndex = draft.hotkey_action === "record" ? 1 : 0
        show()
        raise()
        requestActivate()
        backend.watchKeys(true)
    }
    function setValue(name, value) { draft[name] = value; draftChanged() }
    onClosing: backend.watchKeys(false)
    onVisibleChanged: if (!visible) backend.watchKeys(false)

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
            width: scroll.availableWidth - 32
            x: 16
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
                    model: backend.models
                    textRole: "label"
                    onActivated: win.setValue("model", backend.models[currentIndex].id)
                    function find(id) { for (let i = 0; i < backend.models.length; ++i) if (backend.models[i].id === id) return i; return 0 }
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
                    PillButton { text: "Забыть всех"; variant: "danger"; implicitHeight: 36; onClicked: backend.forgetVoices() }
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
                heading: "Claude Code"
                Label { text: "Где запускать (пусто — в папке транскриптов)" }
                FieldBox { id: claudeField; trailingButton: true; onBrowse: { folderDialog.target = claudeField; folderDialog.open() } }
                Label { text: "Запрос ({file} — путь к транскрипту)" }
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
            Item { Layout.fillWidth: true }
            PillButton { text: "Отмена"; onClicked: win.close() }
            PillButton {
                variant: "primary"
                text: "Сохранить"
                iconName: "check"
                onClicked: {
                    win.draft.transcripts_dir = folderField.text
                    win.draft.claude_workdir = claudeField.text
                    win.draft.claude_prompt = promptField.text
                    backend.applySettings(win.draft, win.autostart)
                    win.close()
                }
            }
        }
    }
    Shortcut { sequences: ["Escape"]; onActivated: win.close() }
}
