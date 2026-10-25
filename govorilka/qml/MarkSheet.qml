import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// «Что здесь было важного?»: подписать отметку своими словами. Текст попадёт в ленту и в файл транскрипта.
Popup {
    id: sheet
    property int row: -1

    function ask(index, text) {
        row = index
        field.text = text === "Отметка" ? "" : text
        open()
    }

    // пришёл другой транскрипт: номер строки мог смениться — не правим вслепую
    Connections { target: backend; function onTranscriptInfoChanged() { if (sheet.opened) sheet.close() } }

    parent: Overlay.overlay
    anchors.centerIn: parent
    width: 440
    modal: true
    focus: true
    padding: 0
    Overlay.modal: Rectangle { color: Qt.rgba(0, 0, 0, Theme.dark ? 0.5 : 0.22) }
    enter: Transition {
        ParallelAnimation {
            NumberAnimation { property: "opacity"; from: 0; to: 1; duration: Theme.base; easing.type: Easing.BezierSpline; easing.bezierCurve: Theme.easeOut }
            NumberAnimation { property: "scale"; from: Theme.reduceMotion ? 1 : 0.96; to: 1; duration: Theme.base; easing.type: Easing.BezierSpline; easing.bezierCurve: Theme.easeOut }
        }
    }
    exit: Transition { NumberAnimation { property: "opacity"; to: 0; duration: Theme.fast } }
    onOpened: { field.forceActiveFocus(); field.selectAll() }

    function commit() {
        if (row >= 0) backend.editMark(row, field.text)
        close()
    }

    background: Bezel {}
    contentItem: ColumnLayout {
        spacing: 12
        Item { Layout.preferredHeight: 14 }
        RowLayout {
            Layout.leftMargin: 24
            Layout.rightMargin: 24
            spacing: 12
            Rectangle {
                width: 38; height: 38; radius: 19
                color: Theme.markSoft
                border.color: Qt.rgba(Theme.mark.r, Theme.mark.g, Theme.mark.b, 0.4)
                Icon { anchors.centerIn: parent; name: "star"; size: 18; color: Theme.mark }
            }
            ColumnLayout {
                spacing: 1
                Text { text: "Подпись отметки"; font.pixelSize: 17; font.weight: Font.DemiBold; color: Theme.text }
                Text { text: "Что здесь было важного?"; font.pixelSize: 12; color: Theme.textTertiary }
            }
        }
        Rectangle {
            Layout.fillWidth: true
            Layout.leftMargin: 24
            Layout.rightMargin: 24
            implicitHeight: 44
            radius: 14
            color: Theme.core
            border.color: field.activeFocus ? Qt.rgba(Theme.accent.r, Theme.accent.g, Theme.accent.b, 0.6) : Theme.shellLine
            TextField {
                id: field
                anchors.fill: parent
                leftPadding: 14
                color: Theme.text
                placeholderText: "Например: договорились про срок"
                placeholderTextColor: Theme.textTertiary
                font.pixelSize: 14
                selectByMouse: true
                background: Item {}
                Accessible.name: "Подпись отметки"
                onAccepted: sheet.commit()
            }
        }
        Text {
            Layout.fillWidth: true
            Layout.leftMargin: 24
            Layout.rightMargin: 24
            wrapMode: Text.WordWrap
            text: "Подпись видна в ленте, в файле транскрипта и в запросе нейросети. Пустая подпись вернёт «Отметка»."
            font.pixelSize: 12
            lineHeight: 1.25
            color: Theme.textTertiary
        }
        RowLayout {
            Layout.fillWidth: true
            Layout.leftMargin: 20
            Layout.rightMargin: 20
            Layout.topMargin: 4
            spacing: 8
            PillButton { text: "Убрать отметку"; tip: "Удалить эту отметку из текста и из файла"; onClicked: { backend.deleteMark(sheet.row); sheet.close() } }
            Item { Layout.fillWidth: true }
            PillButton { text: "Отмена"; tip: "Закрыть, ничего не меняется"; onClicked: sheet.close() }
            PillButton { variant: "primary"; text: "Сохранить"; tip: "Сохранить подпись (Enter)"; iconName: "check"; onClicked: sheet.commit() }
        }
        Item { Layout.preferredHeight: 14 }
    }
}
