import QtQuick
import QtQuick.Controls

// Подсказка при наведении в стиле Говорилки: стеклянная плашка, появляется через полсекунды, растворяется.
// Ставится внутрь кнопки: Tip { text: "…"; shown: control.hovered }
ToolTip {
    id: tip
    property bool shown: false
    visible: shown && text !== ""
    delay: 500
    timeout: 8000
    padding: 0
    margins: 8
    enter: Transition { NumberAnimation { property: "opacity"; from: 0; to: 1; duration: Theme.base; easing.type: Easing.BezierSpline; easing.bezierCurve: Theme.easeOut } }
    exit: Transition { NumberAnimation { property: "opacity"; to: 0; duration: Theme.fast } }
    background: Rectangle {
        radius: 10
        color: Theme.dark ? "#1C1F28" : "#22252A"
        border.color: Theme.dark ? Qt.rgba(1, 1, 1, 0.10) : "transparent"
    }
    contentItem: Text {
        text: tip.text
        width: Math.min(implicitWidth, 300)
        leftPadding: 11; rightPadding: 11; topPadding: 7; bottomPadding: 7
        wrapMode: Text.WordWrap
        font.pixelSize: 12
        lineHeight: 1.2
        color: "#F1F2F5"
    }
}
