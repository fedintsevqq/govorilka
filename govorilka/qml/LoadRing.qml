import QtQuick
import QtQuick.Shapes

// Маленькое кольцо загрузки: дуга набирается по мере скачивания (fraction 0…1); если процент неизвестен
// (fraction < 0), дуга короткая и крутится. Появляется, пока качаются или загружаются модели.
Item {
    id: ring
    property real fraction: -1
    property color tint: Theme.accent
    property int size: 22
    readonly property bool known: fraction >= 0
    implicitWidth: size
    implicitHeight: size
    Accessible.role: Accessible.ProgressBar
    Accessible.name: known ? "Загрузка моделей: " + Math.round(fraction * 100) + " процентов" : "Загрузка моделей"

    Shape {
        anchors.fill: parent
        layer.enabled: true
        layer.samples: 4
        ShapePath {   // дорожка
            strokeColor: Theme.hairline
            strokeWidth: 2.5
            fillColor: "transparent"
            PathAngleArc { centerX: ring.size / 2; centerY: ring.size / 2; radiusX: ring.size / 2 - 2; radiusY: ring.size / 2 - 2; startAngle: 0; sweepAngle: 360 }
        }
        ShapePath {   // прогресс
            strokeColor: ring.tint
            strokeWidth: 2.5
            fillColor: "transparent"
            capStyle: ShapePath.RoundCap
            PathAngleArc {
                centerX: ring.size / 2; centerY: ring.size / 2
                radiusX: ring.size / 2 - 2; radiusY: ring.size / 2 - 2
                startAngle: -90
                sweepAngle: ring.known ? Math.max(8, 360 * ring.fraction) : 100
                Behavior on sweepAngle { NumberAnimation { duration: Theme.slow; easing.type: Easing.BezierSpline; easing.bezierCurve: Theme.easeOut } }
            }
        }
        rotation: 0
        RotationAnimator on rotation {
            running: !ring.known && ring.visible && !Theme.reduceMotion
            from: 0; to: 360; duration: 1100; loops: Animation.Infinite
        }
    }
}
