// Помощник Говорилки для macOS.
//   --source system  весь системный звук через ScreenCaptureKit (по умолчанию)
//   --source mic     микрофон по умолчанию через AVAudioEngine, со сменой устройства на лету
// stdout — сырой float32 моно 48 кГц, stderr — служебные строки: "ready", "error: ...", прочее — в лог.
// Завершается, когда закрывается stdin (родительский процесс остановил запись или упал).

import AVFoundation
import CoreGraphics
import CoreMedia
import Foundation
import ScreenCaptureKit

let sampleRate = 48_000
let reconnectAttempts = 10  // ~5 секунд на переключение AirPods между режимами
let reconnectDelayNs: UInt64 = 500_000_000

func report(_ line: String) {
    FileHandle.standardError.write((line + "\n").data(using: .utf8)!)
}

final class SystemTap: NSObject, SCStreamOutput, SCStreamDelegate {
    private var stream: SCStream?
    private let output = FileHandle.standardOutput
    private let audioQueue = DispatchQueue(label: "govorilka.audiotap")

    func start() async throws {
        let content = try await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: true)
        guard let display = content.displays.first else {
            throw NSError(domain: "audiotap", code: 1, userInfo: [NSLocalizedDescriptionKey: "не найден дисплей"])
        }
        let filter = SCContentFilter(display: display, excludingApplications: [], exceptingWindows: [])

        let config = SCStreamConfiguration()
        config.capturesAudio = true
        config.sampleRate = sampleRate
        config.channelCount = 1
        config.excludesCurrentProcessAudio = true
        // видео не нужно, но без него поток не стартует: берём крошечный кадр раз в секунду
        config.width = 2
        config.height = 2
        config.minimumFrameInterval = CMTime(value: 1, timescale: 1)

        let stream = SCStream(filter: filter, configuration: config, delegate: self)
        try stream.addStreamOutput(self, type: .audio, sampleHandlerQueue: audioQueue)
        // кадры экрана принимаем и выбрасываем, иначе ScreenCaptureKit сыплет предупреждениями
        try stream.addStreamOutput(self, type: .screen, sampleHandlerQueue: DispatchQueue(label: "govorilka.screen"))
        try await stream.startCapture()
        self.stream = stream
    }

    func stream(_ stream: SCStream, didOutputSampleBuffer sampleBuffer: CMSampleBuffer, of type: SCStreamOutputType) {
        guard type == .audio, sampleBuffer.isValid else { return }
        var blockBuffer: CMBlockBuffer?
        var bufferList = AudioBufferList()
        let status = CMSampleBufferGetAudioBufferListWithRetainedBlockBuffer(
            sampleBuffer,
            bufferListSizeNeededOut: nil,
            bufferListOut: &bufferList,
            bufferListSize: MemoryLayout<AudioBufferList>.size,
            blockBufferAllocator: nil,
            blockBufferMemoryAllocator: nil,
            flags: 0,
            blockBufferOut: &blockBuffer
        )
        guard status == noErr, let data = bufferList.mBuffers.mData else { return }
        output.write(Data(bytes: data, count: Int(bufferList.mBuffers.mDataByteSize)))
    }

    // Подключили или сменили наушники — macOS может остановить поток системного звука. Вместо выхода
    // переподключаемся сами: иначе Говорилка молча теряет звук собеседников до следующего ручного переподключения.
    func stream(_ stream: SCStream, didStopWithError error: Error) {
        report("system audio stream stopped, reconnecting: \(error.localizedDescription)")
        self.stream = nil
        reconnect(attempt: 1)
    }

    private func reconnect(attempt: Int) {
        Task {
            do {
                try await self.start()
                report("device changed")
            } catch {
                if attempt < reconnectAttempts {
                    try? await Task.sleep(nanoseconds: reconnectDelayNs)
                    reconnect(attempt: attempt + 1)
                } else {
                    report("error: поток остановлен: \(error.localizedDescription)")
                    exit(2)
                }
            }
        }
    }
}

/// Микрофон: AVAudioEngine с переводом в моно 48 кГц.
/// При смене устройства (AirPods, внешний микрофон) движок присылает уведомление — перезапускаемся.
final class MicTap {
    private let engine = AVAudioEngine()
    private let output = FileHandle.standardOutput
    private let outFormat = AVAudioFormat(
        commonFormat: .pcmFormatFloat32, sampleRate: Double(sampleRate), channels: 1, interleaved: false
    )!
    private var converter: AVAudioConverter?
    private var observer: NSObjectProtocol?

