import QtQuick
import QtQuick.Window
import QtQuick.Shapes
import QtQuick.Effects

// Круговое меню у курсора по горячей клавише.
// Выбор по направлению, как в пирог-меню: достаточно повести мышь в сторону сектора, попадать в иконку
// не нужно. Жест одним движением: зажали клавишу → повели мышь → отпустили. Можно кликнуть, можно цифрами 1–6.
// Появление 150 мс с подъёмом секторов по очереди (шаг 20 мс), уход — 100 мс. Меню вызывают часто,
// поэтому анимация короткая и не мешает: нажимать можно сразу.
Window {
    id: menu
    width: 440
    height: 440
    flags: Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool | Qt.NoDropShadowWindowHint
    color: "transparent"
    title: "Говорилка — меню"
    objectName: "radial"
    transientParent: null   // самостоятельное окно: иначе оно скрыто, пока скрыто главное

    readonly property string phase: backend.state
    readonly property bool live: phase === "recording" || phase === "paused"
    readonly property real ringRadius: 128
    readonly property real deadZone: 46      // в центре — «ничего не выбрано»
    property int hovered: -1
    property bool practice: false             // тренировка в знакомстве: в центре подсказка, что делать
    property bool closing: false
    property real openedAt: 0
    property real reveal: 0                   // 0 → 1 при появлении

    // действия по часовой стрелке, первое — сверху
    readonly property var actions: live ? [
        { id: "stop", icon: "stop", label: "Стоп", tint: Theme.record },
        { id: "pause", icon: phase === "paused" ? "play" : "pause", label: phase === "paused" ? "Продолжить" : "Пауза", tint: Theme.pause },
        { id: "mark", icon: "star", label: "Отметка", tint: Theme.mark },
        { id: "claude", icon: "sparkle", label: backend.aiTarget.short, tint: Theme.accentA },
        { id: "transcript", icon: "file", label: "Транскрипт", tint: Theme.accentB },
        { id: "window", icon: "window", label: "Окно", tint: Theme.textSecondary }
    ] : [
        { id: "record", icon: "record", label: "Запись", tint: Theme.record },
        { id: "claude", icon: "sparkle", label: backend.aiTarget.short, tint: Theme.accentA },
        { id: "transcript", icon: "file", label: "Транскрипт", tint: Theme.accentB },
        { id: "settings", icon: "settings", label: "Настройки", tint: Theme.textSecondary },
        { id: "window", icon: "window", label: "Окно", tint: Theme.textSecondary },
        { id: "mark", icon: "star", label: "Отметка", tint: Theme.mark, disabled: true }
    ]
    readonly property real step: 360 / actions.length

    function popupAt(px, py) {
        const screen = Qt.application.screens.find(s => px >= s.virtualX && px < s.virtualX + s.width
                                                         && py >= s.virtualY && py < s.virtualY + s.height)
                       || Qt.application.screens[0]
        x = Math.max(screen.virtualX, Math.min(px - width / 2, screen.virtualX + screen.width - width))
        y = Math.max(screen.virtualY, Math.min(py - height / 2, screen.virtualY + screen.height - height))
        hovered = -1
        closing = false
        openedAt = Date.now()
        reveal = 0
        show()
        raise()
        requestActivate()
        appear.restart()
    }
    function dismiss() {
        if (!visible || closing) return
        closing = true
        disappear.restart()
    }
    function activate(index) {
        if (index < 0 || index >= actions.length || actions[index].disabled) return
        const id = actions[index].id
        dismiss()
        Qt.callLater(() => backend.radialAction(id))
    }
    // жест «зажал → повёл → отпустил»: отпускание над сектором выбирает его; без выбора меню остаётся для клика
    function releaseHotkey() {
        if (visible && !closing && hovered >= 0) activate(hovered)
    }
    function sectorAt(px, py) {
        // математика секторов одна на всех — в Python (там она и проверена тестами)
        return backend.radialSector(px - width / 2, py - height / 2, actions.length, deadZone)
    }

    NumberAnimation { id: appear; target: menu; property: "reveal"; from: 0; to: 1; duration: Theme.reduceMotion ? 120 : 150 + 20 * menu.actions.length; easing.type: Easing.Linear }
    SequentialAnimation {
        id: disappear
        NumberAnimation { target: disc; property: "opacity"; to: 0; duration: 100; easing.type: Easing.BezierSpline; easing.bezierCurve: Theme.easeOut }
        ScriptAction { script: { menu.hide(); disc.opacity = 1 } }
    }

    // кликнули мимо — меню уходит. Но ОС отдаёт фокус новому окну с задержкой, и без паузы меню могло
    // закрыться само, не дав нажать клавишу.
    Timer { id: lostFocus; interval: 180; onTriggered: if (!menu.active && menu.visible) menu.dismiss() }
    onActiveChanged: if (!active && visible) lostFocus.restart()

    Item {
        id: disc
        anchors.centerIn: parent
        width: 372
        height: 372
        focus: true
        Accessible.role: Accessible.PopupMenu
        Accessible.name: "Меню Говорилки"
        // общий выход: масштаб от 0.9 и прозрачность — «ниоткуда» ничего не появляется
        scale: Theme.reduceMotion ? 1 : 0.9 + 0.1 * Math.min(1, menu.reveal * 1.6)
        opacity: Math.min(1, menu.reveal * 2.2)

        Keys.onEscapePressed: menu.dismiss()
        Keys.onReturnPressed: menu.activate(menu.hovered)
        Keys.onEnterPressed: menu.activate(menu.hovered)
        Keys.onPressed: (event) => {
            const n = event.key - Qt.Key_1
            if (n >= 0 && n < menu.actions.length) { menu.activate(n); event.accepted = true }
            else if (event.key === Qt.Key_Right || event.key === Qt.Key_Down) { menu.hovered = (menu.hovered + 1) % menu.actions.length; event.accepted = true }
            else if (event.key === Qt.Key_Left || event.key === Qt.Key_Up) { menu.hovered = (menu.hovered + menu.actions.length - 1) % menu.actions.length; event.accepted = true }
        }

        // стеклянная плита с мягкой тенью
        Rectangle {
            id: plate
            anchors.fill: parent
            radius: width / 2
            color: Theme.glass
            border.color: Theme.shellLine
            border.width: 1
            layer.enabled: true
            layer.effect: MultiEffect {
                shadowEnabled: true
                shadowColor: Theme.shadow
                shadowBlur: 1.0
                shadowVerticalOffset: 10
            }
        }
        Rectangle {   // внутреннее кольцо — глубина
            anchors.centerIn: parent
            width: 2 * (menu.ringRadius + 38); height: width; radius: width / 2
            color: "transparent"
            border.color: Theme.hairline
        }

        // клин подсветки, плавно переезжает к выбранному сектору
        Shape {
            id: wedge
            anchors.fill: parent
            preferredRendererType: Shape.CurveRenderer
            opacity: menu.hovered >= 0 ? 1 : 0
            Behavior on opacity { NumberAnimation { duration: Theme.fast } }
            rotation: menu.hovered >= 0 ? menu.hovered * menu.step : rotation
            Behavior on rotation { RotationAnimation { duration: Theme.reduceMotion ? 0 : Theme.base; direction: RotationAnimation.Shortest; easing.type: Easing.BezierSpline; easing.bezierCurve: Theme.easeOut } }
            readonly property color tint: menu.hovered >= 0 ? menu.actions[menu.hovered].tint : Theme.accent
            ShapePath {
                strokeWidth: 84
                strokeColor: Qt.rgba(wedge.tint.r, wedge.tint.g, wedge.tint.b, Theme.dark ? 0.16 : 0.12)
                fillColor: "transparent"
                capStyle: ShapePath.RoundCap
                PathAngleArc {
                    centerX: disc.width / 2; centerY: disc.height / 2
                    radiusX: menu.ringRadius; radiusY: menu.ringRadius
                    startAngle: -90 - menu.step / 2 + 9; sweepAngle: menu.step - 18
                }
            }
        }

        // секторы
        Repeater {
            model: menu.actions
            delegate: Item {
                id: sector
                required property var modelData
                required property int index
                readonly property real angle: (index * menu.step - 90) * Math.PI / 180
                readonly property bool active: menu.hovered === index
                readonly property real appearAt: index * 20 / (150 + 20 * menu.actions.length)
                readonly property real t: Math.max(0, Math.min(1, (menu.reveal - appearAt) / 0.55))
                width: 96; height: 76
                Accessible.role: Accessible.MenuItem
                Accessible.name: modelData.label + (modelData.disabled ? " — недоступно" : "")
                x: disc.width / 2 + Math.cos(angle) * menu.ringRadius - width / 2
                y: disc.height / 2 + Math.sin(angle) * menu.ringRadius - height / 2
                opacity: (modelData.disabled ? 0.5 : 1) * t
                scale: Theme.reduceMotion ? 1 : (0.86 + 0.14 * t) * (active ? 1.06 : 1)
                Behavior on scale { enabled: menu.reveal >= 1; NumberAnimation { duration: Theme.fast; easing.type: Easing.BezierSpline; easing.bezierCurve: Theme.easeOut } }

                Rectangle {
                    id: badge
                    anchors.horizontalCenter: parent.horizontalCenter
                    y: 2
                    width: 46; height: 46; radius: 23
                    color: sector.active ? Qt.rgba(sector.modelData.tint.r, sector.modelData.tint.g, sector.modelData.tint.b, 0.22) : Theme.core
                    border.color: sector.active ? Qt.rgba(sector.modelData.tint.r, sector.modelData.tint.g, sector.modelData.tint.b, 0.55) : Theme.shellLine
                    Behavior on color { ColorAnimation { duration: Theme.fast } }
                    Icon {
                        anchors.centerIn: parent
                        name: sector.modelData.icon
                        size: 22
                        color: sector.active ? sector.modelData.tint : Theme.text
                    }
                }
                Text {
                    anchors.horizontalCenter: parent.horizontalCenter
                    anchors.top: badge.bottom
                    anchors.topMargin: 5
                    text: sector.modelData.label
                    font.pixelSize: 12
                    font.weight: sector.active ? Font.DemiBold : Font.Medium
                    color: sector.active ? Theme.text : Theme.textSecondary
                }
                Text {   // номер для клавиатуры
                    anchors.left: badge.right
                    anchors.leftMargin: -4
                    anchors.top: badge.top
                    anchors.topMargin: -4
                    text: sector.index + 1
                    font.pixelSize: 11
                    font.weight: Font.DemiBold
                    color: Theme.textSecondary
                }
            }
        }

        // центр: состояние, время, живое кольцо уровня звука
        Rectangle {
            anchors.centerIn: parent
            width: 92; height: 92; radius: 46
            color: Theme.coreRaised
            border.color: Theme.shellLine
            Rectangle {
                anchors.centerIn: parent
                width: parent.width + 10; height: width; radius: width / 2
                color: "transparent"
                border.width: 2
                border.color: menu.phase === "paused" ? Theme.pause : Theme.record
                visible: menu.live
                opacity: 0.25 + Math.max(backend.micLevel, backend.systemLevel) * 0.75
                scale: 1 + Math.max(backend.micLevel, backend.systemLevel) * 0.12
            }
            Column {
                anchors.centerIn: parent
                spacing: 1
                Text {
                    anchors.horizontalCenter: parent.horizontalCenter
                    width: 80   // центр меню маленький: длинная подпись переносится, а не вылезает на кольцо
                    horizontalAlignment: Text.AlignHCenter
                    wrapMode: Text.WordWrap
                    text: menu.hovered >= 0 ? (menu.actions[menu.hovered].disabled ? menu.actions[menu.hovered].label + "
нет записи" : menu.actions[menu.hovered].label)
                        : menu.practice ? "Тренировка" : menu.live ? (menu.phase === "paused" ? "Пауза" : "Запись") : "Говорилка"
                    font.pixelSize: menu.hovered >= 0 ? 13 : 12
                    font.weight: Font.DemiBold
                    color: menu.hovered >= 0 ? Theme.text : Theme.textSecondary
                }
                Text {
                    anchors.horizontalCenter: parent.horizontalCenter
                    visible: menu.practice && menu.hovered < 0
                    text: "ведите мышь
к кружку"
                    horizontalAlignment: Text.AlignHCenter
                    font.pixelSize: 11
                    color: Theme.textTertiary
                }
                Text {
                    anchors.horizontalCenter: parent.horizontalCenter
                    visible: menu.live && !menu.practice
                    text: backend.elapsed
                    font.pixelSize: 15
                    font.weight: Font.DemiBold
                    font.features: { "tnum": 1 }
                    color: menu.phase === "paused" ? Theme.pause : Theme.record
                }
            }
        }

        MouseArea {
            anchors.fill: parent
            hoverEnabled: true
            onPositionChanged: (mouse) => menu.hovered = menu.sectorAt(mouse.x + disc.x, mouse.y + disc.y)
            onExited: menu.hovered = -1
            onPressed: (mouse) => menu.hovered = menu.sectorAt(mouse.x + disc.x, mouse.y + disc.y)   // отклик на нажатие
            onReleased: (mouse) => {
                const index = menu.sectorAt(mouse.x + disc.x, mouse.y + disc.y)
                if (index >= 0) menu.activate(index); else menu.dismiss()
            }
        }
    }
    // клик в прозрачный угол окна — тоже «мимо»
    MouseArea {
        anchors.fill: parent
        z: -1
        onClicked: menu.dismiss()
    }
}
