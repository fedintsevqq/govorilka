import QtQuick
import QtQuick.Shapes

// Фон окна: глубокий цвет и два размытых светящихся пятна — ирис сверху слева, бирюза снизу справа.
// Пятна неподвижны: фон не должен отвлекать от текста.
Rectangle {
    id: backdrop
    color: Theme.bg

    component Orb: Shape {
        id: orb
        property color tint
        property real diameter: 760
        width: diameter
        height: diameter
        preferredRendererType: Shape.CurveRenderer
        ShapePath {
            strokeColor: "transparent"
            fillGradient: RadialGradient {
                centerX: orb.diameter / 2; centerY: orb.diameter / 2; centerRadius: orb.diameter / 2
                focalX: centerX; focalY: centerY
                GradientStop { position: 0.0; color: orb.tint }
                GradientStop { position: 0.55; color: Qt.rgba(orb.tint.r, orb.tint.g, orb.tint.b, orb.tint.a * 0.35) }
                GradientStop { position: 1.0; color: "transparent" }
            }
            PathAngleArc {
                centerX: orb.diameter / 2; centerY: orb.diameter / 2
                radiusX: orb.diameter / 2; radiusY: orb.diameter / 2
                startAngle: 0; sweepAngle: 360
            }
        }
    }

    Orb { tint: Theme.orbA; x: -diameter * 0.35; y: -diameter * 0.45 }
    Orb { tint: Theme.orbB; diameter: 900; x: backdrop.width - diameter * 0.55; y: backdrop.height - diameter * 0.5 }
}
