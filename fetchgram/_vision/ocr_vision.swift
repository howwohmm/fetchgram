// macOS Vision OCR CLI — reads image paths from stdin (one per line),
// emits one JSON object per line: {"path": <path>, "text": <recognized text>}
// Uses Apple's Vision framework (free, local, high accuracy on designed text).
import Foundation
import Vision
import ImageIO

func loadCG(_ path: String) -> CGImage? {
    guard let src = CGImageSourceCreateWithURL(URL(fileURLWithPath: path) as CFURL, nil) else { return nil }
    return CGImageSourceCreateImageAtIndex(src, 0, nil)
}

func ocr(_ path: String) -> String {
    guard let cg = loadCG(path) else { return "" }
    let request = VNRecognizeTextRequest()
    request.recognitionLevel = .accurate
    request.usesLanguageCorrection = true
    let handler = VNImageRequestHandler(cgImage: cg, options: [:])
    do { try handler.perform([request]) } catch { return "" }
    guard let obs = request.results else { return "" }
    let lines = obs.compactMap { $0.topCandidates(1).first?.string }
    return lines.joined(separator: "\n")
}

// Read all paths first.
var paths: [String] = []
while let line = readLine() {
    let p = line.trimmingCharacters(in: .whitespacesAndNewlines)
    if !p.isEmpty { paths.append(p) }
}

// Process concurrently; print under a lock (order irrelevant — each line carries its path).
let lock = NSLock()
let out = FileHandle.standardOutput
DispatchQueue.concurrentPerform(iterations: paths.count) { i in
    let path = paths[i]
    let text = ocr(path)
    let obj: [String: Any] = ["path": path, "text": text]
    if let data = try? JSONSerialization.data(withJSONObject: obj),
       var s = String(data: data, encoding: .utf8) {
        s += "\n"
        lock.lock()
        out.write(s.data(using: .utf8)!)
        lock.unlock()
    }
}
