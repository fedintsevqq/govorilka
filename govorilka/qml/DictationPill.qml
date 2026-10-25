import QtQuick
import QtQuick.Window

// Плашка диктовки внизу экрана, как у Handy: знак Говорилки и живые полоски голоса, пока вы говорите;
// кольцо, пока текст распознаётся. Поверх всех окон, не берёт фокус и пропускает клики: курсор остаётся
// в том поле, куда вставится текст.
Window {
    id: pill
    readonly property string phase: backend.dictationPhase
    readonly property string note: backend.dictationNote
    readonly property bool shown: phase !== "" || note !== ""
    width: content.width + 24    // прозрачный запас: окно не обрезает края плашки (клики всё равно проходят насквозь)
    height: content.height + 24
    color: "transparent"
    flags: Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool | Qt.WindowDoesNotAcceptFocus
           | Qt.WindowTransparentForInput | Qt.NoDropShadowWindowHint
    visible: shown
    x: Screen.virtualX + (Screen.desktopAvailableWidth - width) / 2
    y: Screen.virtualY + Screen.desktopAvailableHeight - height - 48
    title: "Говорилка — диктовка"
    objectName: "dictationPill"
    onVisibleChanged: if (visible) backend.prepareOverlay(pill)   // macOS: видна и над полноэкранными окнами
    transientParent: null

    Rectangle {
        id: content
        anchors.centerIn: parent
        height: Math.max(44, row.implicitHeight + 14)
        width: row.implicitWidth + 16   // без анимации ширины: окно плашки за ней не успевает и обрезает подпись
        radius: Math.min(22, height / 2)
        color: Theme.glass
        border.color: Qt.rgba(1, 1, 1, Theme.dark ? 0.10 : 0)

        Row {
            id: row
            anchors.verticalCenter: parent.verticalCenter
            x: 8
            spacing: 10

            Rectangle {   // знак Говорилки
                width: 30; height: 30; radius: 10
                anchors.verticalCenter: parent.verticalCenter
                gradient: Gradient {
                    orientation: Gradient.Horizontal
                    GradientStop { position: 0; color: Theme.accentA }
                    GradientStop { position: 1; color: Theme.accentB }
                }
                Icon { anchors.centerIn: parent; name: "mic"; size: 17; color: "#FFFFFF" }
            }

            // Голос: волна бежит справа налево. Каждые 50 мс в правый край добавляется текущий уровень голоса,
            // остальные полоски сдвигаются: видно, как звук «течёт». Тишина — ровные точки, голос — высокая волна.
            Row {
                id: bars
                visible: pill.phase === "listening"
                anchors.verticalCenter: parent.verticalCenter
                spacing: 3
                rightPadding: 6
                property var history: new Array(18).fill(0)
                property real smooth: 0
                Timer {
                    interval: 50
                    repeat: true
                    running: bars.visible
                    onTriggered: {
                        const level = Math.min(1, backend.dictationLevel * 1.15)
                        // быстро вверх на звуке, плавно вниз на паузе: волна не дёргается
                        bars.smooth = Math.max(level, bars.smooth * 0.82)
                        // небольшая живость, чтобы ровный звук не рисовал забор
                        const wobble = 0.82 + 0.18 * Math.sin(Date.now() / 90)
                        const next = bars.history.slice(1)
                        next.push(bars.smooth * wobble)
                        bars.history = next
                    }
                    onRunningChanged: if (!running) { bars.history = new Array(18).fill(0); bars.smooth = 0 }
                }
                Repeater {
                    model: 18
                    Rectangle {
                        required property int index
                        readonly property real v: bars.history[index] || 0
                        // середина волны чуть выше краёв: получается «язычок», а не прямоугольник
                        readonly property real shape: 0.55 + 0.45 * Math.sin(Math.PI * (index + 1) / 19)
                        anchors.verticalCenter: parent.verticalCenter
                        width: 3; radius: 1.5
                        height: Math.max(4, 26 * v * shape)
                        color: Theme.accent
                        opacity: 0.4 + 0.6 * Math.min(1, v * 1.6)
                        Behavior on height { NumberAnimation { duration: 60; easing.type: Easing.OutQuad } }
                    }
                }
            }

            LoadRing {
                visible: pill.phase === "recognizing" || pill.phase === "loading"
                anchors.verticalCenter: parent.verticalCenter
                fraction: pill.phase === "loading" ? backend.loadFraction : -1
                size: 20
            }
            Text {
                visible: pill.phase !== "listening"
                anchors.verticalCenter: parent.verticalCenter
                rightPadding: 8
                text: pill.note !== "" ? pill.note
                      : pill.phase === "loading" ? (backend.loadFraction >= 0 ? "Скачиваю модель " + Math.round(backend.loadFraction * 100) + "%" : "Загружаю модель…")
                      : "Распознаю…"
                font.pixelSize: 13
                font.weight: Font.Medium
                color: Theme.text
                width: Math.min(implicitWidth, 420)
                wrapMode: Text.WordWrap
                lineHeight: 1.15
            }
        }
    }
}
