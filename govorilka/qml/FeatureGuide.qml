import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// «Что умеет Говорилка»: все возможности списком. После обновления открывается сам и показывает только то, что
// появилось с прошлого раза («Что нового в 0.3.3»); из настроек открывается целиком. Список живёт в govorilka/features.py:
// новая возможность — одна запись там, и она попадёт сюда сама.
Popup {
    id: guide
    objectName: "featureGuide"
    property string mode: "all"   // "new" — только новое после обновления, "all" — всё
    readonly property var items: mode === "new" ? backend.featuresNew : backend.featuresAll
    readonly property bool newOnly: mode === "new"

    function show(kind) {
        mode = kind === "new" ? "new" : "all"
        open()
    }

    parent: Overlay.overlay
    anchors.centerIn: parent
    width: Math.min(640, parent ? parent.width - 48 : 640)
    height: Math.min(640, parent ? parent.height - 48 : 640)
    modal: true
    focus: true
    padding: 0
    closePolicy: Popup.CloseOnEscape
    Overlay.modal: Rectangle { color: Qt.rgba(0, 0, 0, Theme.dark ? 0.6 : 0.3) }
    enter: Transition {
        ParallelAnimation {
            NumberAnimation { property: "opacity"; from: 0; to: 1; duration: Theme.slow; easing.type: Easing.BezierSpline; easing.bezierCurve: Theme.easeOut }
            NumberAnimation { property: "scale"; from: Theme.reduceMotion ? 1 : 0.96; to: 1; duration: Theme.slow; easing.type: Easing.BezierSpline; easing.bezierCurve: Theme.easeOut }
        }
    }
    exit: Transition { NumberAnimation { property: "opacity"; to: 0; duration: Theme.fast } }
    background: Bezel {}

    contentItem: ColumnLayout {
        spacing: 0
        RowLayout {
            Layout.fillWidth: true
            Layout.margins: 24
            Layout.bottomMargin: 10
            ColumnLayout {
                Layout.fillWidth: true
                spacing: 2
                Text {
                    Layout.fillWidth: true
                    text: guide.newOnly ? "Что нового в " + backend.guideVersion : "Что умеет Говорилка"
                    font.pixelSize: 24
                    font.weight: Font.Bold
                    font.letterSpacing: -0.5
                    color: Theme.text
                    wrapMode: Text.WordWrap
                }
                Text {
                    Layout.fillWidth: true
                    text: guide.newOnly ? "Говорилка обновилась. Вот что появилось с прошлого раза:"
                                        : "Все возможности. Что вышло недавно, помечено «Новое»."
                    font.pixelSize: 13
                    color: Theme.textSecondary
                    wrapMode: Text.WordWrap
                }
            }
            IconButton { iconName: "close"; size: 32; tip: "Закрыть (Esc)"; onClicked: guide.close() }
        }

        ListView {
            id: list
            Layout.fillWidth: true
            Layout.fillHeight: true
            Layout.leftMargin: 24
            Layout.rightMargin: 24
            clip: true
            spacing: 10
            model: guide.items
            bottomMargin: 8
            ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }
            delegate: Rectangle {
                id: card
                required property var modelData
                readonly property bool fresh: guide.newOnly || backend.guideVersion === modelData.since
                width: ListView.view.width - 10
                implicitHeight: cardRow.implicitHeight + 28
                height: implicitHeight
                radius: 16
                color: fresh ? Qt.rgba(Theme.accent.r, Theme.accent.g, Theme.accent.b, Theme.dark ? 0.10 : 0.07) : Theme.coreRaised
                border.color: fresh ? Qt.rgba(Theme.accent.r, Theme.accent.g, Theme.accent.b, 0.35) : Theme.shellLine
                RowLayout {
                    id: cardRow
                    anchors { left: parent.left; right: parent.right; verticalCenter: parent.verticalCenter; leftMargin: 16; rightMargin: 16 }
                    spacing: 14
                    Rectangle {
                        Layout.alignment: Qt.AlignTop
                        width: 40; height: 40; radius: 13
                        color: Qt.rgba(Theme.accent.r, Theme.accent.g, Theme.accent.b, Theme.dark ? 0.18 : 0.12)
                        Icon { anchors.centerIn: parent; name: card.modelData.icon; size: 20; color: Theme.accent }
                    }
                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 4
                        RowLayout {
                            spacing: 8
                            Text { text: card.modelData.title; font.pixelSize: 15; font.weight: Font.DemiBold; color: Theme.text; Layout.fillWidth: true; wrapMode: Text.WordWrap }
                            Rectangle {
                                visible: card.fresh && !guide.newOnly
                                implicitWidth: freshLabel.implicitWidth + 14
                                implicitHeight: 20
                                radius: 10
                                color: Qt.rgba(Theme.accent.r, Theme.accent.g, Theme.accent.b, 0.16)
                                Text { id: freshLabel; anchors.centerIn: parent; text: "Новое"; font.pixelSize: 11; font.weight: Font.DemiBold; color: Theme.accent }
                            }
                        }
                        Text {
                            Layout.fillWidth: true
                            text: card.modelData.body
                            textFormat: Text.StyledText
                            wrapMode: Text.WordWrap
                            font.pixelSize: 13
                            lineHeight: 1.3
                            color: Theme.textSecondary
                        }
                    }
                }
            }
        }

        RowLayout {
            Layout.fillWidth: true
            Layout.margins: 20
            Layout.topMargin: 8
            spacing: 8
            PillButton {
                visible: guide.newOnly
                text: "Все возможности"
                tip: "Показать полный список"
                onClicked: guide.mode = "all"
            }
            PillButton {
                visible: !guide.newOnly
                text: "Знакомство с горячей клавишей"
                iconName: "bolt"
                tip: "Пройти обучающие шаги заново"
                onClicked: { guide.close(); backend.showTour() }
            }
            Item { Layout.fillWidth: true }
            PillButton { text: "Понятно"; variant: "primary"; iconName: "check"; onClicked: guide.close() }
        }
    }
}
