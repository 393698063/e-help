import Foundation

enum APIConfig {
    /// 后端服务地址。
    /// - iOS 模拟器直接访问宿主机时用 127.0.0.1 即可。
    /// - 真机调试请改成电脑的局域网 IP，例如 http://192.168.1.100:8000。
    /// - 也可在 Info.plist 的 APIBaseURL 字段中配置。
    static let baseURL: URL = {
        if let configured = Bundle.main.object(forInfoDictionaryKey: "APIBaseURL") as? String,
           let url = URL(string: configured) {
            return url
        }
        return URL(string: "http://127.0.0.1:8000")!
    }()
}
