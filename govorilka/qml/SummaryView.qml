import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// Вкладка «Итоги»: краткое содержание, решения, задачи и открытые вопросы созвона. Считает локальная модель
// на этом компьютере (без интернета, после того как модель скачана один раз).
Item {
    id: root
    readonly property var model: backend.summaryModel
    readonly property bool hasText: backend.summaryText !== ""
    signal openSettings()

    function sizeLabel(mb) { return mb >= 1000 ? (mb / 1024).toFixed(1).replace(".", ",") + " ГБ" : mb + " МБ" }

    // ----- итоги готовы -----
    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 6
        visible: root.hasText && !backend.summaryBusy
        spacing: 0
        ScrollView {
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            contentWidth: availableWidth
            TextEdit {
                width: Math.min(parent.width - 48, 900)
                x: 24
                topPadding: 16
                bottomPadding: 16
                textFormat: TextEdit.MarkdownText
                text: backend.summaryText
                readOnly: true
                selectByMouse: true
                wrapMode: TextEdit.Wrap
                font.pixelSize: Theme.fontBody
                color: Theme.text
                selectionColor: Qt.rgba(Theme.accent.r, Theme.accent.g, Theme.accent.b, 0.35)
                selectedTextColor: Theme.text
            }
        }
        RowLayout {
            Layout.fillWidth: true
            Layout.margins: 12
            spacing: 8
            PillButton { text: "Копировать"; iconName: "copy"; onClicked: backend.copySummary() }
            PillButton { text: "Сделать заново"; iconName: "sparkle"; tip: "Посчитать итоги ещё раз (например, после того как подписали людей)"; onClicked: backend.makeSummary() }
            Item { Layout.fillWidth: true }
            Text { text: "Модель: " + root.model.title; font.pixelSize: 12; color: Theme.textTertiary }
        }
    }

    // ----- считаем -----
    Column {
        anchors.centerIn: parent
        visible: backend.summaryBusy
        spacing: 14
        width: Math.min(parent.width - 48, 420)
        LoadRing { anchors.horizontalCenter: parent.horizontalCenter; size: 40; fraction: backend.summaryFraction }
        Text {
            width: parent.width
            horizontalAlignment: Text.AlignHCenter
            wrapMode: Text.WordWrap
            text: backend.summaryStatus || "Готовлю…"
            font.pixelSize: 15
            font.weight: Font.Medium
            color: Theme.text
        }
        Text {
            width: parent.width
            horizontalAlignment: Text.AlignHCenter
            wrapMode: Text.WordWrap
            text: "Всё считается на этом компьютере, разговор никуда не уходит. Часовой созвон — несколько минут: можно свернуть окно."
            font.pixelSize: 12
            lineHeight: 1.2
            color: Theme.textTertiary
        }
        PillButton { anchors.horizontalCenter: parent.horizontalCenter; text: "Отменить"; onClicked: backend.cancelSummary() }
    }

    // ----- ещё не считали -----
    Column {
        anchors.centerIn: parent
        visible: !root.hasText && !backend.summaryBusy
        spacing: 14
        width: Math.min(parent.width - 48, 440)
        Rectangle {
            anchors.horizontalCenter: parent.horizontalCenter
            width: 64; height: 64; radius: 32
            color: Qt.rgba(Theme.accent.r, Theme.accent.g, Theme.accent.b, 0.14)
            Icon { anchors.centerIn: parent; name: "sparkle"; size: 28; color: Theme.accent }
        }
        Text {
            width: parent.width
            horizontalAlignment: Text.AlignHCenter
            text: "Итоги созвона"
            font.pixelSize: 18
            font.weight: Font.DemiBold
            color: Theme.text
        }
        Text {
            width: parent.width
            horizontalAlignment: Text.AlignHCenter
            wrapMode: Text.WordWrap
            text: "Кратко о чём говорили, принятые решения, задачи с ответственными и сроками, открытые вопросы. Без интернета: считает модель на вашем компьютере."
            font.pixelSize: 13
            lineHeight: 1.25
            color: Theme.textSecondary
        }
        Text {
            width: parent.width
            horizontalAlignment: Text.AlignHCenter
            wrapMode: Text.WordWrap
            visible: root.model.downloaded !== true
            text: "Модель " + root.model.title + " (" + root.sizeLabel(root.model.sizeMb) + ") скачается один раз при первом нажатии."
            font.pixelSize: 12
            color: Theme.textTertiary
        }
        Text {
            width: parent.width
            horizontalAlignment: Text.AlignHCenter
            wrapMode: Text.WordWrap
            visible: root.model.note !== ""
            text: root.model.note
            font.pixelSize: 12
            color: root.model.fit === "no" ? Theme.danger : Theme.pause
        }
        Row {
            anchors.horizontalCenter: parent.horizontalCenter
            spacing: 8
            PillButton {
                text: "Сделать итоги"
                iconName: "sparkle"
                variant: "primary"
                enabled: root.model.fit !== "no" && backend.transcriptPath !== ""
                tip: backend.transcriptPath === "" ? "Сначала запишите созвон или откройте файл" : "Посчитать итоги этого разговора"
                onClicked: backend.makeSummary()
            }
            PillButton { text: "Выбрать модель"; tip: "Модели итогов: полегче или поточнее"; onClicked: root.openSettings() }
        }
    }
}
