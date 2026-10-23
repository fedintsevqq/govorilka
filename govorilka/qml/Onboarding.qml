import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// Знакомство при первом запуске: четыре коротких шага. На втором можно потренироваться:
// клавиши подсвечиваются, пока вы их держите, и открывается настоящее круговое меню.
// Выбор в нём на тренировке ничего не запускает. Повторить знакомство можно из настроек.
Popup {
    id: tour
    objectName: "tour"
    property int step: 0
    readonly property int steps: 4
    readonly property var opts: backend.settings
    readonly property var chordKeys: String(opts.hotkeyLabel).split(" + ")
    readonly property var heldKeys: backend.liveKeys && backend.liveKeys !== "ничего" ? backend.liveKeys.split(" + ") : []
    readonly property bool chordHeld: chordKeys.length > 0 && chordKeys.every(k => heldKeys.indexOf(k) >= 0)
    readonly property bool practiceStep: step === 1 && opened
    property bool menuSeen: false
    property string chosen: ""

    function begin() {
        step = 0
        menuSeen = false
        chosen = ""
        open()
    }
    function next() { if (step < steps - 1) step++; else finish() }
    function back() { if (step > 0) step-- }
    function finish() { close() }
    function labelOf(id) {
        return { record: "Запись", claude: backend.aiTarget.short, transcript: "Транскрипт", settings: "Настройки",
                 window: "Окно", stop: "Стоп", pause: "Пауза", mark: "Отметка" }[id] || id
    }

    // пока знакомство открыто, горячая клавиша только тренирует: ничего не записывает и не останавливает
    onOpened: { backend.setTourActive(true); primary.forceActiveFocus() }
    onPracticeStepChanged: backend.watchKeys("tour", practiceStep)
    onClosed: {
        backend.watchKeys("tour", false)
        backend.finishTour()
    }

    Connections {
        target: backend
        enabled: tour.practiceStep
        function onRadialRequested(x, y) { tour.menuSeen = true }
        function onPracticed(id) { tour.chosen = id }
    }

    parent: Overlay.overlay
    anchors.centerIn: parent
    width: 600
    modal: true
    focus: true
    padding: 0
    closePolicy: Popup.NoAutoClose   // случайный клик мимо не должен выкидывать из знакомства
    Overlay.modal: Rectangle { color: Qt.rgba(0, 0, 0, Theme.dark ? 0.6 : 0.3) }
    enter: Transition {
        ParallelAnimation {
            NumberAnimation { property: "opacity"; from: 0; to: 1; duration: Theme.slow; easing.type: Easing.BezierSpline; easing.bezierCurve: Theme.easeOut }
            NumberAnimation { property: "scale"; from: Theme.reduceMotion ? 1 : 0.96; to: 1; duration: Theme.slow; easing.type: Easing.BezierSpline; easing.bezierCurve: Theme.easeOut }
        }
    }
    exit: Transition { NumberAnimation { property: "opacity"; to: 0; duration: Theme.fast } }

    background: Bezel {}
    contentItem: FocusScope {
        implicitHeight: 520
        focus: true
        Keys.onEscapePressed: tour.finish()
        Keys.onRightPressed: tour.next()
        Keys.onLeftPressed: tour.back()
        Keys.onReturnPressed: tour.next()
        Keys.onEnterPressed: tour.next()

        // ---------- шапка: шаги и «пропустить» ----------
        RowLayout {
            id: head
            anchors { left: parent.left; right: parent.right; top: parent.top; leftMargin: 30; rightMargin: 18; topMargin: 22 }
            Row {
                spacing: 6
                Repeater {
                    model: tour.steps
                    Rectangle {
                        required property int index
                        width: index === tour.step ? 22 : 7
                        height: 7
                        radius: 3.5
                        color: index <= tour.step ? Theme.accent : Theme.hairline
                        Behavior on width { NumberAnimation { duration: Theme.base; easing.type: Easing.BezierSpline; easing.bezierCurve: Theme.easeOut } }
                        Behavior on color { ColorAnimation { duration: Theme.base } }
                    }
                }
            }
            Item { Layout.fillWidth: true }
            AbstractButton {
                id: skip
                visible: tour.step < tour.steps - 1
                hoverEnabled: true
                focusPolicy: Qt.TabFocus
                implicitHeight: 30
                implicitWidth: skipText.implicitWidth + 20
                Accessible.name: "Пропустить знакомство"
                Tip { text: "Закрыть знакомство (Esc). Открыть снова можно в настройках"; shown: skip.hovered }
                onClicked: tour.finish()
                background: Rectangle { radius: 15; color: skip.hovered ? Theme.hover : "transparent"; border.color: skip.visualFocus ? Theme.accent : "transparent" }
                contentItem: Text { id: skipText; text: "Пропустить"; font.pixelSize: 13; color: Theme.textSecondary; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter }
            }
        }

        // ---------- шаги ----------
        Item {
            id: pages
            anchors { left: parent.left; right: parent.right; top: head.bottom; bottom: foot.top; leftMargin: 30; rightMargin: 30; topMargin: 8 }

            Page0 { visible: tour.step === 0 }
            Page1 { visible: tour.step === 1 }
            Page2 { visible: tour.step === 2 }
            Page3 { visible: tour.step === 3 }
        }

        // ---------- низ: назад / дальше ----------
        RowLayout {
            id: foot
            anchors { left: parent.left; right: parent.right; bottom: parent.bottom; leftMargin: 24; rightMargin: 24; bottomMargin: 22 }
            spacing: 8
            PillButton { text: "Назад"; tip: "Предыдущий шаг (стрелка влево)"; visible: tour.step > 0; onClicked: tour.back() }
            Item { Layout.fillWidth: true }
            PillButton {
                visible: tour.step === tour.steps - 1
                text: "Начать запись"
                tip: "Закрыть знакомство и сразу начать запись"
                iconName: "record"
                onClicked: { tour.finish(); backend.startRecording("") }
            }
            PillButton {
                id: primary
                variant: "primary"
                text: tour.step === 0 ? "Покажите" : tour.step === tour.steps - 1 ? "Всё понятно" : "Дальше"
                iconName: tour.step === tour.steps - 1 ? "check" : "chevron"
                tip: tour.step === tour.steps - 1 ? "Закрыть знакомство. Открыть снова можно в настройках" : "Следующий шаг (стрелка вправо или Enter)"
                onClicked: tour.next()
            }
        }
    }

    // ===================== страницы =====================

    // общий вид страницы: картинка сверху, заголовок, текст. Появляется подъёмом с растворением.
    component Page: ColumnLayout {
        id: page
        default property alias art: artBox.data
        property string title: ""
        property string body: ""
        anchors.fill: parent
        spacing: 0
        opacity: visible ? 1 : 0
        transform: Translate { y: page.visible || Theme.reduceMotion ? 0 : 8; Behavior on y { NumberAnimation { duration: Theme.slow; easing.type: Easing.BezierSpline; easing.bezierCurve: Theme.easeOut } } }
        Behavior on opacity { NumberAnimation { duration: Theme.slow; easing.type: Easing.BezierSpline; easing.bezierCurve: Theme.easeOut } }
        Item { id: artBox; Layout.fillWidth: true; Layout.fillHeight: true; Layout.minimumHeight: 120; Layout.maximumHeight: 210 }
        Text {
            Layout.fillWidth: true
            Layout.topMargin: 14
            text: page.title
            wrapMode: Text.WordWrap
            font.pixelSize: 22
            font.weight: Font.Bold
            font.letterSpacing: -0.4
            color: Theme.text
        }
        Text {
            Layout.fillWidth: true
            Layout.topMargin: 8
            text: page.body
            textFormat: Text.StyledText
            wrapMode: Text.WordWrap
            font.pixelSize: 14
            lineHeight: 1.35
            color: Theme.textSecondary
        }
        Item { Layout.preferredHeight: 4 }
    }

    // большая клавиша, которая подсвечивается, пока её держат
    component BigKey: Rectangle {
        id: key
        property string label: ""
        property bool held: false
        implicitWidth: Math.max(64, keyText.implicitWidth + 36)
        implicitHeight: 58
        radius: 14
        color: held ? Qt.rgba(Theme.accent.r, Theme.accent.g, Theme.accent.b, Theme.dark ? 0.22 : 0.14) : Theme.coreRaised
        border.color: held ? Theme.accent : Theme.shellLine
        border.width: held ? 2 : 1
        y: held ? 3 : 0
        Behavior on y { NumberAnimation { duration: Theme.fast; easing.type: Easing.BezierSpline; easing.bezierCurve: Theme.easeOut } }
        Behavior on color { ColorAnimation { duration: Theme.fast } }
        Rectangle {   // «толщина» клавиши: у нажатой её почти не видно
            anchors { left: parent.left; right: parent.right; bottom: parent.bottom; leftMargin: 6; rightMargin: 6 }
            height: key.held ? 1 : 4
            radius: 2
            color: Theme.hairline
        }
        Text {
            id: keyText
            anchors.centerIn: parent
            anchors.verticalCenterOffset: -1
            text: key.label
            font.pixelSize: 17
            font.weight: Font.DemiBold
            color: key.held ? Theme.text : Theme.textSecondary
        }
    }

    // 1. Что это
    component Page0: Page {
        title: "Говорилка записывает созвоны и сразу превращает их в текст"
        body: "Ваш голос и голоса собеседников идут отдельно. Всё распознаётся на этом компьютере, в интернет ничего не уходит. Текст сохраняется в файл, пока вы говорите."
        Row {
            anchors.centerIn: parent
            spacing: 18
            Repeater {
                model: [
                    { icon: "mic", text: "Вы", tint: Theme.me },
                    { icon: "speaker", text: "Собеседники", tint: Theme.others[0] },
                    { icon: "file", text: "Текст в файле", tint: Theme.accentB }
                ]
                Column {
                    required property var modelData
                    required property int index
                    spacing: 10
                    Rectangle {
                        anchors.horizontalCenter: parent.horizontalCenter
                        width: 92; height: 92; radius: 30
                        color: Qt.rgba(modelData.tint.r, modelData.tint.g, modelData.tint.b, Theme.dark ? 0.14 : 0.10)
                        border.color: Qt.rgba(modelData.tint.r, modelData.tint.g, modelData.tint.b, 0.35)
                        Icon { anchors.centerIn: parent; name: modelData.icon; size: 38; color: modelData.tint }
                    }
                    Text { anchors.horizontalCenter: parent.horizontalCenter; text: modelData.text; font.pixelSize: 13; font.weight: Font.Medium; color: Theme.textSecondary }
                }
            }
        }
    }

    // 2. Горячая клавиша — с тренировкой
    component Page1: Page {
        title: !tour.opts.hotkey_enabled ? "Горячая клавиша выключена"
             : tour.chosen !== "" ? "Получилось!"
             : tour.menuSeen ? "Теперь ведите мышь к действию и отпустите"
             : "Зажмите " + tour.chordKeys.join(" + ") + " и подержите полсекунды"
        body: !tour.opts.hotkey_enabled ? "С ней Говорилкой можно управлять, не открывая окно. Включите её — это одна кнопка ниже."
            : tour.chosen !== "" ? "Вы выбрали «" + tour.labelOf(tour.chosen) + "». Это тренировка, поэтому ничего не запустилось. В работе так же: зажали, повели мышь, отпустили. Можно и просто кликнуть по кружку."
            : tour.menuSeen ? "Меню открылось у курсора. Не отпуская клавиш, ведите мышь в сторону любого кружка: попадать точно не нужно, важно направление."
            : "Так открывается круговое меню: запись, пауза, отметка, отправка нейросети. Работает из любой программы, даже когда окно Говорилки закрыто. Попробуйте прямо сейчас." +
              (backend.isMac && backend.inputMonitoringMissing ? "<br><br><b>macOS пока не разрешила читать клавиши.</b> Откройте Настройки → Конфиденциальность → Мониторинг ввода и включите Говорилку." : "")
        Column {
            anchors.centerIn: parent
            spacing: 22
            Row {
                anchors.horizontalCenter: parent.horizontalCenter
                spacing: 12
                visible: tour.opts.hotkey_enabled
                Repeater {
                    model: tour.chordKeys
                    Row {
                        required property string modelData
                        required property int index
                        spacing: 12
                        Text { visible: index > 0; text: "+"; font.pixelSize: 20; color: Theme.textTertiary; anchors.verticalCenter: parent.verticalCenter }
                        BigKey { label: modelData; held: tour.heldKeys.indexOf(modelData) >= 0 }
                    }
                }
            }
            // полоска удержания и статус
            Item {
                anchors.horizontalCenter: parent.horizontalCenter
                width: 300; height: 40
                visible: tour.opts.hotkey_enabled
                Rectangle {
                    id: holdTrack
                    width: parent.width; height: 6; radius: 3
                    color: Theme.hairline
                    Rectangle {
                        height: parent.height; radius: 3
                        width: tour.chosen !== "" || tour.menuSeen ? parent.width : tour.chordHeld ? parent.width : 0
                        color: tour.chosen !== "" ? Theme.success : Theme.accent
                        Behavior on width { NumberAnimation { duration: tour.chordHeld && !tour.menuSeen ? 500 : Theme.fast; easing.type: Easing.Linear } }
                    }
                }
                Row {
                    anchors.horizontalCenter: parent.horizontalCenter
                    anchors.top: holdTrack.bottom
                    anchors.topMargin: 12
                    spacing: 7
                    Icon {
                        anchors.verticalCenter: parent.verticalCenter
                        name: tour.chosen !== "" ? "check" : "keyboard"
                        size: 16
                        color: tour.chosen !== "" ? Theme.success : Theme.textTertiary
                    }
                    Text {
                        anchors.verticalCenter: parent.verticalCenter
                        text: tour.chosen !== "" ? "Готово — жмите «Дальше»"
                            : tour.menuSeen ? "Меню открыто"
                            : tour.chordHeld ? "Держите…"
                            : "Сейчас нажато: " + (backend.liveKeys || "ничего")
                        font.pixelSize: 13
                        color: tour.chosen !== "" ? Theme.success : Theme.textTertiary
                    }
                }
            }
            PillButton {
                anchors.horizontalCenter: parent.horizontalCenter
                visible: !tour.opts.hotkey_enabled
                variant: "primary"
                text: "Включить горячую клавишу"
                tip: "Включает управление с клавиатуры; изменить комбинацию можно в настройках"
                iconName: "keyboard"
                onClicked: backend.setOption("hotkey_enabled", true)
            }
        }
    }

    // 3. Окно можно закрыть
    component Page2: Page {
        title: "Окно можно закрыть — запись продолжится"
        body: backend.isMac
              ? "Говорилка остаётся в строке меню вверху экрана. Пока идёт запись, в углу видна маленькая плашка с таймером. Щелчок по значку в строке меню открывает действия."
              : "Крестик прячет окно в трей — к значкам у часов. Пока идёт запись, в углу видна маленькая плашка с таймером. Щелчок по значку в трее открывает действия."
        Item {
            anchors.fill: parent
            // условный край экрана со значком и плашкой записи
            Rectangle {
                anchors.centerIn: parent
                width: 420; height: 170
                radius: 18
                color: Theme.bgDeep
                border.color: Theme.shellLine
                Rectangle {   // полоса: строка меню на Mac сверху, панель задач Windows снизу
                    anchors { left: parent.left; right: parent.right; leftMargin: 1; rightMargin: 1 }
                    y: backend.isMac ? 1 : parent.height - height - 1
                    height: 34
                    radius: 17
                    color: Theme.shell
                    Row {
                        anchors.right: parent.right
                        anchors.rightMargin: 16
                        anchors.verticalCenter: parent.verticalCenter
                        spacing: 14
                        Rectangle {
                            width: 24; height: 24; radius: 7
                            anchors.verticalCenter: parent.verticalCenter
                            gradient: Gradient {
                                orientation: Gradient.Horizontal
                                GradientStop { position: 0; color: Theme.accentA }
                                GradientStop { position: 1; color: Theme.accentB }
                            }
                            Icon { anchors.centerIn: parent; name: "wave"; size: 15; color: "#FFFFFF" }
                            Rectangle {   // мягкое кольцо-подсказка вокруг значка
                                anchors.centerIn: parent
                                width: 38; height: 38; radius: 19
                                color: "transparent"
                                border.color: Theme.accent
                                border.width: 2
                                SequentialAnimation on opacity {
                                    running: !Theme.reduceMotion && tour.step === 2
                                    loops: Animation.Infinite
                                    NumberAnimation { from: 0.9; to: 0.15; duration: 900; easing.type: Easing.InOutSine }
                                    NumberAnimation { from: 0.15; to: 0.9; duration: 900; easing.type: Easing.InOutSine }
                                }
                            }
                        }
                        Text { text: "12:30"; font.pixelSize: 12; color: Theme.textSecondary; anchors.verticalCenter: parent.verticalCenter }
                    }
                }
                Rectangle {   // плашка записи в углу
                    anchors.right: parent.right
                    anchors.rightMargin: 16
                    y: backend.isMac ? 50 : 16
                    width: pillRow.implicitWidth + 26; height: 32; radius: 16
                    color: Theme.glass
                    border.color: Theme.shellLine
                    Row {
                        id: pillRow
                        anchors.centerIn: parent
                        spacing: 8
                        Rectangle { width: 8; height: 8; radius: 4; color: Theme.record; anchors.verticalCenter: parent.verticalCenter }
                        Text { text: "Запись"; font.pixelSize: 12; font.weight: Font.DemiBold; color: Theme.text }
                        Text { text: "01:27"; font.pixelSize: 12; color: Theme.textSecondary }
                    }
                }
            }
        }
    }

    // 4. После созвона
    component Page3: Page {
        title: "После созвона"
        body: "Нажмите на имя «Собеседник 1», чтобы подписать человека, — имя встанет во все его реплики. Важный момент можно отметить звёздочкой. Кнопка «Отправить в " + backend.aiTarget.name + "» отдаёт весь разговор нейросети: ChatGPT, Claude или другой — выберите в настройках. Файлы лежат в папке <b>" + String(tour.opts.transcripts_dir).split(/[\\/]/).slice(-2).join("/") + "</b>."
        Column {
            anchors.centerIn: parent
            spacing: 12
            Rectangle {   // реплика с подсказкой «нажмите на имя»
                width: 420; height: 74; radius: 16
                color: Theme.coreRaised
                border.color: Theme.shellLine
                Rectangle {
                    x: 16; y: 14
                    width: 30; height: 30; radius: 15
                    color: Qt.rgba(Theme.others[0].r, Theme.others[0].g, Theme.others[0].b, 0.16)
                    Text { anchors.centerIn: parent; text: "С1"; font.pixelSize: 12; font.weight: Font.Bold; color: Theme.others[0] }
                }
                Text {
                    id: nameDemo
                    x: 58; y: 14
                    text: "Собеседник 1"
                    font.pixelSize: 13; font.weight: Font.DemiBold; font.underline: true
                    color: Theme.others[0]
                }
                Text { x: 58; y: 38; text: "Добрый день, коллеги. Начинаем планёрку."; font.pixelSize: 13; color: Theme.text }
                Rectangle {   // всплывающая подсказка
                    x: nameDemo.x + nameDemo.implicitWidth + 12
                    y: 10
                    width: hintRow.implicitWidth + 20; height: 26; radius: 13
                    color: Theme.accent
                    Row {
                        id: hintRow
                        anchors.centerIn: parent
                        spacing: 6
                        Icon { name: "edit"; size: 13; color: Theme.fgAccent; anchors.verticalCenter: parent.verticalCenter }
                        Text { text: "Кто это?"; font.pixelSize: 12; font.weight: Font.DemiBold; color: Theme.fgAccent }
                    }
                }
            }
            Row {
                anchors.horizontalCenter: parent.horizontalCenter
                spacing: 8
                Repeater {
                    model: [
                        { icon: "star", text: "Отметка", tint: Theme.mark },
                        { icon: "sparkle", text: "В " + backend.aiTarget.name, tint: Theme.accentA },
                        { icon: "folder", text: "Папка", tint: Theme.accentB }
                    ]
                    Rectangle {
                        required property var modelData
                        width: chip.implicitWidth + 26; height: 34; radius: 17
                        color: Qt.rgba(modelData.tint.r, modelData.tint.g, modelData.tint.b, Theme.dark ? 0.12 : 0.09)
                        border.color: Qt.rgba(modelData.tint.r, modelData.tint.g, modelData.tint.b, 0.3)
                        Row {
                            id: chip
                            anchors.centerIn: parent
                            spacing: 7
                            Icon { name: modelData.icon; size: 15; color: modelData.tint; anchors.verticalCenter: parent.verticalCenter }
                            Text { text: modelData.text; font.pixelSize: 13; font.weight: Font.Medium; color: Theme.text; anchors.verticalCenter: parent.verticalCenter }
                        }
                    }
                }
            }
        }
    }
}
