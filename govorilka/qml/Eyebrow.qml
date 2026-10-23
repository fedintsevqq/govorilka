import QtQuick

// Маленькая пилюля: состояние или раздел, капсом с разрядкой. Точка и подпись всегда по центру пилюли,
// даже если раскладка сделала её шире текста.
Rectangle {
    id: eyebrow
    property alias text: label.text
    property color tint: Theme.textSecondary
    property bool dot: false
    property bool pulse: false            // точка «дышит» — так видно, что идёт запись
    implicitHeight: 22
    implicitWidth: content.width + 20
    radius: height / 2
    color: Qt.rgba(tint.r, tint.g, tint.b, Theme.dark ? 0.12 : 0.10)
    border.color: Qt.rgba(tint.r, tint.g, tint.b, 0.25)
    border.width: 1
    Behavior on color { ColorAnimation { duration: Theme.base } }

    Row {
        id: content
        anchors.centerIn: parent
        // разрядка добавляет пустоту и после последней буквы: сдвигаем на её половину, чтобы центр был честным
        anchors.horizontalCenterOffset: label.font.letterSpacing / 2
        spacing: 6
        Rectangle {
            id: point
            visible: eyebrow.dot
            width: 6; height: 6; radius: 3
            color: eyebrow.tint
            anchors.verticalCenter: parent.verticalCenter
            // пульс раз в 1.4 с: заметен краем глаза, но не мельтешит
            SequentialAnimation on opacity {
                running: eyebrow.pulse && point.visible && !Theme.reduceMotion
                loops: Animation.Infinite
                NumberAnimation { to: 0.35; duration: 700; easing.type: Easing.InOutSine }
                NumberAnimation { to: 1; duration: 700; easing.type: Easing.InOutSine }
            }
        }
        Text {
            id: label
            anchors.verticalCenter: parent.verticalCenter
            font.pixelSize: Theme.fontEyebrow
            font.weight: Font.DemiBold
            font.letterSpacing: 1.6
            font.capitalization: Font.AllUppercase
            color: eyebrow.tint
        }
    }
}
