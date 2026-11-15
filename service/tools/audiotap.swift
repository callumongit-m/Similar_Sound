// audiotap: records one app's audio output using a Core Audio process tap (macOS 14.2+), with no
// virtual audio driver. Used to hear Ableton Live while the user presses play.
//
//   audiotap --pid <pid> --out <file.wav> [--max <seconds>]
//   audiotap --pid <pid> --stream          live meter feed: "FMT <rate> <channels>\n", then raw interleaved float32 on stdout, nothing kept
//   audiotap --selftest <file.wav>          writes a 1.5 s test tone through the same WAV writer (needs no audio access)
//
// stdout: "L <rms> <seconds>" about ten times a second.  Stops on SIGINT/SIGTERM or at --max, then
// writes a float32 WAV. Exit codes: 0 ok, 2 bad arguments / setup failure, 3 no permission, 4 no such app.
import AVFoundation
import CoreAudio
import Foundation

func die(_ message: String, code: Int32) -> Never {
    FileHandle.standardError.write((message + "\n").data(using: .utf8)!)
    exit(code)
}

// ---- writing the recording
/// Writes interleaved float32 frames as a WAV. The buffer must be NON-interleaved: AVAudioPCMBuffer exposes
/// `floatChannelData` only for non-interleaved formats (for interleaved ones it is nil, which once made every
/// recording silently empty). The file is finalised when this function returns, before the process exits.
func writeWav(_ data: [Float], channels: Int, sampleRate: Double, to path: String) throws {
    let frames = data.count / max(channels, 1)
    guard frames > 0,
          let format = AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: sampleRate,
                                     channels: AVAudioChannelCount(channels), interleaved: false),
          let buffer = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: AVAudioFrameCount(frames)),
          let planes = buffer.floatChannelData
    else { throw NSError(domain: "audiotap", code: 1, userInfo: [NSLocalizedDescriptionKey: "nothing to write"]) }
    buffer.frameLength = AVAudioFrameCount(frames)
    for ch in 0..<channels { for f in 0..<frames { planes[ch][f] = data[f * channels + ch] } }
    let settings: [String: Any] = [AVFormatIDKey: kAudioFormatLinearPCM, AVSampleRateKey: sampleRate,
                                   AVNumberOfChannelsKey: channels, AVLinearPCMBitDepthKey: 32,
                                   AVLinearPCMIsFloatKey: true, AVLinearPCMIsBigEndianKey: false]
    let file = try AVAudioFile(forWriting: URL(fileURLWithPath: path), settings: settings,
                               commonFormat: .pcmFormatFloat32, interleaved: false)
    try file.write(from: buffer)
}

// ---- arguments
var pid: pid_t = 0
var outPath = ""
var maxSeconds = 90.0
var streaming = false
var args = Array(CommandLine.arguments.dropFirst())
if args.first == "--selftest", args.count == 2 {   // exercise the WAV writer alone: a stereo tone, left louder than right
    let rate = 44100.0
    var tone = [Float]()
    for i in 0..<Int(rate * 1.5) { let v = Float(sin(2 * Double.pi * 440 * Double(i) / rate)); tone.append(0.5 * v); tone.append(0.25 * v) }
    do { try writeWav(tone, channels: 2, sampleRate: rate, to: args[1]); print("selftest wrote \(tone.count / 2) frames"); exit(0) }
    catch { die("selftest failed: \(error)", code: 2) }
}
while !args.isEmpty {
    let flag = args.removeFirst()
    if flag == "--stream" { streaming = true; continue }
    guard !args.isEmpty else { die("missing value for \(flag)", code: 2) }
    let value = args.removeFirst()
    switch flag {
    case "--pid": pid = pid_t(value) ?? 0
    case "--out": outPath = value
    case "--max": maxSeconds = Double(value) ?? 90
    default: die("unknown flag \(flag)", code: 2)
    }
}
guard pid > 0, streaming || !outPath.isEmpty else { die("usage: audiotap --pid <pid> --out <file.wav> [--max <s>]", code: 2) }

