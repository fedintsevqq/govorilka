import QtQuick
import QtQuick.Controls

// Переключатель-чипса: иконка + подпись, включённое состояние подсвечено акцентом.
AbstractButton {
    id: control
    property string tip: ""   // подсказка при наведении
    property string iconName: "mic"
    // вкладка или выбор одного из двух: повторное нажатие не снимает выбор (checked задаёт тот, кто знает выбор)
    property bool exclusive: false
    checkable: !exclusive
    opacity: enabled ? 1 : 0.45   // недоступная чипса видна сразу
    implicitHeight: 32
    implicitWidth: row.implicitWidth + 24
    focusPolicy: Qt.TabFocus
    hoverEnabled: true
    Accessible.name: text
    Accessible.checkable: true
    Accessible.checked: checked

    background: Rectangle {
        radius: height / 2
        color: control.checked ? Qt.rgba(Theme.accent.r, Theme.accent.g, Theme.accent.b, Theme.dark ? 0.15 : 0.11)
                               : (control.hovered ? Theme.hover : "transparent")
        border.color: control.visualFocus ? Theme.accent
                    : control.checked ? Qt.rgba(Theme.accent.r, Theme.accent.g, Theme.accent.b, 0.42) : Theme.shellLine
        border.width: control.checked || control.visualFocus ? 2 : 1   // включённое видно и по рамке, не только по оттенку
        Behavior on color { ColorAnimation { duration: Theme.base } }
    }
    contentItem: Item {
        Row {
            id: row
            anchors.centerIn: parent
            spacing: 7
            Icon {
                anchors.verticalCenter: parent.verticalCenter
                name: control.iconName
                size: 15
                color: control.checked ? Theme.accent : Theme.textTertiary
            }
            Text {
                anchors.verticalCenter: parent.verticalCenter
                text: control.text
                font.pixelSize: 13
                font.weight: control.checked ? Font.DemiBold : Font.Medium
                color: control.checked ? Theme.text : Theme.textSecondary
            }
        }
    }
    scale: down ? 0.96 : 1
    Behavior on scale { NumberAnimation { duration: Theme.fast; easing.type: Easing.BezierSpline; easing.bezierCurve: Theme.easeOut } }
    Tip { text: control.tip; shown: control.hovered || control.visualFocus }
}
