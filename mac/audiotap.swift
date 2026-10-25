// Помощник Говорилки для macOS.
//   --source system  весь системный звук через ScreenCaptureKit (по умолчанию)
//   --source mic     микрофон по умолчанию через AVAudioEngine, со сменой устройства на лету
//   --prompt         показать системный запрос на запись экрана, если доступа нет (только при старте записи
//                    по нажатию, не при переподключении: иначе запрос всплывал бы каждые десять секунд)
// stdout — сырой float32 моно 48 кГц, stderr — служебные строки: "ready", "error: ...", "waiting: permission",
// прочее — в лог. Завершается, когда закрывается stdin (родительский процесс остановил запись или упал).

import AVFoundation
import CoreGraphics
import CoreMedia
import Foundation
import ScreenCaptureKit

// Говорилка закрыла трубу (остановила запись) — не падаем от SIGPIPE посреди записи, а выходим по EOF stdin.
signal(SIGPIPE, SIG_IGN)

let sampleRate = 48_000
let reconnectAttempts = 10  // ~5 секунд на переключение AirPods между режимами
let reconnectDelayNs: UInt64 = 500_000_000

func report(_ line: String) {
    FileHandle.standardError.write((line + "\n").data(using: .utf8)!)
}

let arguments = CommandLine.arguments
let source = arguments.firstIndex(of: "--source").flatMap { $0 + 1 < arguments.count ? arguments[$0 + 1] : nil } ?? "system"
let mayPrompt = arguments.contains("--prompt")

/// Ошибка запуска системного звука строкой для Говорилки: отказ в доступе помечаем "permission: screen:",
/// чтобы приложение показало подсказку про разрешение, а не «устройство недоступно».
func describeSystemFailure(_ error: Error) -> String {
    let code = (error as NSError).code
    // -3801 — SCStreamError.userDeclined; запасной признак — система сама говорит, что доступа к записи нет
    if code == -3801 || !CGPreflightScreenCaptureAccess() {
        return "permission: screen: \(error.localizedDescription) (code \(code))"
    }
    return "\(error.localizedDescription) (code \(code))"
}

final class SystemTap: NSObject, SCStreamOutput, SCStreamDelegate {
    private var stream: SCStream?
    private let output = FileHandle.standardOutput
    private let audioQueue = DispatchQueue(label: "govorilka.audiotap")
    private let lock = NSLock()
    private var reconnecting = false

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
        lock.lock()
        let previous = self.stream
        self.stream = stream
        lock.unlock()
        if let previous { try? await previous.stopCapture() }  // старый поток не должен писать вперемешку с новым
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
        lock.lock()
        let alreadyReconnecting = reconnecting
        reconnecting = true
        if self.stream === stream { self.stream = nil }
        lock.unlock()
        if alreadyReconnecting { return }  // второе уведомление о той же остановке — цепочка уже идёт
        report("system audio stream stopped, reconnecting: \(error.localizedDescription)")
        reconnect(attempt: 1)
    }

    private func reconnect(attempt: Int) {
        Task {
            do {
                try await self.start()
                self.lock.lock()
                self.reconnecting = false
                self.lock.unlock()
                report("device changed")
            } catch {
                if attempt < reconnectAttempts {
                    try? await Task.sleep(nanoseconds: reconnectDelayNs)
                    reconnect(attempt: attempt + 1)
                } else {
                    report("error: \(describeSystemFailure(error))")
                    exit(2)
                }
            }
        }
    }
}

/// Микрофон: AVAudioEngine с переводом в моно 48 кГц.
/// При смене устройства (AirPods, внешний микрофон) движок присылает уведомление — перезапускаемся.
/// Формат входа меняется на лету (AirPods в режиме гарнитуры дают 16–24 кГц вместо 48), поэтому подписку на звук
/// ставим в «родном» формате узла (format: nil), а переводчик формата пересоздаём, как только он поменялся.
final class MicTap {
    private let engine = AVAudioEngine()
    private let output = FileHandle.standardOutput
    private let outFormat = AVAudioFormat(
        commonFormat: .pcmFormatFloat32, sampleRate: Double(sampleRate), channels: 1, interleaved: false
    )!
    // переводчик и его формат трогает только поток звука (write): без гонки с перезапуском
    private var converter: AVAudioConverter?
    private var converterFormat: AVAudioFormat?
    private var observer: NSObjectProtocol?
    private var restarting = false  // уведомлений о смене устройства бывает несколько подряд: одна цепочка перезапуска

    private func startEngine() throws {
        let input = engine.inputNode
        let inFormat = input.outputFormat(forBus: 0)
        guard inFormat.sampleRate > 0, inFormat.channelCount > 0 else {
            throw NSError(domain: "audiotap", code: 2, userInfo: [NSLocalizedDescriptionKey: "микрофон не найден"])
        }
        input.installTap(onBus: 0, bufferSize: 4800, format: nil) { [weak self] buffer, _ in
            self?.write(buffer)
        }
        engine.prepare()
        do {
            try engine.start()
        } catch {
            input.removeTap(onBus: 0)
            throw error
        }
        if observer == nil {
            observer = NotificationCenter.default.addObserver(
                forName: .AVAudioEngineConfigurationChange, object: engine, queue: .main
            ) { [weak self] _ in self?.deviceChanged() }
        }
    }

    /// Первый запуск тоже с повторами: AirPods, подключённые за секунду до записи, ещё переключаются между режимами.
    func start(attempt: Int = 1, onReady: @escaping () -> Void) {
        do {
            try startEngine()
            onReady()
        } catch {
            if attempt < reconnectAttempts {
                DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) { self.start(attempt: attempt + 1, onReady: onReady) }
            } else {
                report("error: микрофон не запустился: \(error.localizedDescription)")
                exit(1)
            }
        }
    }

    private func deviceChanged() {
        if restarting { return }
        restarting = true
        restart(attempt: 1)
    }

    /// Пока AirPods переключаются между режимами (наушники и микрофон), вход на короткое время без формата.
    /// Тогда пробуем снова через полсекунды, а не выходим: выход оставил бы Говорилку без микрофона.
    private func restart(attempt: Int) {
        engine.inputNode.removeTap(onBus: 0)
        engine.stop()
        do {
            try startEngine()
            restarting = false
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
        if converterFormat == nil || buffer.format != converterFormat! {
            guard let fresh = AVAudioConverter(from: buffer.format, to: outFormat) else { return }
            fresh.downmix = true
            converter = fresh
            converterFormat = buffer.format
        }
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

// держим ссылки до конца процесса
let systemTap = SystemTap()
let micTap = MicTap()

func failSystem(_ error: Error) -> Never {
    let description = describeSystemFailure(error)
    if description.hasPrefix("permission:") && mayPrompt {
        _ = CGRequestScreenCaptureAccess()  // показывает системный запрос и вносит приложение в список настроек
    }
    report("error: \(description)")
    exit(1)
}

func startMic() {
    micTap.start(onReady: { report("ready") })
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
            report("waiting: permission")  // человек читает системный запрос: Говорилка подождёт дольше обычного
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
    if mayPrompt && !CGPreflightScreenCaptureAccess() {
        report("waiting: permission")
    }
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

dispatchMain()
