// jiyao-recorder：用 ScreenCaptureKit 同时录系统声音和麦克风（macOS 15+）。
//
// 用法：jiyao-recorder <输出目录> [--no-mic]
// 输出：<目录>/system.wav、<目录>/mic.wav；收到 SIGINT/SIGTERM 后收尾退出。
// 向 stdout 打印一行状态：READY 表示已开始录音，ERROR: … 表示失败。

import AVFoundation
import Foundation
import ScreenCaptureKit

final class Track {
    let url: URL
    var file: AVAudioFile?
    var framesWritten: AVAudioFramePosition = 0

    init(url: URL) { self.url = url }
}

final class Recorder: NSObject, SCStreamOutput, SCStreamDelegate {
    let system: Track
    let mic: Track
    let withMic: Bool
    let queue = DispatchQueue(label: "jiyao.recorder")
    var stream: SCStream?
    var t0: CMTime?

    init(outDir: URL, withMic: Bool) {
        system = Track(url: outDir.appendingPathComponent("system.wav"))
        mic = Track(url: outDir.appendingPathComponent("mic.wav"))
        self.withMic = withMic
    }

    func start() async throws {
        let content = try await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: false)
        guard let display = content.displays.first else {
            throw NSError(domain: "jiyao", code: 1, userInfo: [NSLocalizedDescriptionKey: "找不到显示器"])
        }
        let filter = SCContentFilter(display: display, excludingApplications: [], exceptingWindows: [])
        let cfg = SCStreamConfiguration()
        cfg.capturesAudio = true
        cfg.excludesCurrentProcessAudio = true
        cfg.sampleRate = 48000
        cfg.channelCount = 1
        // 只要声音，画面压到最小
        cfg.width = 2
        cfg.height = 2
        cfg.minimumFrameInterval = CMTime(value: 1, timescale: 1)
        if withMic {
            cfg.captureMicrophone = true
            cfg.microphoneCaptureDeviceID = AVCaptureDevice.default(for: .audio)?.uniqueID
        }

        let s = SCStream(filter: filter, configuration: cfg, delegate: self)
        try s.addStreamOutput(self, type: .screen, sampleHandlerQueue: queue)
        try s.addStreamOutput(self, type: .audio, sampleHandlerQueue: queue)
        if withMic {
            try s.addStreamOutput(self, type: .microphone, sampleHandlerQueue: queue)
        }
        try await s.startCapture()
        stream = s
    }

    func stop() async {
        try? await stream?.stopCapture()
        queue.sync {
            system.file = nil
            mic.file = nil
        }
    }

    func stream(_ stream: SCStream, didOutputSampleBuffer sb: CMSampleBuffer, of type: SCStreamOutputType) {
        let track: Track
        switch type {
        case .audio: track = system
        case .microphone: track = mic
        default: return
        }
        guard sb.isValid, let desc = sb.formatDescription,
              var asbd = desc.audioStreamBasicDescription,
              let format = AVAudioFormat(streamDescription: &asbd)
        else { return }

        do {
            if track.file == nil {
                track.file = try AVAudioFile(
                    forWriting: track.url, settings: format.settings,
                    commonFormat: format.commonFormat, interleaved: format.isInterleaved)
            }
            guard let file = track.file else { return }

            // 两条音轨按同一个起点对齐；中间没有数据的空档补静音，避免两路错位
            let pts = sb.presentationTimeStamp
            if t0 == nil { t0 = pts }
            let expected = AVAudioFramePosition(CMTimeGetSeconds(CMTimeSubtract(pts, t0!)) * format.sampleRate)
            let gap = expected - track.framesWritten
            if gap > AVAudioFramePosition(format.sampleRate / 10) {
                try writeSilence(file: file, format: format, frames: gap)
                track.framesWritten += gap
            }

            try sb.withAudioBufferList { abl, _ in
                guard let pcm = AVAudioPCMBuffer(pcmFormat: format, bufferListNoCopy: abl.unsafePointer) else { return }
                try file.write(from: pcm)
                track.framesWritten += AVAudioFramePosition(pcm.frameLength)
            }
        } catch {
            fail("写入音频失败：\(error.localizedDescription)")
        }
    }

    func writeSilence(file: AVAudioFile, format: AVAudioFormat, frames: AVAudioFramePosition) throws {
        var left = frames
        while left > 0 {
            let n = AVAudioFrameCount(min(left, 48000))
            guard let buf = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: n) else { return }
            buf.frameLength = n  // 新分配的缓冲区内容为 0，即静音
            try file.write(from: buf)
            left -= AVAudioFramePosition(n)
        }
    }

    func stream(_ stream: SCStream, didStopWithError error: Error) {
        fail("录音中断：\(error.localizedDescription)")
    }
}

func fail(_ msg: String) -> Never {
    print("ERROR: \(msg)")
    fflush(stdout)
    exit(1)
}

let args = CommandLine.arguments
guard args.count >= 2 else { fail("用法：jiyao-recorder <输出目录> [--no-mic]") }
let outDir = URL(fileURLWithPath: args[1])
try? FileManager.default.createDirectory(at: outDir, withIntermediateDirectories: true)
let recorder = Recorder(outDir: outDir, withMic: !args.contains("--no-mic"))

signal(SIGINT, SIG_IGN)
signal(SIGTERM, SIG_IGN)
var stopSources: [DispatchSourceSignal] = []
for sig in [SIGINT, SIGTERM] {
    let src = DispatchSource.makeSignalSource(signal: sig, queue: .main)
    src.setEventHandler {
        Task {
            await recorder.stop()
            print("STOPPED")
            fflush(stdout)
            exit(0)
        }
    }
    src.resume()
    stopSources.append(src)
}

Task {
    do {
        try await recorder.start()
        print("READY")
        fflush(stdout)
    } catch {
        let ns = error as NSError
        if ns.domain == SCStreamErrorDomain && ns.code == SCStreamError.userDeclined.rawValue {
            fail("没有录屏与系统录音权限：系统设置 → 隐私与安全性 → 屏幕与系统录音，勾选你的终端后重启终端")
        }
        fail(error.localizedDescription)
    }
}

dispatchMain()
