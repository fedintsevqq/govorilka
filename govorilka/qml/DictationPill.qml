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

            Row {   // голос: полоски прыгают в такт речи
                id: bars
                visible: pill.phase === "listening"
                anchors.verticalCenter: parent.verticalCenter
                spacing: 3
                rightPadding: 6
                Repeater {
                    model: [0.45, 0.8, 1.0, 0.7, 0.9, 0.55, 0.35]
                    Rectangle {
                        required property real modelData
                        required property int index
                        anchors.verticalCenter: parent.verticalCenter
                        width: 3; radius: 1.5
                        // ровная тишина — короткие точки; голос — высокие полоски с разной высотой
                        height: Math.max(4, 24 * Math.min(1, backend.dictationLevel) * modelData)
                        color: Theme.accent
                        opacity: 0.55 + 0.45 * modelData
                        Behavior on height { NumberAnimation { duration: 90; easing.type: Easing.OutQuad } }
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
