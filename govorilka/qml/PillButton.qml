import QtQuick
import QtQuick.Controls

// Кнопка-пилюля. У главной кнопки иконка сидит в собственном кружке у правого края
// и чуть смещается при наведении — «кнопка в кнопке».
AbstractButton {
    id: control
    property string iconName: ""
    property string variant: "ghost"   // primary / ghost / danger
    readonly property bool primary: variant === "primary"
    readonly property bool trailing: primary && iconName !== ""
    implicitHeight: 44
    implicitWidth: row.implicitWidth + (trailing ? 26 : 32)
    focusPolicy: Qt.TabFocus
    hoverEnabled: true
    Accessible.name: text

    background: Rectangle {
        radius: height / 2
        color: control.variant === "danger" ? Theme.recordSoft
             : (control.down ? Theme.pressed : control.hovered ? Theme.hover : Theme.shell)
        border.color: control.visualFocus ? Theme.accent : (control.primary ? "transparent" : Theme.shellLine)
        border.width: control.visualFocus ? 1.5 : 1
        Rectangle {   // градиент главной кнопки
            anchors.fill: parent
            radius: parent.radius
            visible: control.primary
            gradient: Gradient {
                orientation: Gradient.Horizontal
                GradientStop { position: 0; color: Theme.accentA }
                GradientStop { position: 1; color: Theme.accentB }
            }
        }
        Rectangle {   // мягкая подсветка главной кнопки при наведении
            anchors.fill: parent
            radius: parent.radius
            color: Theme.fgAccent
            opacity: control.primary && control.hovered && !control.down ? 0.10 : 0
            Behavior on opacity { NumberAnimation { duration: Theme.fast } }
        }
    }
    contentItem: Item {
        Row {
            id: row
            anchors.verticalCenter: parent.verticalCenter
            anchors.left: parent.left
            anchors.leftMargin: control.trailing ? 20 : 16
            spacing: 10
            Icon {
                visible: control.iconName !== "" && !control.trailing
                anchors.verticalCenter: parent.verticalCenter
                name: control.iconName
                size: 18
                color: control.variant === "danger" ? Theme.record : Theme.text
            }
            Text {
                anchors.verticalCenter: parent.verticalCenter
                text: control.text
                font.pixelSize: Theme.fontBody
                font.weight: Font.DemiBold
                color: control.primary ? Theme.fgAccent : (control.variant === "danger" ? Theme.record : Theme.text)
            }
            Rectangle {   // иконка в своём кружке
                visible: control.trailing
                anchors.verticalCenter: parent.verticalCenter
                width: 32; height: 32; radius: 16
                color: Qt.rgba(Theme.fgAccent.r, Theme.fgAccent.g, Theme.fgAccent.b, 0.16)
                scale: control.hovered ? 1.06 : 1
                Behavior on scale { NumberAnimation { duration: Theme.base; easing.type: Easing.BezierSpline; easing.bezierCurve: Theme.easeOut } }
                Icon {
                    anchors.centerIn: parent
                    anchors.horizontalCenterOffset: control.hovered ? 1.5 : 0
                    name: control.iconName
                    size: 16
                    color: Theme.fgAccent
                    Behavior on anchors.horizontalCenterOffset { NumberAnimation { duration: Theme.base; easing.type: Easing.BezierSpline; easing.bezierCurve: Theme.easeOut } }
                }
            }
        }
    }
    scale: down ? 0.97 : 1
    Behavior on scale { NumberAnimation { duration: Theme.fast; easing.type: Easing.BezierSpline; easing.bezierCurve: Theme.easeOut } }
}
