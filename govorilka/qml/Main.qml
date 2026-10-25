import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Dialogs
import QtQuick.Window

// Главное окно Говорилки.
// Слева — пульт записи: большая кнопка, таймер, уровень звука, источники, отправка разговора нейросети.
// Справа — лента транскрипта. Сверху — плавающая шапка с темой созвона.
ApplicationWindow {
    id: window
    width: 1120
    height: 740
    minimumWidth: 900
    minimumHeight: 700   // ниже пульт записи не помещается целиком
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
        // готов — зелёный, запись — красный, пауза — янтарный, всё «в процессе» — акцентный
        return phase === "recording" ? Theme.record : phase === "paused" ? Theme.pause
             : phase === "idle" ? Theme.success : Theme.accent
    }
    // «Окно» из кругового меню и трея: вернуть окно, даже если оно свёрнуто или закрыто другими программами.
    // show() свёрнутое окно не разворачивает — поэтому разворачиваем явно, в том же виде, в каком его свернули.
    property bool wasMaximized: false
    onVisibilityChanged: (mode) => { if (mode === Window.Maximized || mode === Window.Windowed) wasMaximized = mode === Window.Maximized }
    function showWindow() {
        backend.setTrayMode(false)   // Mac: вернуть значок в Dock
        if (window.visibility === Window.Minimized || !window.visible)
            window.wasMaximized ? window.showMaximized() : window.showNormal()
        window.raise()
        window.requestActivate()
        backend.bringToFront(window)   // Windows не всегда отдаёт фокус по просьбе: дожимаем системным вызовом
    }

    // вернулись из «Системных настроек» — сразу обновляем зелёные и красные кружки разрешений
    onActiveChanged: if (active && backend.accessAvailable) backend.refreshAccess()

    onClosing: (close) => {
        if (backend.trayAvailable && !backend.quitting) {   // Говорилка уходит в трей и продолжает слушать горячую клавишу
            close.accepted = false
            window.hide()
            backend.setTrayMode(true)   // Mac: без значка в Dock, пока окно спрятано
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
                Layout.minimumWidth: 116   // подпись состояния меняет длину — поле темы рядом не прыгает
                text: window.stateLabel()
                tint: window.stateTint()
                dot: true
                pulse: window.phase === "recording" || window.phase === "starting" || window.phase === "stopping" || window.phase === "file"
            }

            Item {   // кольцо загрузки моделей: появляется, пока они качаются или грузятся в память
                id: loadIndicator
                visible: backend.loading
                Layout.preferredWidth: visible ? loadRow.implicitWidth : 0
                Layout.preferredHeight: 26
                opacity: visible ? 1 : 0
                Behavior on opacity { NumberAnimation { duration: Theme.base } }
                Row {
                    id: loadRow
                    anchors.verticalCenter: parent.verticalCenter
                    spacing: 8
                    LoadRing { fraction: backend.loadFraction; anchors.verticalCenter: parent.verticalCenter }
                    Text {
                        anchors.verticalCenter: parent.verticalCenter
                        text: backend.loadFraction >= 0 ? "Модели " + Math.round(backend.loadFraction * 100) + "%" : "Загружаю модели"
                        font.pixelSize: 12
                        font.features: { "tnum": 1 }
                        color: Theme.textSecondary
                    }
                }
                HoverHandler { id: loadHover }
                Tip {
                    shown: loadHover.hovered
                    text: backend.loadFraction >= 0
                          ? "Первый запуск: скачиваются модели распознавания речи (около 880 МБ). Это один раз, дальше всё работает без интернета"
                          : "Модели загружаются в память: несколько секунд"
                }
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

    // ---------- плашка обновления ----------
    Rectangle {
        id: updateBar
        readonly property bool shown: backend.updateInfo.available === true
        visible: shown
        anchors { top: topBar.bottom; left: parent.left; right: parent.right; leftMargin: 16; rightMargin: 16; topMargin: 10 }
        height: shown ? 46 : 0
        radius: 16
        color: Qt.rgba(Theme.accent.r, Theme.accent.g, Theme.accent.b, Theme.dark ? 0.14 : 0.10)
        border.color: Qt.rgba(Theme.accent.r, Theme.accent.g, Theme.accent.b, 0.4)
        Accessible.role: Accessible.AlertMessage
        Accessible.name: "Доступна версия " + backend.updateInfo.version
        RowLayout {
            anchors { fill: parent; leftMargin: 16; rightMargin: 8 }
            spacing: 8
            Icon { name: "bolt"; size: 17; color: Theme.accent; Layout.alignment: Qt.AlignVCenter }
            Text {
                Layout.fillWidth: true
                text: "Вышла версия " + backend.updateInfo.version + (backend.updateInfo.sizeMb ? " (" + backend.updateInfo.sizeMb + " МБ)" : "")
                elide: Text.ElideRight
                font.pixelSize: 13
                font.weight: Font.DemiBold
                color: Theme.text
            }
            LoadRing { visible: backend.installing; fraction: backend.loadFraction; size: 20; Layout.alignment: Qt.AlignVCenter }
            Text {
                visible: backend.installing
                text: backend.loadFraction >= 0 ? Math.round(backend.loadFraction * 100) + "%" : "проверяю…"
                font.pixelSize: 12
                font.features: { "tnum": 1 }
                color: Theme.textSecondary
            }
            PillButton { text: "Что нового"; implicitHeight: 32; visible: !backend.installing; tip: "Открыть страницу релиза на GitHub"; onClicked: backend.openReleasePage() }
            PillButton {
                text: backend.installing ? "Устанавливаю…" : backend.canInstallUpdate ? "Установить" : "Скачать"
                implicitHeight: 32
                variant: "primary"
                enabled: !backend.installing
                tip: backend.canInstallUpdate ? "Скачать новую версию, проверить и заменить ею программу. Говорилка перезапустится сама"
                                              : "Скачать новую версию: закройте Говорилку и запустите скачанный файл"
                onClicked: backend.installUpdate()
            }
            IconButton { iconName: "close"; size: 30; visible: !backend.installing; tip: "Напомнить позже: плашка скроется до следующего запуска"; onClicked: backend.dismissUpdate() }
        }
    }

    // ---------- тело ----------
    RowLayout {
        anchors { top: updateBar.shown ? updateBar.bottom : topBar.bottom; left: parent.left; right: parent.right; bottom: parent.bottom; margins: 16; topMargin: 14 }
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
                    diameter: window.height < 780 ? 80 : 100   // на невысоком окне пульт целиком, без обрезанного низа
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
                    Layout.fillWidth: true
                    horizontalAlignment: Text.AlignHCenter
                    wrapMode: Text.WordWrap
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
                    // не выключаем, а приглушаем: подсказка при наведении объясняет, что это и когда работает
                    opacity: window.live ? 1 : 0.45
                    Behavior on opacity { NumberAnimation { duration: Theme.base } }
                    PillButton {
                        Layout.fillWidth: true
                        text: window.phase === "paused" ? "Продолжить" : "Пауза"
                        iconName: window.phase === "paused" ? "play" : "pause"
                        tip: window.phase === "paused" ? "Продолжить запись с того же места" : "Поставить запись на паузу: звук не пишется, пока не продолжите"
                        onClicked: backend.togglePause()
                    }
                    PillButton {
                        Layout.fillWidth: true
                        text: "Отметка"
                        iconName: "star"
                        tip: "Отметить важный момент. В тексте и в файле появится звёздочка со временем, чтобы потом быстро найти это место. Горячая клавиша Ctrl+M"
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
                    ChipToggle { text: "Микрофон"; iconName: "mic"; checked: window.opts.capture_mic; onToggled: backend.setOption("capture_mic", checked); tip: "Записывать ваш микрофон — реплики «Я»" }
                    ChipToggle { text: "Звук компьютера"; iconName: "speaker"; checked: window.opts.capture_system; onToggled: backend.setOption("capture_system", checked); tip: "Записывать звук компьютера — голоса собеседников из Zoom, Телемоста, Teams" }
                    ChipToggle { text: "Без эха"; iconName: "echo"; checked: window.opts.echo_filter; onToggled: backend.setOption("echo_filter", checked); tip: "Убирать из микрофона звук колонок, чтобы собеседники не попадали в «Я». В наушниках можно выключить" }
                    ChipToggle { text: "Голоса"; iconName: "people"; checked: window.opts.split_speakers; onToggled: backend.setOption("split_speakers", checked); tip: "Различать собеседников по голосу: «Собеседник 1», «Собеседник 2»…" }
                }

                Item { Layout.fillHeight: true }

                Text {
                    Layout.fillWidth: true
                    Layout.bottomMargin: 8
                    Layout.topMargin: 6
                    text: backend.statusText
                    wrapMode: Text.Wrap          // статус целиком, в две-три строки, а не обрезанный многоточием
                    maximumLineCount: 3
                    elide: Text.ElideRight
                    lineHeight: 1.15
                    font.pixelSize: 12
                    color: Theme.textTertiary
                    HoverHandler { id: statusHover }
                    ToolTip.visible: statusHover.hovered && truncated
                    ToolTip.text: backend.statusText
                    ToolTip.delay: 300
                }

                PillButton {   // обычная кнопка, а не «премиум»: нейросеть выбирается лентой ниже или в настройках
                    Layout.fillWidth: true
                    text: backend.aiTarget.id === "clipboard" ? "Скопировать для нейросети" : "Отправить в " + backend.aiTarget.name
                    iconName: "sparkle"
                    tip: backend.aiTarget.id === "claude_code"
                         ? "Открыть Claude Code с этим транскриптом и вашим запросом: итоги, задачи, письмо. Другую нейросеть выберите ниже"
                         : backend.aiTarget.web
                           ? "Скопировать разговор с вашим запросом и открыть " + backend.aiTarget.name + " — короткий разговор сразу окажется в поле ввода. Другую нейросеть выберите ниже"
                           : "Скопировать разговор с вашим запросом, чтобы вставить в любую нейросеть"
                    onClicked: backend.sendToAi()
                }

                // лента нейросетей: выбор в одно касание, не заходя в настройки
                ListView {
                    id: aiStrip
                    Layout.fillWidth: true
                    Layout.topMargin: 8
                    Layout.preferredHeight: 28
                    orientation: ListView.Horizontal
                    spacing: 6
                    clip: true
                    boundsBehavior: Flickable.StopAtBounds
                    model: backend.aiChoices
                    Accessible.name: "Нейросеть"
                    Component.onCompleted: positionViewAtIndex(Math.max(0, backend.aiChoices.findIndex(c => c.id === backend.aiTarget.id)), ListView.Contain)
                    delegate: AbstractButton {
                        id: chip
                        required property var modelData
                        readonly property bool chosen: backend.aiTarget.id === modelData.id
                        height: 28
                        width: chipText.implicitWidth + 22
                        hoverEnabled: true
                        focusPolicy: Qt.TabFocus
                        Accessible.name: modelData.label
                        Accessible.checkable: true
                        Accessible.checked: chosen
                        onClicked: backend.setOption("ai_target", modelData.id)
                        background: Rectangle {
                            radius: 14
                            color: chip.chosen ? Qt.rgba(Theme.accent.r, Theme.accent.g, Theme.accent.b, Theme.dark ? 0.18 : 0.12)
                                               : (chip.hovered ? Theme.hover : "transparent")
                            border.color: chip.chosen ? Qt.rgba(Theme.accent.r, Theme.accent.g, Theme.accent.b, 0.55)
                                        : (chip.visualFocus ? Theme.accent : Theme.shellLine)
                            Behavior on color { ColorAnimation { duration: Theme.fast } }
                        }
                        contentItem: Text {
                            id: chipText
                            text: chip.modelData.short
                            horizontalAlignment: Text.AlignHCenter
                            verticalAlignment: Text.AlignVCenter
                            font.pixelSize: 12
                            font.weight: chip.chosen ? Font.DemiBold : Font.Normal
                            color: chip.chosen ? Theme.text : Theme.textSecondary
                        }
                        Tip {
                            shown: chip.hovered || chip.visualFocus
                            text: chip.modelData.label + (chip.modelData.vpn ? ". Из России — только с VPN: без него Говорилка ничего не откроет" : "")
                        }
                    }
                    // мягкие края: видно, что лента прокручивается
                    Rectangle {
                        anchors { left: parent.left; top: parent.top; bottom: parent.bottom }
                        width: 18; visible: !aiStrip.atXBeginning
                        gradient: Gradient { orientation: Gradient.Horizontal
                            GradientStop { position: 0; color: Theme.core }
                            GradientStop { position: 1; color: Qt.rgba(Theme.core.r, Theme.core.g, Theme.core.b, 0) } }
                    }
                    Rectangle {
                        anchors { right: parent.right; top: parent.top; bottom: parent.bottom }
                        width: 18; visible: !aiStrip.atXEnd
                        gradient: Gradient { orientation: Gradient.Horizontal
                            GradientStop { position: 0; color: Qt.rgba(Theme.core.r, Theme.core.g, Theme.core.b, 0) }
                            GradientStop { position: 1; color: Theme.core } }
                    }
                }

                RowLayout {
                    Layout.alignment: Qt.AlignHCenter
                    Layout.topMargin: 10
                    spacing: 5
                    // на невысоком окне места нет: подсказку про клавиши даёт знакомство и пустая лента
                    visible: window.opts.hotkey_enabled && window.height >= 760
                    HoverHandler { id: keysHover }
                    Tip {
                        shown: keysHover.hovered
                        text: window.opts.hotkey_action === "record"
                              ? "Зажмите эти клавиши и подержите полсекунды — запись начнётся или остановится. Работает из любой программы"
                              : "Зажмите эти клавиши и подержите полсекунды — у курсора откроется круговое меню. Работает из любой программы"
                    }
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

    Onboarding { id: tour }
    // знакомство — при первом запуске, как только окно действительно на экране. Раз в полсекунды проверяем, пока не
    // покажем: на Mac окно появляется позже, чем сработал бы разовый таймер, и знакомство терялось
    property bool tourShown: false
    Timer {
        interval: 500
        repeat: true
        running: !window.opts.tour_done && !window.tourShown
        onTriggered: if (window.visible && window.visibility !== Window.Minimized && !tour.opened) { window.tourShown = true; tour.begin() }
    }

    SettingsWindow { id: settingsWindow; onTourRequested: { window.showWindow(); tour.begin() } }
    RadialMenu { id: radial; practice: tour.opened }
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