    func start() throws {
        let input = engine.inputNode
        let inFormat = input.outputFormat(forBus: 0)
        guard inFormat.sampleRate > 0, inFormat.channelCount > 0 else {
            throw NSError(domain: "audiotap", code: 2, userInfo: [NSLocalizedDescriptionKey: "микрофон не найден"])
        }
        guard let converter = AVAudioConverter(from: inFormat, to: outFormat) else {
            throw NSError(domain: "audiotap", code: 3, userInfo: [NSLocalizedDescriptionKey: "неподдерживаемый формат микрофона"])
        }
        converter.downmix = true
        self.converter = converter
        input.installTap(onBus: 0, bufferSize: 4800, format: inFormat) { [weak self] buffer, _ in
            self?.write(buffer)
        }
        engine.prepare()
        try engine.start()
        if observer == nil {
            observer = NotificationCenter.default.addObserver(
                forName: .AVAudioEngineConfigurationChange, object: engine, queue: .main
            ) { [weak self] _ in self?.restart() }
        }
    }

    /// Пока AirPods переключаются между режимами (наушники и микрофон), вход на короткое время без формата.
    /// Тогда пробуем снова через полсекунды, а не выходим: выход оставил бы Говорилку без микрофона.
    private func restart(attempt: Int = 1) {
        engine.inputNode.removeTap(onBus: 0)
        engine.stop()
        do {
            try start()
            report("device changed")
        } catch {
            if attempt < reconnectAttempts {
                DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) { self.restart(attempt: attempt + 1) }
            } else {
                report("error: не переподключился к микрофону: \(error.localizedDescription)")
                exit(3)
            }
        }
    }

    private func write(_ buffer: AVAudioPCMBuffer) {
        guard let converter else { return }
        let ratio = outFormat.sampleRate / buffer.format.sampleRate
        let capacity = AVAudioFrameCount(Double(buffer.frameLength) * ratio) + 32
        guard let converted = AVAudioPCMBuffer(pcmFormat: outFormat, frameCapacity: capacity) else { return }
        var consumed = false
        var error: NSError?
        converter.convert(to: converted, error: &error) { _, status in
            if consumed {
                status.pointee = .noDataNow
                return nil
            }
            consumed = true
            status.pointee = .haveData
            return buffer
        }
        guard error == nil, let channel = converted.floatChannelData?[0], converted.frameLength > 0 else { return }
        output.write(Data(bytes: channel, count: Int(converted.frameLength) * MemoryLayout<Float>.size))
    }
}

let arguments = CommandLine.arguments
let source = arguments.firstIndex(of: "--source").flatMap { $0 + 1 < arguments.count ? arguments[$0 + 1] : nil } ?? "system"

// держим ссылки до конца процесса
let systemTap = SystemTap()
let micTap = MicTap()

/// Приложение показывает разные сообщения для «нет разрешения» и для остальных сбоев, поэтому различаем их здесь.
/// Строка "error: permission: <служба>: подробности" означает отказ в доступе, всё прочее — другую причину.
func failSystem(_ error: Error) -> Never {
    let code = (error as NSError).code
    // -3801 — SCStreamError.userDeclined; запасной признак — система сама говорит, что доступа к записи нет
    if code == -3801 || !CGPreflightScreenCaptureAccess() {
        _ = CGRequestScreenCaptureAccess()  // показывает системный запрос и вносит приложение в список настроек
        report("error: permission: screen: \(error.localizedDescription) (code \(code))")
    } else {
        report("error: \(error.localizedDescription) (code \(code))")
    }
    exit(1)
}

func startMic() {
    do {
        try micTap.start()
        report("ready")
    } catch {
        report("error: \(error.localizedDescription)")
        exit(1)
    }
}

switch source {
case "mic":
    DispatchQueue.main.async {
        // без проверки доступа AVAudioEngine при отказе не падает, а молча отдаёт тишину
        switch AVCaptureDevice.authorizationStatus(for: .audio) {
        case .denied, .restricted:
            report("error: permission: microphone: доступ запрещён в настройках")
            exit(1)
        case .notDetermined:
            AVCaptureDevice.requestAccess(for: .audio) { granted in
                DispatchQueue.main.async {
                    if granted {
                        startMic()
                    } else {
                        report("error: permission: microphone: пользователь отказал в доступе")
                        exit(1)
                    }
                }
            }
        default:
            startMic()
        }
    }
case "system":
    Task {
        do {
            try await systemTap.start()
            report("ready")
        } catch {
            failSystem(error)
        }
    }
default:
    report("error: неизвестный источник \(source)")
    exit(64)
}

// читаем stdin до конца: EOF значит, что Говорилка закрыла запись
DispatchQueue.global().async {
    while !FileHandle.standardInput.availableData.isEmpty {}
    exit(0)
}

signal(SIGPIPE, SIG_IGN)
dispatchMain()
