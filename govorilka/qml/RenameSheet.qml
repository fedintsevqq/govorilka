import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// «Кто это?»: подписать говорящего именем. Имя встанет во все его реплики сразу.
// Если в настройках включена память голосов, Говорилка узнает этого человека и в следующих созвонах.
Popup {
    id: sheet
    property string voiceId: ""
    property string currentName: ""
    property color tint: Theme.accent

    function ask(id, name, color) {
        voiceId = id
        currentName = name
        tint = color
        field.text = name === id ? "" : name
        open()
    }

    // пришёл другой транскрипт или голоса пересчитаны: ярлык мог сменить владельца — не переименовываем вслепую
    Connections { target: backend; function onTranscriptInfoChanged() { if (sheet.opened) sheet.close() } }

    parent: Overlay.overlay
    anchors.centerIn: parent
    width: 400
    modal: true
    focus: true
    padding: 0
    Overlay.modal: Rectangle { color: Qt.rgba(0, 0, 0, Theme.dark ? 0.5 : 0.22) }
    enter: Transition {
        ParallelAnimation {
            NumberAnimation { property: "opacity"; from: 0; to: 1; duration: Theme.base; easing.type: Easing.BezierSpline; easing.bezierCurve: Theme.easeOut }
            NumberAnimation { property: "scale"; from: Theme.reduceMotion ? 1 : 0.96; to: 1; duration: Theme.base; easing.type: Easing.BezierSpline; easing.bezierCurve: Theme.easeOut }
        }
    }
    exit: Transition { NumberAnimation { property: "opacity"; to: 0; duration: Theme.fast } }
    onOpened: { field.forceActiveFocus(); field.selectAll() }

    function commit() {
        const name = field.text.trim()
        if (name !== "") backend.renameSpeaker(voiceId, name)
        close()
    }

    background: Bezel {}
    contentItem: ColumnLayout {
        spacing: 12
        Item { Layout.preferredHeight: 14 }
        RowLayout {
            Layout.leftMargin: 24
            Layout.rightMargin: 24
            spacing: 12
            Rectangle {
                width: 38; height: 38; radius: 19
                color: Qt.rgba(sheet.tint.r, sheet.tint.g, sheet.tint.b, 0.16)
                border.color: Qt.rgba(sheet.tint.r, sheet.tint.g, sheet.tint.b, 0.4)
                Icon { anchors.centerIn: parent; name: "person"; size: 18; color: sheet.tint }
            }
            ColumnLayout {
                spacing: 1
                Text { text: "Кто это?"; font.pixelSize: 17; font.weight: Font.DemiBold; color: Theme.text }
                Text { text: sheet.voiceId; Layout.maximumWidth: 280; elide: Text.ElideRight; font.pixelSize: 12; color: Theme.textTertiary }
            }
        }
        Rectangle {
            Layout.fillWidth: true
            Layout.leftMargin: 24
            Layout.rightMargin: 24
            implicitHeight: 44
            radius: 14
            color: Theme.core
            border.color: field.activeFocus ? Qt.rgba(Theme.accent.r, Theme.accent.g, Theme.accent.b, 0.6) : Theme.shellLine
            TextField {
                id: field
                anchors.fill: parent
                leftPadding: 14
                color: Theme.text
                placeholderText: "Имя, например Аня"
                placeholderTextColor: Theme.textTertiary
                font.pixelSize: 14
                selectByMouse: true
                background: Item {}
                Accessible.name: "Имя говорящего"
                onAccepted: sheet.commit()
            }
        }
        Text {
            Layout.fillWidth: true
            Layout.leftMargin: 24
            Layout.rightMargin: 24
            wrapMode: Text.WordWrap
            text: backend.settings.remember_voices
                  ? "Голос запомнится на этом компьютере: в следующих созвонах Говорилка подпишет этого человека сама."
                  : "Имя встанет во все реплики этого человека. Чтобы Говорилка узнавала его в будущих созвонах, включите память голосов в настройках."
            font.pixelSize: 12
            lineHeight: 1.25
            color: Theme.textTertiary
        }
        RowLayout {
            Layout.alignment: Qt.AlignRight
            Layout.rightMargin: 20
            Layout.topMargin: 4
            spacing: 8
            PillButton { text: "Отмена"; tip: "Закрыть, имя не меняется"; onClicked: sheet.close() }
            PillButton { variant: "primary"; text: "Подписать"; tip: "Поставить это имя во все реплики этого человека (Enter)"; iconName: "check"; onClicked: sheet.commit() }
        }
        Item { Layout.preferredHeight: 14 }
    }
}
