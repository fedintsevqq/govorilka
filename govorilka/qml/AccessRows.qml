import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// Разрешения macOS: что нужно Говорилке, что уже разрешено и кнопка «Открыть» прямо на нужный раздел настроек.
// Внизу сброс для переустановки: macOS помнит прежнюю копию приложения, а доступа новой копии нет.
ColumnLayout {
    id: rows
    spacing: 8
    property bool compact: false   // в знакомстве места меньше

    Timer {   // человек включает переключатель в Системных настройках — статус обновляется сам
        interval: 1500
        repeat: true
        running: rows.visible && backend.accessAvailable
        onTriggered: backend.refreshAccess()
    }

    Repeater {
        model: backend.access
        delegate: Rectangle {
            id: row
            required property var modelData
            Layout.fillWidth: true
            implicitHeight: rows.compact ? 40 : 54
            radius: 14
            color: Theme.coreRaised
            border.color: Theme.shellLine
            Accessible.name: modelData.label + (modelData.granted === true ? ": разрешено" : modelData.granted === false ? ": не разрешено" : "")

            Rectangle {   // статус: зелёный — разрешено, красный — нет, серый — система спросит сама
                x: 14; anchors.verticalCenter: parent.verticalCenter
                width: 10; height: 10; radius: 5
                color: row.modelData.granted === true ? Theme.success : row.modelData.granted === false ? Theme.danger : Theme.textTertiary
            }
            Column {
                anchors { left: parent.left; leftMargin: 36; right: openButton.left; rightMargin: 10; verticalCenter: parent.verticalCenter }
                Text { width: parent.width; text: row.modelData.label; elide: Text.ElideRight; font.pixelSize: 13; font.weight: Font.DemiBold; color: Theme.text }
                Text { width: parent.width; text: row.modelData.hint; elide: Text.ElideRight; font.pixelSize: 11; color: Theme.textTertiary }
            }
            PillButton {
                id: openButton
                anchors { right: parent.right; rightMargin: 8; verticalCenter: parent.verticalCenter }
                implicitHeight: 32
                text: row.modelData.granted === true ? "Открыть" : "Разрешить"
                tip: "Открыть «Системные настройки» прямо на разделе «" + row.modelData.label + "» и включить Говорилку"
                variant: row.modelData.granted === false ? "primary" : "ghost"
                onClicked: backend.openPrivacy(row.modelData.pane)
            }
        }
    }

    Text {
        visible: !rows.compact
        Layout.fillWidth: true
        Layout.topMargin: 4
        wrapMode: Text.WordWrap
        text: "Серый кружок — система спросит сама при первой записи. Переключатель включён, а доступа нет? Так бывает после переустановки."
        font.pixelSize: 11
        lineHeight: 1.2
        color: Theme.textTertiary
    }
    PillButton {
        Layout.alignment: Qt.AlignLeft
        implicitHeight: 34
        text: "Сбросить все разрешения"
        tip: "Если вы переустанавливали Говорилку и доступы не работают: сбросить их все. Говорилка закроется, при новом запуске macOS спросит заново"
        onClicked: backend.resetAllPermissions()
    }
}
