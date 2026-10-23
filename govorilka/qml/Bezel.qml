import QtQuick

// Карточка с двойной рамкой: внешняя «оправа» и внутреннее стекло со светлой кромкой сверху,
// концентрические скругления. Так карточка выглядит как деталь, а не плоский прямоугольник.
Item {
    id: bezel
    default property alias content: inner.data
    property int radius: Theme.radiusShell
    property color coreColor: Theme.core

    Rectangle {
        anchors.fill: parent
        radius: bezel.radius
        color: Theme.shell
        border.color: Theme.shellLine
        border.width: 1
    }
    Rectangle {
        id: coreRect
        anchors.fill: parent
        anchors.margins: Theme.bezel
        radius: bezel.radius - Theme.bezel
        color: bezel.coreColor
        border.color: Theme.hairline
        border.width: 1
        clip: true
        // светлая кромка сверху: свет ложится на стекло
        Rectangle {
            anchors { left: parent.left; right: parent.right; top: parent.top; leftMargin: parent.radius; rightMargin: parent.radius }
            height: 1
            color: Theme.highlight
        }
        Item {
            id: inner
            anchors.fill: parent
        }
    }
}
