import QtQuick

// Короткие сообщения внизу окна. Появляются подъёмом, исчезают сами через 4.5 с (ошибки — через 8 с).
// Входят и уходят одной дорогой — снизу.
Item {
    id: toasts
    width: Math.min(460, (parent ? parent.width : 460) - 32)
    height: column.height

    function show(message, kind) {
        if (!message) return
        for (let i = 0; i < queue.count; ++i)   // одно и то же сообщение дважды не показываем
            if (queue.get(i).message === message) return
        queue.append({ message: message, kind: kind || "info" })
        if (queue.count > 3) queue.remove(0)
    }

    ListModel { id: queue }

    Column {
        id: column
        anchors.bottom: parent.bottom
        anchors.horizontalCenter: parent.horizontalCenter
        spacing: 8
        Repeater {
            model: queue
            delegate: Rectangle {
                id: toast
                required property string message
                required property string kind
                required property int index
                readonly property color tint: kind === "error" ? Theme.danger : kind === "success" ? Theme.success : Theme.accent
                anchors.horizontalCenter: parent.horizontalCenter
                width: Math.min(toasts.width, label.implicitWidth + 64)
                height: Math.max(40, label.implicitHeight + 20)
                radius: Math.min(20, height / 2)
                color: Theme.glass
                border.color: Qt.rgba(tint.r, tint.g, tint.b, 0.35)
                opacity: 0
                Accessible.role: Accessible.AlertMessage
                Accessible.name: message
                transform: Translate { id: lift; y: 10 }
                Component.onCompleted: { enter.start(); life.start() }

                ParallelAnimation {
                    id: enter
                    NumberAnimation { target: toast; property: "opacity"; to: 1; duration: Theme.base; easing.type: Easing.BezierSpline; easing.bezierCurve: Theme.easeOut }
                    NumberAnimation { target: lift; property: "y"; to: 0; duration: Theme.reduceMotion ? 0 : Theme.slow; easing.type: Easing.BezierSpline; easing.bezierCurve: Theme.easeOut }
                }
                HoverHandler { id: toastHover }
                Timer {
                    id: life
                    running: false
                    interval: toast.kind === "error" ? 8000 : 4500
                    // пока курсор на сообщении, оно не исчезает: можно дочитать
                    onTriggered: toastHover.hovered ? restart() : leave.start()
                }
                SequentialAnimation {
                    id: leave
                    ParallelAnimation {
                        NumberAnimation { target: toast; property: "opacity"; to: 0; duration: Theme.fast }
                        NumberAnimation { target: lift; property: "y"; to: 10; duration: Theme.reduceMotion ? 0 : Theme.fast }
                    }
                    ScriptAction { script: if (toast.index >= 0 && toast.index < queue.count) queue.remove(toast.index) }
                }

                Rectangle {
                    x: 16; anchors.verticalCenter: parent.verticalCenter
                    width: 8; height: 8; radius: 4
                    color: toast.tint
                }
                Text {
                    id: label
                    x: 34
                    width: Math.min(toasts.width - 64, implicitWidth)
                    anchors.verticalCenter: parent.verticalCenter
                    text: toast.message
                    wrapMode: Text.WordWrap
                    font.pixelSize: 13
                    color: Theme.text
                }
                MouseArea { anchors.fill: parent; onClicked: leave.start() }
            }
        }
    }
}
