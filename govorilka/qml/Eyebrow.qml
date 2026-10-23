import QtQuick

// Маленькая пилюля над заголовком: состояние или раздел, капсом с разрядкой.
Rectangle {
    id: eyebrow
    property alias text: label.text
    property color tint: Theme.textSecondary
    property bool dot: false
    implicitHeight: 22
    implicitWidth: label.implicitWidth + (dot ? 30 : 18)
    radius: height / 2
    color: Qt.rgba(tint.r, tint.g, tint.b, Theme.dark ? 0.12 : 0.10)
    border.color: Qt.rgba(tint.r, tint.g, tint.b, 0.25)
    border.width: 1
    Behavior on color { ColorAnimation { duration: Theme.base } }

    Rectangle {
        id: pulse
        visible: eyebrow.dot
        width: 6; height: 6; radius: 3
        color: eyebrow.tint
        anchors.verticalCenter: parent.verticalCenter
        x: 10
        // пульс раз в 1.4 с: заметен краем глаза, но не мельтешит
        SequentialAnimation on opacity {
            running: pulse.visible && !Theme.reduceMotion
            loops: Animation.Infinite
            NumberAnimation { to: 0.35; duration: 700; easing.type: Easing.InOutSine }
            NumberAnimation { to: 1; duration: 700; easing.type: Easing.InOutSine }
        }
    }
    Text {
        id: label
        anchors.verticalCenter: parent.verticalCenter
        x: eyebrow.dot ? 22 : 9
        font.pixelSize: Theme.fontEyebrow
        font.weight: Font.DemiBold
        font.letterSpacing: 1.6
        font.capitalization: Font.AllUppercase
        color: eyebrow.tint
    }
}
