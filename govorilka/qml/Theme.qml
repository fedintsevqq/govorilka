pragma Singleton
import QtQuick

// Дизайн-система Говорилки: одна точка правды для цветов, размеров и движения.
// Тёмная тема — «эфирное стекло»: почти чёрный фон, мягкие светящиеся пятна, стеклянные карточки
// с двойной рамкой. Светлая — тот же язык на тёплом сером.
QtObject {
    id: theme

    // ---------- режим ----------
    property string mode: "system"          // system / dark / light — из настроек
    property bool reduceMotion: false       // из настроек: вместо движения — растворение
    readonly property bool dark: mode === "dark" || (mode === "system" && Qt.styleHints.colorScheme !== Qt.Light)

    // ---------- поверхности ----------
    readonly property color bg: dark ? "#07080B" : "#EEF0F4"
    readonly property color bgDeep: dark ? "#0B0D12" : "#E6E9EF"
    readonly property color shell: dark ? Qt.rgba(1, 1, 1, 0.035) : Qt.rgba(0, 0, 0, 0.025)
    readonly property color shellLine: dark ? Qt.rgba(1, 1, 1, 0.075) : Qt.rgba(0, 0, 0, 0.06)
    readonly property color core: dark ? "#101218" : "#FFFFFF"
    readonly property color coreRaised: dark ? "#161922" : "#F7F8FA"
    readonly property color highlight: dark ? Qt.rgba(1, 1, 1, 0.07) : Qt.rgba(1, 1, 1, 0.9)
    readonly property color hairline: dark ? Qt.rgba(1, 1, 1, 0.06) : Qt.rgba(0, 0, 0, 0.06)
    readonly property color hover: dark ? Qt.rgba(1, 1, 1, 0.055) : Qt.rgba(0, 0, 0, 0.045)
    readonly property color pressed: dark ? Qt.rgba(1, 1, 1, 0.09) : Qt.rgba(0, 0, 0, 0.08)
    readonly property color glass: dark ? Qt.rgba(0.055, 0.06, 0.08, 0.92) : Qt.rgba(0.99, 0.99, 1.0, 0.94)
    readonly property color shadow: dark ? Qt.rgba(0, 0, 0, 0.55) : Qt.rgba(0.08, 0.1, 0.18, 0.16)

    // ---------- текст ----------
    readonly property color text: dark ? "#F1F2F5" : "#111317"
    readonly property color textSecondary: dark ? "#A2A8B4" : "#555C69"
    readonly property color textTertiary: dark ? "#8A90A0" : "#636A78"
    // текст и значки поверх акцентного градиента и цветных дисков: тёмный на светлом градиенте тёмной темы
    readonly property color fgAccent: dark ? "#0B0D12" : "#FFFFFF"
    readonly property color fgRecord: "#FFFFFF"
    readonly property color fgPause: dark ? "#1A1200" : "#FFFFFF"
    readonly property color switchOff: dark ? "#646B7C" : "#838B9A"   // выключенный переключатель различим (≥ 3:1)

    // ---------- акценты ----------
    readonly property color accentA: dark ? "#8B7BFF" : "#6A55F0"   // ирис
    readonly property color accentB: dark ? "#45B4FF" : "#1F6FD0"   // лазурь
    readonly property color accent: Qt.rgba((accentA.r + accentB.r) / 2, (accentA.g + accentB.g) / 2, (accentA.b + accentB.b) / 2, 1)
    readonly property color record: dark ? "#FF4D61" : "#C8213A"
    readonly property color recordSoft: dark ? Qt.rgba(1, 0.30, 0.38, 0.16) : Qt.rgba(0.78, 0.13, 0.23, 0.11)
    readonly property color pause: dark ? "#FFC14D" : "#8F6100"
    readonly property color mark: dark ? "#FFC14D" : "#8F5A00"
    readonly property color markSoft: dark ? Qt.rgba(1, 0.76, 0.3, 0.12) : Qt.rgba(0.56, 0.35, 0.0, 0.10)
    readonly property color success: dark ? "#3DDC97" : "#0B7A53"
    readonly property color danger: record

    // свечение фона
    readonly property color orbA: dark ? Qt.rgba(0.48, 0.38, 1.0, 0.22) : Qt.rgba(0.48, 0.38, 1.0, 0.14)
    readonly property color orbB: dark ? Qt.rgba(0.15, 0.85, 0.72, 0.13) : Qt.rgba(0.2, 0.7, 1.0, 0.12)

    // ---------- говорящие ----------
    readonly property color me: dark ? "#8DB7FF" : "#2160E0"
    readonly property var others: dark
        ? ["#5EEAD4", "#C4A6FF", "#FFB27D", "#7DD3FC", "#F9A8D4", "#BEF264", "#FCA5A5", "#FDE68A"]
        : ["#0B7A5E", "#7034E0", "#C24A0B", "#0A6EA5", "#B8166A", "#4B7A0C", "#B42323", "#8A6A00"]
    function speakerColor(index) { return index < 0 ? me : others[index % others.length] }

    // ---------- форма ----------
    readonly property int radiusShell: 26
    readonly property int radiusCore: 20      // 26 − 6: концентрические скругления двойной рамки
    readonly property int radiusControl: 14
    readonly property int bezel: 6
    readonly property int gap: 14
    readonly property int pad: 20

    // ---------- шрифт ----------
    readonly property int fontDisplay: 38
    readonly property int fontTitle: 15
    readonly property int fontBody: 14
    readonly property int fontMeta: 11
    readonly property int fontEyebrow: 10

    // ---------- движение (Emil + Apple: ease-out, без отскока, коротко) ----------
    readonly property var easeOut: [0.23, 1, 0.32, 1, 1, 1]          // сильный ease-out для появления
    readonly property var easeInOut: [0.77, 0, 0.175, 1, 1, 1]       // перемещение по экрану
    readonly property var easeDrawer: [0.32, 0.72, 0, 1, 1, 1]       // панели и листы
    readonly property int fast: reduceMotion ? 0 : 120
    readonly property int base: reduceMotion ? 120 : 180
    readonly property int slow: reduceMotion ? 160 : 260
}
