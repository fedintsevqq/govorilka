import QtQuick
import QtQuick.Controls
import QtQuick.Shapes

// Главная кнопка. Ждёт — по кольцу медленно идёт градиент. Пишет — красная, вокруг «дышит» ореол
// в такт голосу. Пауза — янтарная с кнопкой продолжения. Отклик на нажатие сразу.
AbstractButton {
    id: control
    property string phase: "idle"     // idle / starting / recording / paused / stopping / file
    property real level: 0             // 0..1 — громче всего из двух источников
    readonly property bool live: phase === "recording"
    readonly property bool paused: phase === "paused"
    readonly property bool waiting: phase === "starting" || phase === "stopping"
    property int diameter: 112
    implicitWidth: diameter + 44
    implicitHeight: diameter + 40
    hoverEnabled: true
    focusPolicy: Qt.TabFocus
    Accessible.name: live || paused ? "Остановить запись" : "Начать запись"
    Accessible.description: waiting ? "Подождите, запись запускается или сохраняется" : ""

    background: Item {}
    contentItem: Item {
        id: stage

        // ореол уровня звука: два кольца, второе запаздывает — живое «дыхание»
        Repeater {
            model: 2
            Rectangle {
                anchors.centerIn: parent
                width: control.diameter; height: width; radius: width / 2
                color: "transparent"
                border.width: 2
                border.color: Theme.record
                visible: control.live
                opacity: control.live ? (index === 0 ? 0.45 : 0.22) * Math.min(1, 0.25 + control.level * 1.4) : 0
                scale: 1 + (index === 0 ? 0.10 : 0.22) + control.level * (index === 0 ? 0.22 : 0.42)
                Behavior on scale { NumberAnimation { duration: index === 0 ? 90 : 220; easing.type: Easing.OutCubic } }
                Behavior on opacity { NumberAnimation { duration: 160 } }
            }
        }

        // кольцо ожидания: медленно вращающийся градиент ирис → лазурь
        Shape {
            id: ring
            anchors.centerIn: parent
            width: control.diameter + 14; height: width
            visible: !control.live && !control.paused
            opacity: control.hovered ? 1 : 0.85
            preferredRendererType: Shape.CurveRenderer
            ShapePath {
                // кольцо: внешняя окружность минус внутренняя (чётно-нечётная заливка даёт «дырку»)
                fillRule: ShapePath.OddEvenFill
                strokeColor: "transparent"
                fillGradient: ConicalGradient {
                    centerX: ring.width / 2; centerY: ring.height / 2
                    angle: 0
                    GradientStop { position: 0.0; color: Theme.accentA }
                    GradientStop { position: 0.5; color: Theme.accentB }
                    GradientStop { position: 1.0; color: Theme.accentA }
                }
                PathAngleArc {
                    centerX: ring.width / 2; centerY: ring.height / 2
                    radiusX: ring.width / 2; radiusY: ring.height / 2
                    startAngle: 0; sweepAngle: 360
                }
                PathAngleArc {
                    centerX: ring.width / 2; centerY: ring.height / 2
                    radiusX: ring.width / 2 - 2.5; radiusY: ring.height / 2 - 2.5
                    startAngle: 0; sweepAngle: 360
                }
            }
            RotationAnimation on rotation {
                running: ring.visible && !Theme.reduceMotion
                from: 0; to: 360; duration: 9000; loops: Animation.Infinite
            }
        }

        // сам диск
        Rectangle {
            id: disc
            anchors.centerIn: parent
            width: control.diameter; height: width; radius: width / 2
            color: control.live ? Theme.record : control.paused ? Theme.pause : Theme.coreRaised
            border.color: control.live || control.paused ? "transparent" : Theme.shellLine
            border.width: 1
            Behavior on color { ColorAnimation { duration: Theme.base } }

            Rectangle {   // светлая кромка сверху — диск выглядит выпуклым
                anchors.fill: parent
                radius: width / 2
                gradient: Gradient {
                    GradientStop { position: 0; color: Qt.rgba(1, 1, 1, Theme.dark ? 0.10 : 0.55) }
                    GradientStop { position: 0.5; color: "transparent" }
                }
            }

            Icon {
                anchors.centerIn: parent
                name: control.live || control.paused ? "stop" : "record"   // на паузе кнопка тоже останавливает: значок не врёт
                size: control.live ? 34 : 38
                color: control.live ? Theme.fgRecord : control.paused ? Theme.fgPause : Theme.record
                opacity: control.waiting ? 0.35 : 1
                Behavior on opacity { NumberAnimation { duration: Theme.base } }
            }
            BusyIndicator {
                anchors.centerIn: parent
                running: control.waiting
                visible: running
                width: 46; height: 46
            }
        }
    }
    scale: down ? 0.95 : (hovered ? 1.015 : 1)
    Behavior on scale { NumberAnimation { duration: Theme.fast; easing.type: Easing.BezierSpline; easing.bezierCurve: Theme.easeOut } }
    Tip { text: control.live || control.paused ? "Остановить и сохранить запись" : control.waiting ? "Подождите…" : "Начать запись: ваш микрофон и звук компьютера"; shown: control.hovered }
}
