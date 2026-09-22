import UIKit

extension UIColor {
    convenience init(hex: UInt32, alpha: CGFloat = 1.0) {
        let red = CGFloat((hex >> 16) & 0xFF) / 255.0
        let green = CGFloat((hex >> 8) & 0xFF) / 255.0
        let blue = CGFloat(hex & 0xFF) / 255.0
        self.init(red: red, green: green, blue: blue, alpha: alpha)
    }

    static let appBackground = UIColor(hex: 0x020617)
    static let appSurface = UIColor(hex: 0x1E293B)
    static let appInput = UIColor(hex: 0x0F172A)
    static let appBorder = UIColor(hex: 0x1E293B)
    static let appTextPrimary = UIColor(hex: 0xF1F5F9)
    static let appTextMuted = UIColor(hex: 0x94A3B8)
    static let appAccent = UIColor(hex: 0x06B6D4)
    static let appAccentText = UIColor(hex: 0x020617)
    static let appErrorText = UIColor(hex: 0xFECDD3)
    static let appErrorBackground = UIColor(hex: 0x4C0519)
}
