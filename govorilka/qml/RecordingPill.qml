import QtQuick
import QtQuick.Window

// Плашка записи поверх всех окон, когда главное окно спрятано или свёрнуто.
// Не принимает фокус и пропускает клики сквозь себя: никогда не мешает работать.
Window {
    id: pill
    width: content.width + 2
    height: 40
    color: "transparent"
    flags: Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool | Qt.WindowDoesNotAcceptFocus
           | Qt.WindowTransparentForInput | Qt.NoDropShadowWindowHint
    readonly property string phase: backend.state
    readonly property bool live: phase === "recording" || phase === "paused"
    property bool mainHidden: true      // задаёт главное окно: оно спрятано, свёрнуто или не в фокусе
    visible: backend.settings.show_indicator && live && mainHidden
    x: Screen.virtualX + Screen.desktopAvailableWidth - width - 22
    y: Screen.virtualY + 22
    title: "Говорилка — запись"
    objectName: "pill"
    transientParent: null   // самостоятельное окно: иначе оно скрыто, пока скрыто главное

    Rectangle {
        id: content
        height: 38
        width: row.implicitWidth + 30
        radius: 19
        color: Theme.glass
        border.color: Qt.rgba(1, 1, 1, Theme.dark ? 0.10 : 0)
        opacity: pill.visible ? 1 : 0
        Behavior on opacity { NumberAnimation { duration: Theme.base } }

        Row {
            id: row
            anchors.centerIn: parent
            spacing: 9
            Rectangle {
                width: 9; height: 9; radius: 4.5
                anchors.verticalCenter: parent.verticalCenter
                color: pill.phase === "paused" ? Theme.pause : Theme.record
                SequentialAnimation on opacity {
                    running: pill.phase === "recording" && pill.visible && !Theme.reduceMotion
                    loops: Animation.Infinite
                    NumberAnimation { to: 0.35; duration: 700; easing.type: Easing.InOutSine }
                    NumberAnimation { to: 1; duration: 700; easing.type: Easing.InOutSine }
                }
            }
            Text {
                anchors.verticalCenter: parent.verticalCenter
                text: pill.phase === "paused" ? "Пауза" : "Запись"
                font.pixelSize: 13
                font.weight: Font.DemiBold
                color: Theme.text
            }
            Text {
                anchors.verticalCenter: parent.verticalCenter
                text: backend.elapsed
                font.pixelSize: 13
                font.features: { "tnum": 1 }
                color: Theme.textSecondary
            }
            Row {   // крошечный уровень звука
                anchors.verticalCenter: parent.verticalCenter
                spacing: 2
                Repeater {
                    model: [0.5, 0.9, 0.65]
                    Rectangle {
                        required property real modelData
                        anchors.verticalCenter: parent.verticalCenter
                        width: 2.5; radius: 1.25
                        height: Math.max(3, 14 * Math.min(1, Math.max(backend.micLevel, backend.systemLevel) * 1.3) * modelData)
                        color: pill.phase === "paused" ? Theme.textTertiary : Theme.record
                        Behavior on height { NumberAnimation { duration: 80 } }
                    }
                }
            }
        }
    }
}
