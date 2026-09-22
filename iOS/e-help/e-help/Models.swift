import Foundation

struct ThreadCreateRequest: Encodable {
    let userId: String?
    let requestId: String?

    enum CodingKeys: String, CodingKey {
        case userId = "user_id"
        case requestId = "request_id"
    }
}

struct ThreadCreateResponse: Decodable {
    let threadId: String
    let createdAt: String?

    enum CodingKeys: String, CodingKey {
        case threadId = "thread_id"
        case createdAt = "created_at"
    }
}

struct ChatRequest: Encodable {
    let threadId: String?
    let userId: String?
    let requestId: String?
    let message: String

    enum CodingKeys: String, CodingKey {
        case threadId = "thread_id"
        case userId = "user_id"
        case requestId = "request_id"
        case message
    }
}

struct ChatMessage {
    enum Role {
        case user
        case assistant

        var isUser: Bool {
            if case .user = self { return true }
            return false
        }
    }

    let role: Role
    var content: String
}

struct SSEEvent {
    let event: String
    let data: [String: Any]
}
