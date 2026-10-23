import QtQuick

// Иконка из набора Говорилки: image://icon/<имя>/<цвет без #>. Рисуется под нужный размер и цвет.
Image {
    id: icon
    property string name: "mic"
    property color color: Theme.text
    property int size: 20
    width: size
    height: size
    source: name ? "image://icon/" + name + "/" + String(color).substring(1) : ""
    // ровно под размер: Qt сам умножает его на масштаб экрана (Retina, 125–200% Windows), и иконка
    // рисуется пиксель в пиксель. Запас с последующим сжатием размывал тонкие линии.
    sourceSize: Qt.size(size, size)
    smooth: false
}
