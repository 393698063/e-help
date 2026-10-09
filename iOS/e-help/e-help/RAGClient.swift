import Alamofire
import Foundation

final class RAGClient {
    private let baseURL: URL
    private let session: Session

    init(baseURL: URL = APIConfig.baseURL) {
        self.baseURL = baseURL

        let configuration = URLSessionConfiguration.default
        configuration.timeoutIntervalForRequest = 30
        configuration.timeoutIntervalForResource = 180
        self.session = Session(configuration: configuration)
    }

    /// 新建会话，对应前端 POST /api/threads。
    func createThread(userId: String?, requestId: String) async throws -> ThreadCreateResponse {
        let payload = ThreadCreateRequest(userId: userId, requestId: requestId)
        let url = baseURL.appendingPathComponent("api/threads")
        return try await session.request(
            url,
            method: .post,
            parameters: payload,
            encoder: JSONParameterEncoder.default
        )
        .validate()
        .serializingDecodable(ThreadCreateResponse.self)
        .value
    }

    /// 流式问答，对应前端 POST /api/chat/stream。
    func streamChat(_ payload: ChatRequest) -> AsyncThrowingStream<SSEEvent, Error> {
        let url = baseURL.appendingPathComponent("api/chat/stream")
        let session = self.session

        return AsyncThrowingStream { continuation in
            let request = session.streamRequest(
                url,
                method: .post,
                parameters: payload,
                encoder: JSONParameterEncoder.default
            )

            let parser = LockedSSEParser()

            request.responseStreamString { stream in
                switch stream.event {
                case .stream(let result):
                    if case .success(let line) = result {
                        for event in parser.appendLine(line) {
                            continuation.yield(event)
                        }
                    }
                case .complete(let completion):
                    if let error = completion.error {
                        continuation.finish(throwing: error)
                    } else {
                        for event in parser.finish() {
                            continuation.yield(event)
                        }
                        continuation.finish()
                    }
                }
            }

            continuation.onTermination = { _ in
                request.cancel()
            }
        }
    }
}
