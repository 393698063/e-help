import Foundation

/// 把 text/event-stream 解析成 SSEEvent。
///
/// 解析逻辑与 H5 (`frontend/app/chat-app.tsx` 的 parseSSE + 外层 buffer 切分) 保持一致：
///   1. 用空行 `\n\n` 把数据流切成一个个完整事件块，残缺的一截留在 buffer 等下一次；
///   2. 每个事件块按行解析，`event:` 行取事件名，`data:` 行累积为 JSON。
///
/// 后端每个事件形如：
///   event: message.delta
///   data: {"content":"可以"}
///   （空行）
struct SSEParser {
    /// 累积尚未凑成完整事件（即还没遇到 \n\n）的数据。
    private var buffer = ""

    /// 处理网络层送来的原始数据块：切出完整事件块，剩余半块留待下一次。
    mutating func appendChunk(_ chunk: String) -> [SSEEvent] {
        buffer += chunk
        var events: [SSEEvent] = []
        // 对齐 H5：buffer.split("\n\n") 后 buffer = events.pop()，即只处理完整块。
        while let range = buffer.range(of: "\n\n") {
            let block = String(buffer[..<range.lowerBound])
            buffer.removeSubrange(..<range.upperBound)
            if let event = Self.parseBlock(block) {
                events.append(event)
            }
        }
        return events
    }

    /// 流结束时，把缓冲区里最后一截没有空行结尾的内容当作一个事件块冲刷掉。
    mutating func finish() -> [SSEEvent] {
        let block = buffer
        buffer = ""
        if let event = Self.parseBlock(block) {
            return [event]
        }
        return []
    }

    /// 对齐 H5 parseSSE：按行解析一个事件块。
    private static func parseBlock(_ block: String) -> SSEEvent? {
        var event = "message"
        var dataLines: [String] = []
        // omittingEmptySubsequences: false → 和 JS 的 split("\n") 行为一致，保留空行。
        for rawLine in block.split(separator: "\n", omittingEmptySubsequences: false) {
            let line = String(rawLine)
            if line.hasPrefix("event:") {
                event = String(line.dropFirst(6)).trimmingCharacters(in: .whitespaces)
                continue
            }
            if line.hasPrefix("data:") {
                dataLines.append(String(line.dropFirst(5)).trimmingCharacters(in: .whitespaces))
            }
        }
        guard !dataLines.isEmpty else { return nil }
        let json = dataLines.joined(separator: "\n")
        guard let raw = json.data(using: .utf8),
              let object = try? JSONSerialization.jsonObject(with: raw) as? [String: Any] else {
            return nil
        }
        return SSEEvent(event: event, data: object)
    }
}

/// Serializes access to the mutable parser state from Alamofire's @Sendable stream callback.
final class LockedSSEParser: @unchecked Sendable {
    private var parser = SSEParser()
    private let lock = NSLock()

    func appendChunk(_ chunk: String) -> [SSEEvent] {
        lock.lock()
        defer { lock.unlock() }
        return parser.appendChunk(chunk)
    }

    func finish() -> [SSEEvent] {
        lock.lock()
        defer { lock.unlock() }
        return parser.finish()
    }
}
