// jiyao-recorder：用 ScreenCaptureKit 录系统声音和麦克风（macOS 15+），也负责把音视频解码成 16 kHz wav。
//
// 录音：jiyao-recorder <输出目录> [--no-mic] [--no-system]
//   输出 <目录>/system.wav、<目录>/mic.wav；收到 SIGINT/SIGTERM 后收尾退出。
//   向 stdout 打印一行状态：READY 表示已开始录音，ERROR: … 表示失败。
// 解码：jiyao-recorder decode <输入文件> <输出.wav>
//   用系统的 AVFoundation 解码 m4a/mp3/wav/aac/flac/mp4/mov 等，输出 16 kHz 单声道 16 位 wav；成功打印 OK。

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
    let withSystem: Bool
    let queue = DispatchQueue(label: "jiyao.recorder")
    var stream: SCStream?
    var t0: CMTime?

    init(outDir: URL, withMic: Bool, withSystem: Bool) {
        system = Track(url: outDir.appendingPathComponent("system.wav"))
        mic = Track(url: outDir.appendingPathComponent("mic.wav"))
        self.withMic = withMic
        self.withSystem = withSystem
    }

    func start() async throws {
        let content = try await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: false)
        guard let display = content.displays.first else {
            throw NSError(domain: "jiyao", code: 1, userInfo: [NSLocalizedDescriptionKey: "找不到显示器"])
        }
        let filter = SCContentFilter(display: display, excludingApplications: [], exceptingWindows: [])
        let cfg = SCStreamConfiguration()
        cfg.capturesAudio = withSystem
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
        if withSystem {
            try s.addStreamOutput(self, type: .audio, sampleHandlerQueue: queue)
        }
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

/// 解码任意音视频为 16 kHz 单声道 16 位 PCM wav。
func decode(_ input: String, _ output: String) -> Never {
    let asset = AVURLAsset(url: URL(fileURLWithPath: input))
    let done = DispatchSemaphore(value: 0)
    var tracks: [AVAssetTrack] = []
    Task {
        tracks = (try? await asset.loadTracks(withMediaType: .audio)) ?? []
        done.signal()
    }
    done.wait()
    guard !tracks.isEmpty else { fail("文件里没有可识别的音频（支持 m4a、mp3、wav、mp4、mov 等）：\(input)") }
    guard let reader = try? AVAssetReader(asset: asset) else { fail("无法读取：\(input)") }
    let settings: [String: Any] = [
        AVFormatIDKey: kAudioFormatLinearPCM, AVSampleRateKey: 16000, AVNumberOfChannelsKey: 1,
        AVLinearPCMBitDepthKey: 16, AVLinearPCMIsFloatKey: false, AVLinearPCMIsBigEndianKey: false,
        AVLinearPCMIsNonInterleaved: false,
    ]
    let out = AVAssetReaderAudioMixOutput(audioTracks: tracks, audioSettings: settings)
    reader.add(out)
    guard reader.startReading() else { fail("解码失败：\(reader.error?.localizedDescription ?? "")") }
    var pcm = Data()
    while let sb = out.copyNextSampleBuffer() {
        guard let block = CMSampleBufferGetDataBuffer(sb) else { continue }
        let len = CMBlockBufferGetDataLength(block)
        var chunk = Data(count: len)
        chunk.withUnsafeMutableBytes { _ = CMBlockBufferCopyDataBytes(block, atOffset: 0, dataLength: len, destination: $0.baseAddress!) }
        pcm.append(chunk)
    }
    guard reader.status == .completed else { fail("解码中断：\(reader.error?.localizedDescription ?? "")") }
    var wav = Data()
    func u32(_ v: UInt32) { withUnsafeBytes(of: v.littleEndian) { wav.append(contentsOf: $0) } }
    func u16(_ v: UInt16) { withUnsafeBytes(of: v.littleEndian) { wav.append(contentsOf: $0) } }
    wav.append(contentsOf: Array("RIFF".utf8)); u32(UInt32(36 + pcm.count)); wav.append(contentsOf: Array("WAVEfmt ".utf8))
    u32(16); u16(1); u16(1); u32(16000); u32(32000); u16(2); u16(16)
    wav.append(contentsOf: Array("data".utf8)); u32(UInt32(pcm.count))
    wav.append(pcm)
    do { try wav.write(to: URL(fileURLWithPath: output)) } catch { fail("写入失败：\(error.localizedDescription)") }
    print("OK")
    exit(0)
}

let args = CommandLine.arguments
if args.count >= 4 && args[1] == "decode" { decode(args[2], args[3]) }
guard args.count >= 2 else { fail("用法：jiyao-recorder <输出目录> [--no-mic] [--no-system]，或 jiyao-recorder decode <输入> <输出.wav>") }
let outDir = URL(fileURLWithPath: args[1])
try? FileManager.default.createDirectory(at: outDir, withIntermediateDirectories: true)
let recorder = Recorder(outDir: outDir, withMic: !args.contains("--no-mic"), withSystem: !args.contains("--no-system"))

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
