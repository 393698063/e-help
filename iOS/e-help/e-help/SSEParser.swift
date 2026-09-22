import Foundation

/// 增量地把 text/event-stream 的每一行解析成 SSEEvent。
///
/// 后端每个事件形如：
///   event: message.delta
///   data: {"content":"..."}
///   （空行）
struct SSEParser {
    private var event = "message"
    private var dataLines: [String] = []

    mutating func appendLine(_ line: String) -> [SSEEvent] {
        if line.isEmpty {
            return finishEvent()
        }
        if line.hasPrefix(":") {
            // SSE 注释行，例如后端的 ": ping" 保活心跳。
            return []
        }
        if line.hasPrefix("event:") {
            // 遇到新的事件头，先结算上一个事件（兼容空行被丢弃的情况）。
            let pending = finishEvent()
            event = Self.fieldValue(line, prefix: "event:")
            return pending
        }
        if line.hasPrefix("data:") {
            dataLines.append(Self.fieldValue(line, prefix: "data:"))
        }
        return []
    }

    mutating func finish() -> [SSEEvent] {
        return finishEvent()
    }

    private static func fieldValue(_ line: String, prefix: String) -> String {
        return String(line.dropFirst(prefix.count)).trimmingCharacters(in: .whitespaces)
    }

    private mutating func finishEvent() -> [SSEEvent] {
        defer {
            event = "message"
            dataLines.removeAll()
        }
        guard !dataLines.isEmpty else { return [] }
        let json = dataLines.joined(separator: "\n")
        guard let raw = json.data(using: .utf8),
              let object = try? JSONSerialization.jsonObject(with: raw) as? [String: Any] else {
            return []
        }
        return [SSEEvent(event: event, data: object)]
    }
}