// ---- helpers
func property<T>(_ object: AudioObjectID, _ selector: AudioObjectPropertySelector, qualifier: UnsafeRawPointer? = nil,
                 qualifierSize: UInt32 = 0, as: T.Type) -> T? {
    var address = AudioObjectPropertyAddress(mSelector: selector, mScope: kAudioObjectPropertyScopeGlobal,
                                             mElement: kAudioObjectPropertyElementMain)
    var size = UInt32(MemoryLayout<T>.size)
    let value = UnsafeMutablePointer<T>.allocate(capacity: 1)
    defer { value.deallocate() }
    guard AudioObjectGetPropertyData(object, &address, qualifierSize, qualifier, &size, value) == noErr else { return nil }
    return value.pointee
}

func processObject(for pid: pid_t) -> AudioObjectID? {
    var p = pid
    let obj = property(AudioObjectID(kAudioObjectSystemObject), kAudioHardwarePropertyTranslatePIDToProcessObject,
                       qualifier: &p, qualifierSize: UInt32(MemoryLayout<pid_t>.size), as: AudioObjectID.self)
    return (obj ?? 0) == 0 || obj == AudioObjectID(kAudioObjectUnknown) ? nil : obj
}

func defaultOutputUID() -> String? {
    guard let dev = property(AudioObjectID(kAudioObjectSystemObject), kAudioHardwarePropertyDefaultSystemOutputDevice,
                             as: AudioObjectID.self) else { return nil }
    return (property(dev, kAudioDevicePropertyDeviceUID, as: CFString?.self) ?? nil) as String?
}

// ---- the tap
guard let procObject = processObject(for: pid) else {
    die("That app isn't producing audio yet. Press play in Ableton.", code: 4)
}
let tapDescription = CATapDescription(stereoMixdownOfProcesses: [procObject])
tapDescription.uuid = UUID()
tapDescription.muteBehavior = .unmuted          // Ableton keeps playing out loud as normal

var tapID = AudioObjectID(kAudioObjectUnknown)
let tapStatus = AudioHardwareCreateProcessTap(tapDescription, &tapID)
guard tapStatus == noErr else {
    die("couldn't create the audio tap (status \(tapStatus)); is audio recording allowed?", code: 3)
}

guard let outputUID = defaultOutputUID() else { die("no default output device", code: 2) }
let aggregate: [String: Any] = [
    kAudioAggregateDeviceNameKey: "SimilarSoundTap",
    kAudioAggregateDeviceUIDKey: UUID().uuidString,
    kAudioAggregateDeviceMainSubDeviceKey: outputUID,
    kAudioAggregateDeviceIsPrivateKey: true,
    kAudioAggregateDeviceIsStackedKey: false,
    kAudioAggregateDeviceTapAutoStartKey: true,
    kAudioAggregateDeviceSubDeviceListKey: [[kAudioSubDeviceUIDKey: outputUID]],
    kAudioAggregateDeviceTapListKey: [[kAudioSubTapDriftCompensationKey: true,
                                       kAudioSubTapUIDKey: tapDescription.uuid.uuidString]],
]
var aggregateID = AudioObjectID(kAudioObjectUnknown)
guard AudioHardwareCreateAggregateDevice(aggregate as CFDictionary, &aggregateID) == noErr else {
    die("couldn't create the capture device", code: 2)
}

guard var format = property(tapID, kAudioTapPropertyFormat, as: AudioStreamBasicDescription.self) else {
    die("couldn't read the tap's audio format", code: 2)
}
let channels = Int(format.mChannelsPerFrame)
let sampleRate = format.mSampleRate
let nonInterleaved = (format.mFormatFlags & kAudioFormatFlagIsNonInterleaved) != 0
FileHandle.standardError.write("tap format: \(Int(sampleRate)) Hz, \(channels) ch, \(nonInterleaved ? "separate buffers per channel" : "interleaved")\n".data(using: .utf8)!)
guard format.mFormatID == kAudioFormatLinearPCM, format.mBitsPerChannel == 32, channels > 0 else {
    die("unexpected tap format", code: 2)
}

if streaming { print("FMT \(Int(sampleRate)) \(channels)"); fflush(stdout) }

// ---- recording state (touched from the IO thread and the main loop)
let lock = NSLock()
var samples = [Float]()
samples.reserveCapacity(Int(sampleRate * maxSeconds) * channels + 4096)
var windowSumSquares = 0.0
var windowCount = 0
var stop = false

