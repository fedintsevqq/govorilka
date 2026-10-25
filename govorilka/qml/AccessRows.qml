import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// Разрешения macOS: что нужно Говорилке, что уже разрешено и кнопка «Открыть» прямо на нужный раздел настроек.
// Внизу сброс для переустановки: macOS помнит прежнюю копию приложения, а доступа новой копии нет.
ColumnLayout {
    id: rows
    spacing: 8
    property bool compact: false   // в знакомстве места меньше
    readonly property int missing: backend.access.filter(row => row.granted !== true).length
    readonly property bool allGranted: backend.access.length > 0 && missing === 0

    Timer {   // человек включает переключатель в Системных настройках — статус обновляется сам
        interval: 1500
        repeat: true
        running: rows.visible && backend.accessAvailable
        onTriggered: backend.refreshAccess()
    }

    // итог сверху: сколько осталось, а когда всё зелёное — «Всё готово»
    Rectangle {
        Layout.fillWidth: true
        implicitHeight: 34
        radius: 12
        readonly property color tint: rows.allGranted ? Theme.success : Theme.accent
        color: Qt.rgba(tint.r, tint.g, tint.b, Theme.dark ? 0.14 : 0.10)
        border.color: Qt.rgba(tint.r, tint.g, tint.b, 0.35)
        Row {
            anchors.centerIn: parent
            spacing: 8
            Icon { name: rows.allGranted ? "check" : "bolt"; size: 15; color: parent.parent.tint; anchors.verticalCenter: parent.verticalCenter }
            Text {
                anchors.verticalCenter: parent.verticalCenter
                text: rows.allGranted ? "Всё готово: все разрешения выданы"
                    : "Осталось разрешить: " + rows.missing + " из " + backend.access.length + ". Нажмите «Разрешить», включите Говорилку и вернитесь сюда"
                font.pixelSize: 12
                font.weight: Font.DemiBold
                color: Theme.text
            }
        }
        Accessible.role: Accessible.StaticText
        Accessible.name: rows.allGranted ? "Все разрешения выданы" : "Осталось разрешить: " + rows.missing
    }

    Repeater {
        model: backend.access
        delegate: Rectangle {
            id: row
            required property var modelData
            Layout.fillWidth: true
            implicitHeight: rows.compact ? 36 : 54
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
                tip: row.modelData.granted === true
                     ? "Открыть «Системные настройки» на разделе «" + row.modelData.label + "»"
                     : "Показать запрос macOS или, если он уже был, открыть раздел «" + row.modelData.label + "»: останется включить Говорилку"
                variant: row.modelData.granted === false ? "primary" : "ghost"
                onClicked: row.modelData.granted === true ? backend.openPrivacy(row.modelData.pane) : backend.requestAccess(row.modelData.id)
            }
        }
    }

    Text {
        visible: !rows.compact
        Layout.fillWidth: true
        Layout.topMargin: 4
        wrapMode: Text.WordWrap
        text: "Серый кружок — macOS ещё не спрашивала: нажмите «Разрешить». Переключатель включён, а кружок красный? Так бывает после переустановки: нажмите «Сбросить все разрешения»."
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
