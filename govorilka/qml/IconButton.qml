import QtQuick
import QtQuick.Controls

// Круглая кнопка-иконка: подсветка при наведении, сжатие на нажатии (отклик сразу, а не на отпускании).
AbstractButton {
    id: control
    property string iconName: "settings"
    property color iconColor: Theme.textSecondary
    property int size: 36
    property string tip: ""
    implicitWidth: size
    implicitHeight: size
    focusPolicy: Qt.TabFocus
    Accessible.name: tip
    hoverEnabled: true

    background: Rectangle {
        radius: width / 2
        color: control.down ? Theme.pressed : (control.hovered || control.visualFocus ? Theme.hover : "transparent")
        border.color: control.visualFocus ? Theme.accent : "transparent"
        border.width: control.visualFocus ? 1.5 : 0
        Behavior on color { ColorAnimation { duration: Theme.fast } }
    }
    contentItem: Item {
        Icon {
            anchors.centerIn: parent
            name: control.iconName
            size: Math.round(control.size * 0.52)
            color: control.hovered ? Theme.text : control.iconColor
        }
    }
    scale: down ? 0.94 : 1
    Behavior on scale { NumberAnimation { duration: Theme.fast; easing.type: Easing.BezierSpline; easing.bezierCurve: Theme.easeOut } }

    Tip { text: control.tip; shown: control.hovered || control.visualFocus }
}
