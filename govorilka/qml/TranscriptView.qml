import QtQuick
import QtQuick.Controls

// Лента транскрипта. Реплики одного говорящего идут группой: кружок с инициалом и имя только у первой.
// Новые строки появляются коротким подъёмом с растворением; если вы прокрутили вверх, лента не дёргается,
// а снизу появляется кнопка «к последней фразе».
// Текст выделяется мышью сплошь через несколько реплик, как в обычном документе: Ctrl+C (⌘C) копирует выделенное,
// Ctrl+A (⌘A) выделяет всё, правая кнопка открывает меню. Само копирование делает Backend (copyRequested).
Item {
    id: root
    property var model
    property string hotkeyLabel: ""
    property bool recording: false
    signal renameRequested(string voiceId, string name, color tint)
    signal copyRequested(int startRow, int startPos, int endRow, int endPos)
    signal copyAllRequested()

    // ---------- выделение: от (aRow, aPos) — где нажали, до (bRow, bPos) — куда дотянули ----------
    readonly property int endOfRow: 1000000   // «до конца строки», если строка сейчас не создана (за краем ленты)
    property int aRow: -1
    property int aPos: 0
    property int bRow: -1
    property int bPos: 0
    readonly property bool hasSelection: aRow >= 0 && bRow >= 0 && (aRow !== bRow || aPos !== bPos)
    readonly property color selectionTint: Qt.rgba(Theme.accent.r, Theme.accent.g, Theme.accent.b, 0.35)

    function ordered() {
        return aRow < bRow || (aRow === bRow && aPos <= bPos) ? [aRow, aPos, bRow, bPos] : [bRow, bPos, aRow, aPos]
    }
    function rangeFor(row, length) {   // какой кусок строки row выделен: [начало, конец]
        if (!hasSelection) return [0, 0]
        const o = ordered()
        if (row < o[0] || row > o[2]) return [0, 0]
        return [row === o[0] ? Math.min(o[1], length) : 0, row === o[2] ? Math.min(o[3], length) : length]
    }
    function rowSelected(row) {
        if (!hasSelection) return false
        const o = ordered()
        return row >= o[0] && row <= o[2]
    }
    function clearSelection() { aRow = -1; bRow = -1 }
    function selectAll() {
        if (list.count === 0) return
        aRow = 0; aPos = 0; bRow = list.count - 1; bPos = endOfRow
    }
    function openMenu(x, y) { contextMenu.popup(selector, x, y) }
    function copySelection() {
        if (!hasSelection) return
        const o = ordered()
        root.copyRequested(o[0], o[1], o[2], o[3])
    }
    // Точка (x, y) в координатах ленты: какая строка и какой символ под ней.
    function hit(x, y) {
        if (list.count === 0) return null
        const cy = Math.max(0, Math.min(y, list.height - 1))
        let index = list.indexAt(x, cy + list.contentY)
        for (let step = 1; index < 0 && step <= 8; step++)   // промежутки между строками
            index = list.indexAt(x, cy + list.contentY + (y < list.height / 2 ? step : -step) * 2)
        if (index < 0)
            return y >= list.height / 2 ? { row: list.count - 1, pos: endOfRow, header: false } : { row: 0, pos: 0, header: false }
        const loader = list.itemAtIndex(index)
        const row = loader ? loader.item : null
        if (!row || !row.body)   // отметка или пауза: строка выделяется целиком
            return { row: index, pos: x > list.width / 2 ? endOfRow : 0, header: false }
        const inBody = row.body.mapFromItem(list, x, y)
        if (inBody.y < 0) {
            const onName = row.header.visible && row.header.contains(row.header.mapFromItem(list, x, y))
            return { row: index, pos: 0, header: onName }
        }
        if (inBody.y > row.body.height) return { row: index, pos: row.body.length, header: false }
        return { row: index, pos: row.body.positionAt(inBody.x, inBody.y), header: false }
    }

    Shortcut { sequences: [StandardKey.Copy]; enabled: root.hasSelection; onActivated: root.copySelection() }
    Shortcut { sequences: [StandardKey.SelectAll]; enabled: list.count > 0 && root.activeFocus; onActivated: root.selectAll() }
    Keys.onEscapePressed: root.clearSelection()
    Connections {   // лента начата заново (новая запись, другой файл): старое выделение не к чему привязать
        target: root.model
        ignoreUnknownSignals: true
        function onModelReset() { root.clearSelection() }
    }

    function initials(name) {
        if (name === "Я") return "Я"
        const parts = name.split(" ")
        const digits = parts.length > 1 ? parts[parts.length - 1] : ""
        return parts[0].charAt(0) + (digits.length <= 2 ? digits : "")
    }

    ListView {
        id: list
        anchors.fill: parent
        anchors.margins: 6
        clip: true
        model: root.model
        spacing: 2
        boundsBehavior: Flickable.StopAtBounds
        topMargin: 14
        bottomMargin: 20
        property bool follow: true
        onMovementEnded: follow = atYEnd
        onCountChanged: if (follow) Qt.callLater(positionViewAtEnd)

        ScrollBar.vertical: ScrollBar {
            policy: ScrollBar.AsNeeded
            contentItem: Rectangle { implicitWidth: 4; radius: 2; color: Theme.textTertiary; opacity: 0.5 }
        }

        delegate: Loader {
            width: ListView.view.width
            required property int index
            required property string kind
            required property string time
            required property string speaker
            required property string text
            required property int colorIndex
            required property bool first
            required property string voiceId
            sourceComponent: kind === "mark" ? markRow : kind === "note" ? noteRow : speechRow
            // растворение только у живых строк: при загрузке длинного файла строки не мигают разом
            readonly property bool animate: !Theme.reduceMotion && ListView.view !== null && ListView.view.count < 40
            opacity: animate ? 0 : 1
            Component.onCompleted: if (animate) appear.start()
            NumberAnimation on opacity { id: appear; running: false; to: 1; duration: Theme.base; easing.type: Easing.BezierSpline; easing.bezierCurve: Theme.easeOut }
        }
    }

    MouseArea {
        id: selector
        anchors.fill: list
        acceptedButtons: Qt.LeftButton | Qt.RightButton
        preventStealing: true
        property real lastX: 0
        property real lastY: 0
        function extend(x, y) {
            lastX = x; lastY = y
            const p = root.hit(x, y)
            if (p) { root.bRow = p.row; root.bPos = p.pos }
        }
        onPressed: (mouse) => {
            const p = root.hit(mouse.x, mouse.y)
            if (mouse.button === Qt.RightButton) {
                if (p && !root.rowSelected(p.row)) root.clearSelection()
                root.forceActiveFocus()
                root.openMenu(mouse.x, mouse.y)
                return
            }
            if (p && p.header) { mouse.accepted = false; return }   // нажали на имя: это кнопка «подписать»
            root.forceActiveFocus()   // забираем фокус у полей ввода, чтобы Ctrl+C достался ленте
            if (!p) { root.clearSelection(); return }
            if ((mouse.modifiers & Qt.ShiftModifier) && root.aRow >= 0) { extend(mouse.x, mouse.y); return }
            root.aRow = p.row; root.aPos = p.pos; root.bRow = p.row; root.bPos = p.pos
            lastX = mouse.x; lastY = mouse.y
        }
        onPositionChanged: (mouse) => { if (pressedButtons & Qt.LeftButton) extend(mouse.x, mouse.y) }
        onDoubleClicked: (mouse) => {   // двойной щелчок — слово
            const p = root.hit(mouse.x, mouse.y)
            if (!p || p.header) return
            const loader = list.itemAtIndex(p.row)
            const body = loader && loader.item ? loader.item.body : null
            if (!body) { root.aRow = p.row; root.aPos = 0; root.bRow = p.row; root.bPos = root.endOfRow; return }
            body.cursorPosition = Math.min(p.pos, body.length)
            body.selectWord()
            root.aRow = p.row; root.aPos = body.selectionStart; root.bRow = p.row; root.bPos = body.selectionEnd
        }
        onWheel: (wheel) => wheel.accepted = false   // колесо прокручивает ленту как обычно
        // Дотянули до края: лента прокручивается сама, выделение растёт.
        Timer {
            interval: 30
            repeat: true
            running: selector.pressed && (selector.pressedButtons & Qt.LeftButton) && (selector.lastY < 24 || selector.lastY > selector.height - 24)
            onTriggered: {
                const speed = selector.lastY < 24 ? -Math.min(40, 24 - selector.lastY + 6) : Math.min(40, selector.lastY - selector.height + 30)
                const top = list.originY - list.topMargin
                const bottom = Math.max(top, list.originY + list.contentHeight + list.bottomMargin - list.height)
                list.follow = false
                list.contentY = Math.max(top, Math.min(list.contentY + speed, bottom))
                selector.extend(selector.lastX, selector.lastY)
            }
        }
    }

    component MenuRow: MenuItem {
        id: menuRow
        property string keys: ""
        implicitHeight: 34
        contentItem: Item {
            Text { anchors.left: parent.left; anchors.leftMargin: 4; anchors.verticalCenter: parent.verticalCenter; text: menuRow.text; font.pixelSize: 13; color: menuRow.enabled ? Theme.text : Theme.textTertiary }
            Text { anchors.right: parent.right; anchors.rightMargin: 4; anchors.verticalCenter: parent.verticalCenter; text: menuRow.keys; font.pixelSize: 12; color: Theme.textTertiary }
        }
        background: Rectangle { radius: 7; color: menuRow.highlighted && menuRow.enabled ? Theme.hover : "transparent" }
    }
    readonly property string mod: Qt.platform.os === "osx" ? "⌘" : "Ctrl+"
    Menu {
        id: contextMenu
        padding: 6
        background: Rectangle { implicitWidth: 270; radius: 12; color: Theme.coreRaised; border.color: Theme.shellLine }
        MenuRow { text: "Копировать"; keys: root.mod + "C"; enabled: root.hasSelection; onTriggered: root.copySelection() }
        MenuRow { text: "Выделить всё"; keys: root.mod + "A"; enabled: list.count > 0; onTriggered: root.selectAll() }
        MenuSeparator { contentItem: Rectangle { implicitHeight: 1; color: Theme.hairline } }
        MenuRow { text: "Скопировать весь разговор"; enabled: list.count > 0; onTriggered: root.copyAllRequested() }
    }

    // ---------- строки ----------

    Component {
        id: speechRow
        Item {
            id: speech
            readonly property bool partial: parent.kind === "partial"
            readonly property color tint: Theme.speakerColor(parent.colorIndex)
            readonly property bool showHeader: parent.first
            readonly property bool canRename: parent.colorIndex >= 0 && !partial   // «Я» и недосказанное не переименовываем
            property alias body: body
            property alias header: header
            width: parent.width
            implicitHeight: body.implicitHeight + (showHeader ? header.height + 18 : 6)

            Rectangle {   // кружок с инициалом
                visible: speech.showHeader
                x: 18; y: 14
                width: 30; height: 30; radius: 15
                color: Qt.rgba(speech.tint.r, speech.tint.g, speech.tint.b, Theme.dark ? 0.16 : 0.13)
                border.color: Qt.rgba(speech.tint.r, speech.tint.g, speech.tint.b, 0.35)
                Text {
                    anchors.centerIn: parent
                    text: root.initials(speech.parent.speaker)
                    font.pixelSize: 12
                    font.weight: Font.Bold
                    color: speech.tint
                }
            }
            Row {
                id: header
                visible: speech.showHeader
                x: 60; y: 14
                height: visible ? 18 : 0
                spacing: 9
                AbstractButton {
                    id: nameButton
                    enabled: speech.canRename
                    hoverEnabled: true
                    focusPolicy: speech.canRename ? Qt.TabFocus : Qt.NoFocus
                    implicitHeight: 24   // цель нажатия не меньше 24 px
                    implicitWidth: Math.min(nameText.implicitWidth + 8, 280)
                    Accessible.name: speech.canRename ? "Подписать «" + speech.parent.speaker + "» именем" : speech.parent.speaker
                    onClicked: root.renameRequested(speech.parent.voiceId, speech.parent.speaker, speech.tint)
                    background: Rectangle {
                        radius: 6
                        color: nameButton.visualFocus ? Qt.rgba(speech.tint.r, speech.tint.g, speech.tint.b, 0.16) : "transparent"
                    }
                    contentItem: Text {
                        id: nameText
                        width: nameButton.width
                        elide: Text.ElideRight
                        text: speech.parent.speaker
                        font.pixelSize: 13
                        font.weight: Font.DemiBold
                        color: speech.tint
                        font.underline: nameButton.hovered && speech.canRename
                        verticalAlignment: Text.AlignVCenter
                        horizontalAlignment: Text.AlignHCenter
                    }
                    HoverHandler { cursorShape: speech.canRename ? Qt.PointingHandCursor : Qt.ArrowCursor }
                    Tip { text: "Подписать именем: оно встанет во все реплики этого человека"; shown: nameButton.hovered && speech.canRename }
                }
                Text {
                    text: speech.parent.time
                    font.pixelSize: Theme.fontMeta
                    font.features: { "tnum": 1 }
                    color: Theme.textTertiary
                    anchors.baseline: parent.children[0].baseline
                }
            }
            TextEdit {
                id: body
                x: 60
                y: speech.showHeader ? header.y + header.height + 4 : 2
                width: Math.min(speech.width - x - 26, 900)   // на широком экране строки не тянутся на полтора метра
                text: speech.parent.text + (speech.partial ? " " : "")
                readOnly: true
                selectByMouse: false        // мышью выделяет вся лента сразу (selector ниже), а не каждая реплика отдельно
                activeFocusOnPress: false
                persistentSelection: true
                wrapMode: TextEdit.Wrap
                font.pixelSize: Theme.fontBody
                font.italic: speech.partial
                color: speech.partial ? Theme.textTertiary : Theme.text
                selectionColor: root.selectionTint
                selectedTextColor: Theme.text
                readonly property var range: root.rangeFor(speech.parent.index, length)
                function applyRange() { if (range[1] > range[0]) select(range[0], range[1]); else deselect() }
                onRangeChanged: applyRange()
                Component.onCompleted: applyRange()
                HoverHandler { cursorShape: Qt.IBeamCursor }
                Rectangle {   // курсор недосказанной фразы
                    visible: speech.partial
                    width: 2; height: 15; radius: 1
                    color: Theme.accent
                    x: body.positionToRectangle(body.length).x + 3
                    y: body.positionToRectangle(body.length).y + 2
                    SequentialAnimation on opacity {
                        running: speech.partial && !Theme.reduceMotion
                        loops: Animation.Infinite
                        NumberAnimation { to: 0.15; duration: 520 }
                        NumberAnimation { to: 1; duration: 520 }
                    }
                }
            }
        }
    }

    Component {
        id: markRow
        Item {
            id: markItem
            readonly property Item row: parent
            width: parent.width
            implicitHeight: 44
            Rectangle { anchors.fill: parent; anchors.leftMargin: 50; anchors.rightMargin: 20; radius: 8; color: root.selectionTint; visible: root.rowSelected(markItem.row.index) }
            Accessible.role: Accessible.StaticText
            Accessible.name: "Отметка " + row.time + ": " + row.text
            Rectangle {
                x: 58; anchors.verticalCenter: parent.verticalCenter
                height: 30
                width: Math.min(markContent.implicitWidth + 30, markItem.width - 80)
                radius: 15
                color: Theme.markSoft
                border.color: Qt.rgba(Theme.mark.r, Theme.mark.g, Theme.mark.b, 0.35)
                Row {
                    id: markContent
                    anchors.centerIn: parent
                    spacing: 8
                    Icon { name: "star"; size: 15; color: Theme.mark; anchors.verticalCenter: parent.verticalCenter }
                    Text { id: markLabel; text: markItem.row.text; width: Math.min(implicitWidth, markItem.width - 200); elide: Text.ElideRight; font.pixelSize: 13; font.weight: Font.DemiBold; color: Theme.mark; anchors.verticalCenter: parent.verticalCenter }
                    Text { text: markItem.row.time; font.pixelSize: Theme.fontMeta; color: Theme.textTertiary; anchors.verticalCenter: parent.verticalCenter }
                }
            }
        }
    }

    Component {
        id: noteRow
        Item {
            id: noteItem
            readonly property Item row: parent
            width: parent.width
            implicitHeight: 40
            Rectangle { anchors.fill: parent; anchors.leftMargin: 50; anchors.rightMargin: 20; radius: 8; color: root.selectionTint; visible: root.rowSelected(noteItem.row.index) }
            Rectangle { anchors.verticalCenter: parent.verticalCenter; x: 60; width: (parent.width - noteText.implicitWidth) / 2 - 76; height: 1; color: Theme.hairline }
            Text {
                id: noteText
                anchors.centerIn: parent
                width: Math.min(implicitWidth, parent.width - 200)
                elide: Text.ElideRight
                text: noteItem.row.text + " · " + noteItem.row.time
                font.pixelSize: Theme.fontMeta
                font.letterSpacing: 0.4
                color: Theme.textTertiary
            }
            Rectangle { anchors.verticalCenter: parent.verticalCenter; anchors.right: parent.right; anchors.rightMargin: 26; width: (parent.width - noteText.implicitWidth) / 2 - 40; height: 1; color: Theme.hairline }
        }
    }

    // ---------- пусто ----------

    Column {
        anchors.centerIn: parent
        spacing: 14
        visible: list.count === 0
        opacity: visible ? 1 : 0
        Rectangle {
            anchors.horizontalCenter: parent.horizontalCenter
            width: 76; height: 76; radius: 38
            gradient: Gradient {
                GradientStop { position: 0; color: Qt.rgba(Theme.accentA.r, Theme.accentA.g, Theme.accentA.b, 0.22) }
                GradientStop { position: 1; color: Qt.rgba(Theme.accentB.r, Theme.accentB.g, Theme.accentB.b, 0.10) }
            }
            border.color: Qt.rgba(Theme.accentA.r, Theme.accentA.g, Theme.accentA.b, 0.3)
            Icon { anchors.centerIn: parent; name: root.recording ? "wave" : "mic"; size: 30; color: Theme.accent }
        }
        Text {
            anchors.horizontalCenter: parent.horizontalCenter
            text: root.recording ? "Слушаю…" : "Здесь появится разговор"
            font.pixelSize: 18
            font.weight: Font.DemiBold
            font.letterSpacing: -0.2
            color: Theme.text
        }
        Text {
            anchors.horizontalCenter: parent.horizontalCenter
            width: 330
            horizontalAlignment: Text.AlignHCenter
            wrapMode: Text.WordWrap
            text: root.recording ? "Как только кто-то заговорит, текст побежит здесь — ваш голос и голоса собеседников отдельно."
                                 : "Нажмите запись или откройте аудио и видео. Можно и не открывать окно: зажмите горячую клавишу."
            font.pixelSize: 13
            lineHeight: 1.3
            color: Theme.textSecondary
        }
        Row {
            anchors.horizontalCenter: parent.horizontalCenter
            spacing: 6
            visible: !root.recording && root.hotkeyLabel !== ""
            Repeater {
                model: root.hotkeyLabel.split(" + ")
                KeyCap { required property string modelData; text: modelData }
            }
        }
    }

    // ---------- к последней фразе ----------

    AbstractButton {
        id: jump
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.bottom: parent.bottom
        anchors.bottomMargin: 16
        implicitHeight: 36
        implicitWidth: jumpRow.implicitWidth + 32
        focusPolicy: Qt.TabFocus
        hoverEnabled: true
        visible: opacity > 0
        opacity: !list.follow && list.count > 0 ? 1 : 0
        Accessible.name: "К последней фразе"
        Tip { text: "Прокрутить вниз и снова следить за новыми фразами"; shown: jump.hovered }
        Behavior on opacity { NumberAnimation { duration: Theme.base } }
        onClicked: { list.follow = true; list.positionViewAtEnd() }
        background: Rectangle {
            radius: 18
            color: jump.down ? Theme.pressed : Theme.glass
            border.color: jump.visualFocus ? Theme.accent : Theme.shellLine
            border.width: jump.visualFocus ? 2 : 1
        }
        contentItem: Item {   // подпись и стрелка ровно по центру пилюли
            Row {
                id: jumpRow
                anchors.centerIn: parent
                spacing: 6
                Text { text: "К последней фразе"; font.pixelSize: 13; font.weight: Font.Medium; color: Theme.text; anchors.verticalCenter: parent.verticalCenter }
                Icon { name: "chevron"; size: 14; color: Theme.textSecondary; rotation: 90; anchors.verticalCenter: parent.verticalCenter }
            }
        }
    }
}
