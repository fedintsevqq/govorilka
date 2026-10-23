import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// Строка настройки: заголовок, пояснение мелким текстом и переключатель-таблетка справа.
Item {
    id: row
    property alias title: titleText.text
    property alias hint: hintText.text
    property bool checked: false
    signal toggled(bool value)
    implicitHeight: Math.max(54, textColumn.implicitHeight + 20)
    Layout.fillWidth: true

    ColumnLayout {
        id: textColumn
        anchors { left: parent.left; right: switchPill.left; verticalCenter: parent.verticalCenter; rightMargin: 16 }
        spacing: 2
        Text { id: titleText; font.pixelSize: Theme.fontBody; font.weight: Font.Medium; color: Theme.text; Layout.fillWidth: true; wrapMode: Text.WordWrap }
        Text { id: hintText; visible: text !== ""; font.pixelSize: 12; color: Theme.textTertiary; Layout.fillWidth: true; wrapMode: Text.WordWrap; lineHeight: 1.2 }
    }

    AbstractButton {
        id: switchPill
        anchors { right: parent.right; verticalCenter: parent.verticalCenter }
        width: 46; height: 28
        checkable: true
        checked: row.checked
        focusPolicy: Qt.TabFocus
        Accessible.name: row.title
        Accessible.checkable: true
        Accessible.checked: checked
        onToggled: row.toggled(checked)
        background: Rectangle {
            radius: 14
            color: switchPill.checked ? Theme.accent : Theme.switchOff
            border.color: switchPill.visualFocus ? Theme.text : "transparent"
            border.width: 1.5
            Behavior on color { ColorAnimation { duration: Theme.base } }
        }
        contentItem: Item {
            Rectangle {
                width: 22; height: 22; radius: 11
                y: 3
                x: switchPill.checked ? 21 : 3
                color: "#FFFFFF"
                scale: switchPill.down ? 1.1 : 1
                Behavior on x { NumberAnimation { duration: Theme.base; easing.type: Easing.BezierSpline; easing.bezierCurve: Theme.easeOut } }
                Behavior on scale { NumberAnimation { duration: Theme.fast } }
            }
        }
    }
}
