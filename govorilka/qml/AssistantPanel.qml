import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Window

// Подсказки в звонке: вопрос собеседника и ответ нейросети, который можно сказать вслух.
// Поверх всех окон, не забирает фокус у программы звонка. По умолчанию не видно при демонстрации экрана
// (govorilka/qt/capture_guard.py). Окно можно перетащить за шапку.
Window {
    id: panel
    objectName: "assistantPanel"
    readonly property string phase: backend.assistantPhase
    property bool live: false   // задаёт главное окно: идёт запись
    readonly property bool wanted: backend.settings.assistant_enabled === true && live && phase !== "" && !backend.assistantHidden
    width: 400
    height: Math.min(content.implicitHeight + 2, Screen.desktopAvailableHeight - 80)
    color: "transparent"
    flags: Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool | Qt.WindowDoesNotAcceptFocus | Qt.NoDropShadowWindowHint
    visible: wanted
    x: Screen.virtualX + Screen.desktopAvailableWidth - width - 24
    y: Screen.virtualY + 90
    title: "Говорилка — подсказки"
    transientParent: null
    onVisibleChanged: if (visible) protectLater.restart()
    Timer {   // окно уже на экране: прячем от захвата и поднимаем над полноэкранными окнами
        id: protectLater
        interval: 50
        onTriggered: {
            backend.protectWindow(panel, backend.settings.assistant_hide === true)
            backend.prepareOverlay(panel)
        }
    }
    Connections {
        target: backend
        function onSettingsChanged() { if (panel.visible) protectLater.restart() }
    }

    Rectangle {
        id: content
        width: parent.width
        implicitHeight: column.implicitHeight + 28
        radius: 18
        color: Theme.glass
        border.color: Qt.rgba(Theme.accent.r, Theme.accent.g, Theme.accent.b, 0.35)

        ColumnLayout {
            id: column
            anchors { left: parent.left; right: parent.right; top: parent.top; margins: 14 }
            spacing: 10

            RowLayout {   // шапка: за неё окно перетаскивается
                Layout.fillWidth: true
                spacing: 8
                Rectangle {
                    width: 26; height: 26; radius: 9
                    gradient: Gradient {
                        orientation: Gradient.Horizontal
                        GradientStop { position: 0; color: Theme.accentA }
                        GradientStop { position: 1; color: Theme.accentB }
                    }
                    Icon { anchors.centerIn: parent; name: "sparkle"; size: 15; color: "#FFFFFF" }
                }
                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 0
                    Text { text: "Подсказка"; font.pixelSize: 14; font.weight: Font.DemiBold; color: Theme.text }
                    Text {
                        Layout.fillWidth: true
                        elide: Text.ElideRight
                        text: backend.assistantProtection === "hidden" ? "Не видно при демонстрации экрана"
                              : backend.assistantProtection === "black" ? "При демонстрации — чёрный прямоугольник"
                              : backend.settings.assistant_hide === true ? "Не удалось скрыть от демонстрации экрана" : "Видно при демонстрации экрана"
                        font.pixelSize: 11
                        color: backend.assistantProtection === "hidden" ? Theme.success : Theme.pause
                    }
                }
                IconButton { iconName: "close"; size: 28; tip: "Спрятать подсказки до конца записи (вернуть — в пульте записи)"; onClicked: backend.setAssistantHidden(true) }
                DragHandler { target: null; onActiveChanged: if (active) panel.startSystemMove() }
            }

            Text {   // вопрос собеседника
                visible: backend.assistantQuestion !== ""
                Layout.fillWidth: true
                wrapMode: Text.WordWrap
                maximumLineCount: 3
                elide: Text.ElideRight
                text: "«" + backend.assistantQuestion + "»"
                font.pixelSize: 12
                font.italic: true
                color: Theme.textSecondary
            }

            RowLayout {   // загрузка, ожидание, ошибка
                visible: panel.phase === "loading" || panel.phase === "thinking" || panel.phase === "error"
                    || (panel.phase === "ready" && backend.assistantAnswer === "")
                Layout.fillWidth: true
                spacing: 8
                LoadRing { visible: panel.phase === "loading" || panel.phase === "thinking"; size: 18; fraction: -1 }
                Text {
                    Layout.fillWidth: true
                    wrapMode: Text.WordWrap
                    text: panel.phase === "thinking" ? "Думаю над ответом…" : backend.assistantNote
                    font.pixelSize: 13
                    color: panel.phase === "error" ? Theme.danger : Theme.textSecondary
                }
            }

            TextEdit {   // ответ: можно выделить и скопировать
                visible: backend.assistantAnswer !== ""
                Layout.fillWidth: true
                text: backend.assistantAnswer
                readOnly: true
                selectByMouse: true
                wrapMode: TextEdit.Wrap
                textFormat: TextEdit.MarkdownText
                font.pixelSize: 15
                color: Theme.text
                selectionColor: Qt.rgba(Theme.accent.r, Theme.accent.g, Theme.accent.b, 0.35)
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: 6
                PillButton {
                    text: "Ответить"
                    iconName: "sparkle"
                    implicitHeight: 30
                    enabled: panel.phase === "ready" || panel.phase === "answering" || panel.phase === "error"
                    tip: "Ответить на последнюю фразу собеседника, даже если она не похожа на вопрос"
                    onClicked: backend.assistantAnswerLast()
                }
                PillButton {
                    text: "Копировать"
                    iconName: "copy"
                    implicitHeight: 30
                    enabled: backend.assistantAnswer !== ""
                    onClicked: backend.copyAssistantAnswer()
                }
            }
        }
    }
}