var ioProc: AudioDeviceIOProcID?
let ioStatus = AudioDeviceCreateIOProcIDWithBlock(&ioProc, aggregateID, DispatchQueue(label: "similar-sound.tap")) {
    _, inputData, _, _, _ in
    let buffers = UnsafeMutableAudioBufferListPointer(UnsafeMutablePointer(mutating: inputData))
    if streaming {                       // the audio goes straight down the pipe: never stored here
        var out = [Float]()
        if nonInterleaved, buffers.count >= channels {
            let frames = Int(buffers[0].mDataByteSize) / MemoryLayout<Float>.size
            out.reserveCapacity(frames * channels)
            for f in 0..<frames { for ch in 0..<channels { out.append(buffers[ch].mData!.assumingMemoryBound(to: Float.self)[f]) } }
        } else {
            for b in buffers { if let d = b.mData { out.append(contentsOf: UnsafeBufferPointer(start: d.assumingMemoryBound(to: Float.self), count: Int(b.mDataByteSize) / 4)) } }
        }
        out.withUnsafeBytes { _ = fwrite($0.baseAddress, 1, $0.count, stdout) }
        fflush(stdout)
        return
    }
    lock.lock()
    if nonInterleaved, buffers.count >= channels, let first = buffers[0].mData {
        // one buffer per channel: weave them into interleaved frames so everything downstream sees one layout
        let frames = Int(buffers[0].mDataByteSize) / MemoryLayout<Float>.size
        _ = first
        if samples.count + frames * channels <= samples.capacity {
            for f in 0..<frames {
                for ch in 0..<channels {
                    let v = buffers[ch].mData!.assumingMemoryBound(to: Float.self)[f]
                    windowSumSquares += Double(v * v)
                    samples.append(v)
                }
            }
        }
        windowCount += frames * channels
    } else {
        for buffer in buffers {
            guard let data = buffer.mData else { continue }
            let n = Int(buffer.mDataByteSize) / MemoryLayout<Float>.size
            let floats = data.assumingMemoryBound(to: Float.self)
            for i in 0..<n { let v = floats[i]; windowSumSquares += Double(v * v) }
            windowCount += n
            if samples.count + n <= samples.capacity { samples.append(contentsOf: UnsafeBufferPointer(start: floats, count: n)) }
        }
    }
    lock.unlock()
}
guard ioStatus == noErr, let proc = ioProc else { die("couldn't start recording", code: 2) }
guard AudioDeviceStart(aggregateID, proc) == noErr else { die("couldn't start recording", code: 3) }

// ---- stop on SIGINT / SIGTERM
var signalSources = [DispatchSourceSignal]()
for sig in [SIGINT, SIGTERM] {
    signal(sig, SIG_IGN)
    let source = DispatchSource.makeSignalSource(signal: sig, queue: .main)
    source.setEventHandler { stop = true }
    source.resume()
    signalSources.append(source)
}

// ---- report levels until told to stop
let started = Date()
let timer = DispatchSource.makeTimerSource(queue: .main)
timer.schedule(deadline: .now() + 0.1, repeating: 0.1)
timer.setEventHandler {
    if streaming {
        if stop { AudioDeviceStop(aggregateID, proc); AudioDeviceDestroyIOProcID(aggregateID, proc); AudioHardwareDestroyAggregateDevice(aggregateID); AudioHardwareDestroyProcessTap(tapID); exit(0) }
        return
    }
    lock.lock()
    let rms = windowCount > 0 ? (windowSumSquares / Double(windowCount)).squareRoot() : 0
    windowSumSquares = 0; windowCount = 0
    let recorded = Double(samples.count) / Double(channels) / sampleRate
    lock.unlock()
    print(String(format: "L %.5f %.2f", rms, recorded))
    fflush(stdout)
    if stop || recorded >= maxSeconds || Date().timeIntervalSince(started) > maxSeconds + 5 {
        timer.cancel()
        finish()
    }
}
timer.resume()

func finish() -> Never {
    AudioDeviceStop(aggregateID, proc)
    AudioDeviceDestroyIOProcID(aggregateID, proc)
    AudioHardwareDestroyAggregateDevice(aggregateID)
    AudioHardwareDestroyProcessTap(tapID)
    lock.lock()
    let data = samples
    lock.unlock()
    do { try writeWav(data, channels: channels, sampleRate: sampleRate, to: outPath) }
    catch { die("couldn't write the recording: \(error)", code: 2) }
    let peak = data.reduce(Float(0)) { max($0, abs($1)) }
    FileHandle.standardError.write("wrote \(data.count / channels) frames, peak \(String(format: "%.4f", peak))\n".data(using: .utf8)!)
    exit(0)
}
dispatchMain()
