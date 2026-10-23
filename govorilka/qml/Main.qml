import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Dialogs
import QtQuick.Window

// Главное окно Говорилки.
// Слева — пульт записи: большая кнопка, таймер, уровень звука, источники, отправка в Claude Code.
// Справа — лента транскрипта. Сверху — плавающая шапка с темой созвона.
ApplicationWindow {
    id: window
    width: 1120
    height: 740
    minimumWidth: 900
    minimumHeight: 660
    visible: true
    title: "Говорилка"
    color: Theme.bg

    readonly property string phase: backend.state
    readonly property bool live: phase === "recording" || phase === "paused"
    readonly property bool idle: phase === "idle"
    readonly property var opts: backend.settings

    Binding { target: Theme; property: "mode"; value: window.opts.theme }
    Binding { target: Theme; property: "reduceMotion"; value: window.opts.reduce_motion }

    function stateLabel() {
        return { idle: "Готов", starting: "Запуск…", recording: "Идёт запись", paused: "Пауза",
                 stopping: "Сохраняю…", file: "Расшифровка" }[phase] || ""
    }
    function stateTint() {
        return phase === "recording" ? Theme.record : phase === "paused" ? Theme.pause
             : phase === "file" ? Theme.accent : Theme.textSecondary
    }
    function showWindow() { window.show(); window.raise(); window.requestActivate() }

    onClosing: (close) => {
        if (backend.trayAvailable) {   // Говорилка уходит в трей и продолжает слушать горячую клавишу
            close.accepted = false
            window.hide()
            backend.noteHiddenToTray()
        } else {
            // трея нет (например, GNOME без индикаторов): окно некуда прятать, иначе программа осталась бы
            // невидимой, но живой. Закрываем по-настоящему и выходим.
            backend.quit()
        }
    }

    Backdrop { anchors.fill: parent }

    // ---------- шапка-остров ----------
    Rectangle {
        id: topBar
        anchors { top: parent.top; left: parent.left; right: parent.right; margins: 16 }
        height: 60
        radius: 30
        color: Theme.shell
        border.color: Theme.shellLine

        RowLayout {
            anchors.fill: parent
            anchors.leftMargin: 14
            anchors.rightMargin: 10
            spacing: 12

            Rectangle {   // знак приложения
                Layout.preferredWidth: 34; Layout.preferredHeight: 34
                radius: 11
                gradient: Gradient {
                    orientation: Gradient.Horizontal
                    GradientStop { position: 0; color: Theme.accentA }
                    GradientStop { position: 1; color: Theme.accentB }
                }
                Icon { anchors.centerIn: parent; name: "wave"; size: 20; color: "#FFFFFF" }
            }
            Text {
                text: "Говорилка"
                font.pixelSize: 16
                font.weight: Font.Bold
                font.letterSpacing: -0.3
                color: Theme.text
            }
            Eyebrow {
                text: window.stateLabel()
                tint: window.stateTint()
                dot: window.phase === "recording"
            }

            Rectangle {   // тема созвона
                Layout.fillWidth: true
                Layout.leftMargin: 10
                Layout.preferredHeight: 40
                radius: 20
                color: titleField.activeFocus ? Theme.core : "transparent"
                border.color: titleField.activeFocus ? Qt.rgba(Theme.accent.r, Theme.accent.g, Theme.accent.b, 0.5) : "transparent"
                Behavior on color { ColorAnimation { duration: Theme.base } }
                Icon { x: 14; anchors.verticalCenter: parent.verticalCenter; name: "edit"; size: 15; color: Theme.textTertiary }
                TextField {
                    id: titleField
                    anchors.fill: parent
                    leftPadding: 38
                    rightPadding: 14
                    enabled: window.idle
                    placeholderText: window.live ? (backend.transcriptTitle || "Без темы") : "Тема созвона — попадёт в имя файла"
                    placeholderTextColor: Theme.textTertiary
                    color: Theme.text
                    font.pixelSize: Theme.fontBody
                    background: Item {}
                    selectByMouse: true
                    Accessible.name: "Тема созвона"
                    onAccepted: if (window.idle) backend.toggleRecording(text)
                }
            }

            IconButton { iconName: "open"; tip: "Расшифровать аудио или видео"; onClicked: openDialog.open(); enabled: window.idle }
            IconButton { iconName: "folder"; tip: "Папка с транскриптами"; onClicked: backend.openFolder() }
            IconButton { iconName: "settings"; tip: "Настройки"; onClicked: settingsWindow.open() }
        }
    }

    // ---------- тело ----------
    RowLayout {
        anchors { top: topBar.bottom; left: parent.left; right: parent.right; bottom: parent.bottom; margins: 16; topMargin: 14 }
        spacing: Theme.gap

        // ===== пульт записи =====
        Bezel {
            Layout.preferredWidth: 332
            Layout.fillHeight: true

            ColumnLayout {
                anchors.fill: parent
                anchors.margins: 20
                spacing: 0

                RecordButton {
                    id: recordButton
                    diameter: 100
                    Layout.alignment: Qt.AlignHCenter
                    Layout.topMargin: 4
                    phase: window.phase
                    level: Math.max(backend.micLevel, backend.systemLevel)
                    enabled: window.idle || window.live
                    onClicked: backend.toggleRecording(titleField.text)
                }

                Text {
                    Layout.alignment: Qt.AlignHCenter
                    text: backend.elapsed
                    font.pixelSize: 34
                    font.weight: Font.DemiBold
                    font.letterSpacing: -1
                    font.features: { "tnum": 1 }
                    color: window.live ? Theme.text : Theme.textTertiary
                    Behavior on color { ColorAnimation { duration: Theme.base } }
                }
                Text {
                    Layout.alignment: Qt.AlignHCenter
                    Layout.topMargin: 2
                    text: window.live ? (window.phase === "paused" ? "Пауза — звук не пишется" : "Говорите — текст появится справа")
                                      : window.phase === "file" ? "Расшифровываю файл…" : "Нажмите, чтобы начать"
                    font.pixelSize: 13
                    color: Theme.textSecondary
                }

                RowLayout {   // пауза и отметка появляются вместе с записью
                    Layout.fillWidth: true
                    Layout.topMargin: 14
                    spacing: 8
                    opacity: window.live ? 1 : 0.35
                    enabled: window.live
                    Behavior on opacity { NumberAnimation { duration: Theme.base } }
                    PillButton {
                        Layout.fillWidth: true
                        text: window.phase === "paused" ? "Продолжить" : "Пауза"
                        iconName: window.phase === "paused" ? "play" : "pause"
                        onClicked: backend.togglePause()
                    }
                    PillButton {
                        Layout.fillWidth: true
                        text: "Отметка"
                        iconName: "star"
                        onClicked: backend.addMark()
                    }
                }

                Rectangle { Layout.fillWidth: true; Layout.topMargin: 16; Layout.bottomMargin: 12; height: 1; color: Theme.hairline }

                RowLayout {
                    Layout.fillWidth: true
                    spacing: 18
                    LevelBars { label: "Я"; iconName: "mic"; tint: Theme.me; level: backend.micLevel; Layout.fillWidth: true }
                    LevelBars { label: "Собеседники"; iconName: "speaker"; tint: Theme.others[0]; level: backend.systemLevel; Layout.fillWidth: true }
                }

                Flow {
                    Layout.fillWidth: true
                    Layout.topMargin: 12
                    spacing: 7
                    enabled: window.idle
                    opacity: enabled ? 1 : 0.55
                    ChipToggle { text: "Микрофон"; iconName: "mic"; checked: window.opts.capture_mic; onToggled: backend.setOption("capture_mic", checked) }
                    ChipToggle { text: "Звук компьютера"; iconName: "speaker"; checked: window.opts.capture_system; onToggled: backend.setOption("capture_system", checked) }
                    ChipToggle { text: "Без эха"; iconName: "echo"; checked: window.opts.echo_filter; onToggled: backend.setOption("echo_filter", checked) }
                    ChipToggle { text: "Голоса"; iconName: "people"; checked: window.opts.split_speakers; onToggled: backend.setOption("split_speakers", checked) }
                }

                Item { Layout.fillHeight: true }

                Text {
                    Layout.fillWidth: true
                    Layout.bottomMargin: 8
                    Layout.topMargin: 6
                    text: backend.statusText
                    wrapMode: Text.NoWrap
                    elide: Text.ElideRight
                    font.pixelSize: 12
                    color: Theme.textTertiary
                    HoverHandler { id: statusHover }
                    ToolTip.visible: statusHover.hovered && truncated
                    ToolTip.text: backend.statusText
                    ToolTip.delay: 300
                }

                PillButton {
                    Layout.fillWidth: true
                    variant: "primary"
                    text: "Отправить в Claude Code"
                    iconName: "sparkle"
                    onClicked: backend.sendToClaude()
                }

                RowLayout {
                    Layout.alignment: Qt.AlignHCenter
                    Layout.topMargin: 10
                    spacing: 5
                    visible: window.opts.hotkey_enabled
                    Repeater {
                        model: String(window.opts.hotkeyLabel).split(" + ")
                        KeyCap { required property string modelData; text: modelData }
                    }
                    Text {
                        text: window.opts.hotkey_action === "record" ? "запись" : "меню"
                        font.pixelSize: 12
                        color: Theme.textTertiary
                        Layout.leftMargin: 3
                    }
                }
            }
        }

        // ===== транскрипт =====
        Bezel {
            Layout.fillWidth: true
            Layout.fillHeight: true

            ColumnLayout {
                anchors.fill: parent
                spacing: 0

                RowLayout {
                    Layout.fillWidth: true
                    Layout.leftMargin: 22
                    Layout.rightMargin: 12
                    Layout.topMargin: 14
                    Layout.bottomMargin: 10
                    spacing: 6
                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 1
                        Text {
                            text: "Транскрипт"
                            font.pixelSize: Theme.fontTitle + 1
                            font.weight: Font.DemiBold
                            font.letterSpacing: -0.2
                            color: Theme.text
                        }
                        Text {
                            Layout.fillWidth: true
                            text: backend.transcriptPath ? backend.transcriptPath.split(/[\\/]/).pop() : "Пока пусто"
                            elide: Text.ElideMiddle
                            font.pixelSize: Theme.fontMeta
                            color: Theme.textTertiary
                        }
                    }
                    IconButton { iconName: "copy"; tip: "Скопировать текст"; onClicked: backend.copyTranscript() }
                    IconButton { iconName: "save"; tip: "Сохранить как…"; onClicked: saveDialog.open() }
                    IconButton { iconName: "file"; tip: "Открыть файл транскрипта"; onClicked: backend.openTranscript() }
                }

                // полоса расшифровки файла
                Item {
                    Layout.fillWidth: true
                    Layout.preferredHeight: window.phase === "file" ? 46 : 0
                    clip: true
                    Behavior on Layout.preferredHeight { NumberAnimation { duration: Theme.base; easing.type: Easing.BezierSpline; easing.bezierCurve: Theme.easeOut } }
                    RowLayout {
                        anchors.fill: parent
                        anchors.leftMargin: 22
                        anchors.rightMargin: 14
                        spacing: 12
                        Text {
                            text: backend.fileName ? backend.fileName : "Готовлю файл…"
                            elide: Text.ElideMiddle
                            Layout.maximumWidth: 260
                            font.pixelSize: 13
                            color: Theme.textSecondary
                        }
                        Rectangle {
                            Layout.fillWidth: true
                            height: 6; radius: 3
                            color: Theme.hairline
                            Accessible.role: Accessible.ProgressBar
                            Accessible.name: "Расшифровка файла"
                            Accessible.description: Math.round(backend.fileProgress * 100) + " процентов"
                            Rectangle {
                                height: parent.height; radius: 3
                                width: parent.width * backend.fileProgress
                                gradient: Gradient {
                                    orientation: Gradient.Horizontal
                                    GradientStop { position: 0; color: Theme.accentA }
                                    GradientStop { position: 1; color: Theme.accentB }
                                }
                                Behavior on width { NumberAnimation { duration: 300; easing.type: Easing.OutCubic } }
                            }
                        }
                        Text {
                            text: Math.round(backend.fileProgress * 100) + "%"
                            font.pixelSize: 13
                            font.features: { "tnum": 1 }
                            color: Theme.textSecondary
                        }
                        IconButton { iconName: "close"; size: 30; tip: "Отменить"; onClicked: backend.cancelFile() }
                    }
                }

                Rectangle { Layout.fillWidth: true; height: 1; color: Theme.hairline }

                TranscriptView {
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    model: backend.transcript
                    hotkeyLabel: window.opts.hotkey_enabled ? String(window.opts.hotkeyLabel) : ""
                    recording: window.live
                    onRenameRequested: (voiceId, name, tint) => renameSheet.ask(voiceId, name, tint)
                }
            }
        }
    }

    Toasts {
        id: toasts
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.bottom: parent.bottom
        anchors.bottomMargin: 30
    }

    // ---------- диалоги ----------
    FileDialog {
        id: openDialog
        title: "Аудио или видео для расшифровки"
        nameFilters: ["Аудио и видео (*.mp3 *.wav *.m4a *.ogg *.opus *.flac *.aac *.wma *.mp4 *.mkv *.mov *.webm *.avi *.wmv)", "Все файлы (*)"]
        onAccepted: backend.openFile(selectedFile)
    }
    FileDialog {
        id: saveDialog
        title: "Сохранить транскрипт"
        fileMode: FileDialog.SaveFile
        defaultSuffix: "md"
        nameFilters: ["Markdown (*.md)", "Текст (*.txt)"]
        onAccepted: backend.saveAs(selectedFile)
    }

    PermissionSheet { id: permissionSheet }
    RenameSheet { id: renameSheet }

    SettingsWindow { id: settingsWindow }
    RadialMenu { id: radial }
    RecordingPill { mainHidden: !window.visible || window.visibility === Window.Minimized || !window.active }

    Connections {
        target: backend
        function onToast(message, kind) { toasts.show(message, kind) }
        function onPermissionRequested(message, service, canReset) { permissionSheet.ask(message, service, canReset) }
        function onRadialRequested(x, y) { radial.popupAt(x, y) }
        function onHotkeyReleased() { radial.releaseHotkey() }
        function onWindowRequested() { window.showWindow() }
        function onSettingsRequested() { window.showWindow(); settingsWindow.open() }
    }

    Shortcut { sequences: ["Ctrl+,"]; onActivated: settingsWindow.open() }
    Shortcut { sequences: ["Ctrl+M"]; onActivated: backend.addMark() }
}
