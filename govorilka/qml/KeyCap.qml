import QtQuick

// Клавиша: подсказки горячих клавиш выглядят как настоящие клавиши.
Rectangle {
    property alias text: label.text
    implicitWidth: Math.max(26, label.implicitWidth + 14)
    implicitHeight: 24
    radius: 7
    color: Theme.coreRaised
    border.color: Theme.shellLine
    border.width: 1
    Rectangle {   // нижняя «толщина» клавиши
        anchors { left: parent.left; right: parent.right; bottom: parent.bottom; leftMargin: 3; rightMargin: 3 }
        height: 2
        radius: 1
        color: Theme.hairline
    }
    Text {
        id: label
        anchors.centerIn: parent
        anchors.verticalCenterOffset: -1
        font.pixelSize: 12
        font.weight: Font.DemiBold
        color: Theme.textSecondary
    }
}
