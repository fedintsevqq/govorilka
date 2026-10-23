import QtQuick

// Живой уровень звука: пять тонких столбиков с разной «высотой голоса». Тихо — ровная линия.
Item {
    id: bars
    property real level: 0
    property color tint: Theme.accent
    property string label: ""
    property string iconName: "mic"
    implicitWidth: 120
    implicitHeight: 46
    readonly property var profile: [0.45, 0.85, 1.0, 0.7, 0.4]

    Row {
        id: row
        anchors.left: parent.left
        anchors.top: parent.top
        height: 26
        spacing: 3
        Repeater {
            model: bars.profile.length
            Rectangle {
                anchors.verticalCenter: parent.verticalCenter
                width: 3.5
                radius: 2
                height: Math.max(3.5, 26 * Math.min(1, bars.level * 1.3) * bars.profile[index])
                color: bars.level > 0.02 ? bars.tint : Theme.textTertiary
                opacity: bars.level > 0.02 ? 1 : 0.5
                Behavior on height { NumberAnimation { duration: 70; easing.type: Easing.OutCubic } }
            }
        }
    }
    Row {
        anchors.left: parent.left
        anchors.bottom: parent.bottom
        spacing: 6
        Icon { name: bars.iconName; size: 13; color: Theme.textTertiary; anchors.verticalCenter: parent.verticalCenter }
        Text {
            text: bars.label
            font.pixelSize: 12
            font.weight: Font.Medium
            color: Theme.textSecondary
            anchors.verticalCenter: parent.verticalCenter
        }
    }
}
