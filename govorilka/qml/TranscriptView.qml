import QtQuick
import QtQuick.Controls

// Лента транскрипта. Реплики одного говорящего идут группой: кружок с инициалом и имя только у первой.
// Новые строки появляются коротким подъёмом с растворением; если вы прокрутили вверх, лента не дёргается,
// а снизу появляется кнопка «к последней фразе».
Item {
    id: root
    property var model
    property string hotkeyLabel: ""
    property bool recording: false
    signal renameRequested(string voiceId, string name, color tint)

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

    // ---------- строки ----------

    Component {
        id: speechRow
        Item {
            id: speech
            readonly property bool partial: parent.kind === "partial"
            readonly property color tint: Theme.speakerColor(parent.colorIndex)
            readonly property bool showHeader: parent.first
            readonly property bool canRename: parent.colorIndex >= 0 && !partial   // «Я» и недосказанное не переименовываем
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
                    implicitWidth: nameText.implicitWidth + 8
                    Accessible.name: speech.canRename ? "Подписать «" + speech.parent.speaker + "» именем" : speech.parent.speaker
                    onClicked: root.renameRequested(speech.parent.voiceId, speech.parent.speaker, speech.tint)
                    background: Rectangle {
                        radius: 6
                        color: nameButton.visualFocus ? Qt.rgba(speech.tint.r, speech.tint.g, speech.tint.b, 0.16) : "transparent"
                    }
                    contentItem: Text {
                        id: nameText
                        text: speech.parent.speaker
                        font.pixelSize: 13
                        font.weight: Font.DemiBold
                        color: speech.tint
                        font.underline: nameButton.hovered && speech.canRename
                        verticalAlignment: Text.AlignVCenter
                        horizontalAlignment: Text.AlignHCenter
                    }
                    HoverHandler { cursorShape: speech.canRename ? Qt.PointingHandCursor : Qt.ArrowCursor }
                    ToolTip.visible: nameButton.hovered && speech.canRename
                    ToolTip.delay: 600
                    ToolTip.text: "Нажмите, чтобы подписать именем"
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
                width: speech.width - x - 26
                text: speech.parent.text + (speech.partial ? " " : "")
                readOnly: true
                selectByMouse: true
                wrapMode: TextEdit.Wrap
                font.pixelSize: Theme.fontBody
                font.italic: speech.partial
                color: speech.partial ? Theme.textTertiary : Theme.text
                selectionColor: Qt.rgba(Theme.accent.r, Theme.accent.g, Theme.accent.b, 0.35)
                selectedTextColor: Theme.text
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
            Rectangle {
                x: 58; anchors.verticalCenter: parent.verticalCenter
                height: 30
                width: markLabel.implicitWidth + 64
                radius: 15
                color: Theme.markSoft
                border.color: Qt.rgba(Theme.mark.r, Theme.mark.g, Theme.mark.b, 0.35)
                Row {
                    anchors.centerIn: parent
                    spacing: 8
                    Icon { name: "star"; size: 15; color: Theme.mark; anchors.verticalCenter: parent.verticalCenter }
                    Text { id: markLabel; text: markItem.row.text; font.pixelSize: 13; font.weight: Font.DemiBold; color: Theme.mark; anchors.verticalCenter: parent.verticalCenter }
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
            Rectangle { anchors.verticalCenter: parent.verticalCenter; x: 60; width: (parent.width - noteText.implicitWidth) / 2 - 76; height: 1; color: Theme.hairline }
            Text {
                id: noteText
                anchors.centerIn: parent
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
        Behavior on opacity { NumberAnimation { duration: Theme.base } }
        onClicked: { list.follow = true; list.positionViewAtEnd() }
        background: Rectangle {
            radius: 18
            color: jump.down ? Theme.pressed : Theme.glass
            border.color: jump.visualFocus ? Theme.accent : Theme.shellLine
            border.width: jump.visualFocus ? 2 : 1
        }
        contentItem: Row {
            id: jumpRow
            spacing: 6
            Text { text: "К последней фразе"; font.pixelSize: 13; font.weight: Font.Medium; color: Theme.text; anchors.verticalCenter: parent.verticalCenter }
            Icon { name: "chevron"; size: 14; color: Theme.textSecondary; rotation: 90; anchors.verticalCenter: parent.verticalCenter }
        }
    }
}
