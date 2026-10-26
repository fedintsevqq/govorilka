import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// Карточка модели в каталоге настроек: название, зачем она, языки, точность и скорость, размер;
// справа — «Скачать» (с процентом), «Скачана» и корзина. Нажатие на карточку выбирает модель.
AbstractButton {
    id: card
    property var info: ({})
    property bool selected: false
    property bool downloading: false
    property real fraction: 0
    property bool busy: false   // качается другая модель: вторую параллельно не начинаем
    property bool cancellable: false   // загрузку можно прервать крестиком
    readonly property bool blocked: info.fit === "no"   // компьютеру не по силам
    signal choose()
    signal download()
    signal remove()
    signal cancel()
    Layout.fillWidth: true
    implicitHeight: body.implicitHeight + 24
    hoverEnabled: true
    focusPolicy: Qt.TabFocus
    Accessible.name: info.title + (selected ? ", выбрана" : "")
    onClicked: if (!card.blocked) card.choose()
    opacity: blocked ? 0.6 : 1

    background: Rectangle {
        radius: 14
        color: card.selected ? Qt.rgba(Theme.accent.r, Theme.accent.g, Theme.accent.b, Theme.dark ? 0.12 : 0.08)
                             : card.hovered ? Theme.hover : Theme.core
        border.color: card.selected ? Qt.rgba(Theme.accent.r, Theme.accent.g, Theme.accent.b, 0.55)
                                    : card.visualFocus ? Theme.accent : Theme.shellLine
        border.width: card.visualFocus ? 2 : 1
    }

    component Dots: Row {   // оценка от 1 до 5 точками
        property int value: 0
        spacing: 3
        Repeater {
            model: 5
            Rectangle {
                required property int index
                width: 6; height: 6; radius: 3
                anchors.verticalCenter: parent.verticalCenter
                color: index < parent.value ? Theme.accent : Theme.hairline
            }
        }
    }
    component Badge: Rectangle {
        property alias text: label.text
        property color tint: Theme.textSecondary
        implicitWidth: label.implicitWidth + 14
        implicitHeight: 20
        radius: 10
        color: Qt.rgba(tint.r, tint.g, tint.b, 0.12)
        Text { id: label; anchors.centerIn: parent; font.pixelSize: 11; font.weight: Font.DemiBold; color: parent.tint }
    }

    contentItem: Item {
        RowLayout {
            id: body
            anchors { left: parent.left; right: parent.right; verticalCenter: parent.verticalCenter; leftMargin: 4; rightMargin: 4 }
            spacing: 12

            Rectangle {   // выбор
                Layout.alignment: Qt.AlignTop
                Layout.topMargin: 2
                width: 18; height: 18; radius: 9
                color: "transparent"
                border.color: card.selected ? Theme.accent : Theme.textTertiary
                border.width: 2
                Rectangle { anchors.centerIn: parent; width: 8; height: 8; radius: 4; color: Theme.accent; visible: card.selected }
            }

            ColumnLayout {
                Layout.fillWidth: true
                spacing: 5
                Flow {
                    Layout.fillWidth: true
                    spacing: 6
                    Text { text: card.info.title; font.pixelSize: 14; font.weight: Font.DemiBold; color: Theme.text; height: 20; verticalAlignment: Text.AlignVCenter }
                    Badge { visible: card.info.recommended === true; text: "Рекомендуем"; tint: Theme.accent }
                    Badge { visible: card.info.punctuation === true; text: "С пунктуацией"; tint: Theme.success }
                    Badge { visible: card.info.languages !== undefined && card.info.languages !== "русский"; text: card.info.languages || ""; tint: Theme.me }
                    Badge { visible: card.info.license !== undefined; text: card.info.license || ""; tint: Theme.textSecondary }
                }
                Text {
                    Layout.fillWidth: true
                    wrapMode: Text.WordWrap
                    text: card.info.blurb
                    font.pixelSize: 12
                    color: Theme.textSecondary
                }
                Text {   // хватит ли памяти этому компьютеру
                    visible: (card.info.note || "") !== ""
                    Layout.fillWidth: true
                    wrapMode: Text.WordWrap
                    text: card.info.note || ""
                    font.pixelSize: 12
                    color: card.blocked ? Theme.danger : Theme.pause
                }
                Row {
                    visible: card.info.accuracy !== undefined
                    spacing: 14
                    Row {
                        spacing: 6
                        Text { text: "Точность " + card.info.accuracy + "%"; font.pixelSize: 11; color: Theme.textTertiary; anchors.verticalCenter: parent.verticalCenter }
                    }
                    Row {
                        spacing: 6
                        Text { text: "Скорость"; font.pixelSize: 11; color: Theme.textTertiary; anchors.verticalCenter: parent.verticalCenter }
                        Dots { value: card.info.speed || 0; anchors.verticalCenter: parent.verticalCenter }
                    }
                }
            }

            ColumnLayout {   // размер и скачивание
                Layout.alignment: Qt.AlignVCenter
                spacing: 4
                Text {
                    Layout.alignment: Qt.AlignRight
                    text: card.info.sizeMb >= 1000 ? (card.info.sizeMb / 1024).toFixed(1).replace(".", ",") + " ГБ" : card.info.sizeMb + " МБ"
                    font.pixelSize: 12
                    font.features: { "tnum": 1 }
                    color: Theme.textSecondary
                }
                RowLayout {
                    Layout.alignment: Qt.AlignRight
                    spacing: 4
                    LoadRing { visible: card.downloading; fraction: card.fraction; size: 18 }
                    Text {
                        visible: card.downloading
                        text: Math.round(card.fraction * 100) + "%"
                        font.pixelSize: 12
                        font.features: { "tnum": 1 }
                        color: Theme.textSecondary
                    }
                    IconButton {
                        visible: card.downloading && card.cancellable
                        iconName: "close"
                        size: 28
                        tip: "Отменить загрузку. Скачанное останется на диске: следующая загрузка продолжится с того же места"
                        onClicked: card.cancel()
                    }
                    PillButton {
                        visible: !card.downloading && card.info.downloaded !== true
                        text: "Скачать"
                        implicitHeight: 30
                        enabled: !card.busy && !card.blocked
                        tip: "Скачать заранее, чтобы потом не ждать"
                        onClicked: card.download()
                    }
                    Text {
                        visible: !card.downloading && card.info.downloaded === true
                        text: "Скачана"
                        font.pixelSize: 12
                        color: Theme.success
                    }
                    IconButton {
                        visible: !card.downloading && card.info.downloaded === true && !card.selected
                        iconName: "trash"
                        size: 28
                        tip: "Удалить с диска и освободить место"
                        onClicked: card.remove()
                    }
                }
            }
        }
    }
}
