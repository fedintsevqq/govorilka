// Помощник Говорилки для macOS: пишет весь системный звук через ScreenCaptureKit.
// stdout — сырой float32 моно 48 кГц, stderr — служебные строки: "ready", "error: ...".
// Завершается, когда закрывается stdin (родительский процесс остановил запись или упал).

import CoreMedia
import Foundation
import ScreenCaptureKit

let sampleRate = 48_000

func report(_ line: String) {
    FileHandle.standardError.write((line + "\n").data(using: .utf8)!)
}

final class AudioTap: NSObject, SCStreamOutput, SCStreamDelegate {
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

    func stream(_ stream: SCStream, didStopWithError error: Error) {
        report("error: поток остановлен: \(error.localizedDescription)")
        exit(2)
    }
}

let tap = AudioTap()

Task {
    do {
        try await tap.start()
        report("ready")
    } catch {
        report("error: \(error.localizedDescription)")
        exit(1)
    }
}

// читаем stdin до конца: EOF значит, что Говорилка закрыла запись
DispatchQueue.global().async {
    while !FileHandle.standardInput.availableData.isEmpty {}
    exit(0)
}

signal(SIGPIPE, SIG_IGN)
dispatchMain()
