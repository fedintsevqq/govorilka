import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// macOS отказала в доступе. Объясняем и предлагаем одной кнопкой сбросить устаревшее разрешение.
Popup {
    id: sheet
    property string message: ""
    property string service: ""
    property bool canReset: false

    function ask(text, svc, resettable) {
        message = text
        service = svc
        canReset = resettable
        open()
    }

    parent: Overlay.overlay
    anchors.centerIn: parent
    width: 460
    modal: true
    focus: true
    padding: 0
    Overlay.modal: Rectangle { color: Qt.rgba(0, 0, 0, Theme.dark ? 0.55 : 0.25) }

    enter: Transition {
        ParallelAnimation {
            NumberAnimation { property: "opacity"; from: 0; to: 1; duration: Theme.base; easing.type: Easing.BezierSpline; easing.bezierCurve: Theme.easeOut }
            NumberAnimation { property: "scale"; from: Theme.reduceMotion ? 1 : 0.96; to: 1; duration: Theme.base; easing.type: Easing.BezierSpline; easing.bezierCurve: Theme.easeOut }
        }
    }
    exit: Transition { NumberAnimation { property: "opacity"; to: 0; duration: Theme.fast } }

    background: Bezel {}
    contentItem: ColumnLayout {
        spacing: 14
        Item { Layout.preferredHeight: 18 }
        Rectangle {
            Layout.alignment: Qt.AlignHCenter
            width: 52; height: 52; radius: 26
            color: Theme.recordSoft
            Icon { anchors.centerIn: parent; name: sheet.service === "Microphone" ? "mic" : "window"; size: 24; color: Theme.record }
        }
        Text {
            Layout.alignment: Qt.AlignHCenter
            text: "Нужен доступ"
            font.pixelSize: 18
            font.weight: Font.DemiBold
            color: Theme.text
        }
        Text {
            Layout.fillWidth: true
            Layout.leftMargin: 28
            Layout.rightMargin: 28
            horizontalAlignment: Text.AlignHCenter
            wrapMode: Text.WordWrap
            text: sheet.message + (sheet.canReset
                ? "\n\nЕсли доступ уже включён, а сообщение повторяется, в списке осталась запись от прежней версии. Сбросьте её — macOS спросит заново."
                : "")
            font.pixelSize: 13
            lineHeight: 1.3
            color: Theme.textSecondary
        }
        RowLayout {
            Layout.alignment: Qt.AlignHCenter
            Layout.topMargin: 6
            spacing: 10
            PillButton { text: "Позже"; tip: "Закрыть. Без разрешения эта часть не заработает"; onClicked: sheet.close() }
            PillButton {
                visible: sheet.canReset
                variant: "primary"
                text: "Сбросить и закрыть"
                tip: "Сбросить решение macOS о доступе и закрыть Говорилку. При следующем запуске система спросит снова"
                iconName: "power"
                onClicked: { sheet.close(); backend.resetPermission(sheet.service) }
            }
        }
        Item { Layout.preferredHeight: 14 }
    }
}
