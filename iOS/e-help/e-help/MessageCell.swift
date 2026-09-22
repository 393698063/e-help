import UIKit

final class PaddedLabel: UILabel {
    var textInsets = UIEdgeInsets(top: 10, left: 12, bottom: 10, right: 12)
    var maxLayoutWidth: CGFloat = 280

    override func drawText(in rect: CGRect) {
        super.drawText(in: rect.inset(by: textInsets))
    }

    override var intrinsicContentSize: CGSize {
        guard let text, !text.isEmpty else {
            return CGSize(
                width: textInsets.left + textInsets.right,
                height: textInsets.top + textInsets.bottom
            )
        }

        let availableWidth = max(0, maxLayoutWidth - textInsets.left - textInsets.right)
        let constraint = CGSize(width: availableWidth, height: CGFloat.greatestFiniteMagnitude)
        let rect = (text as NSString).boundingRect(
            with: constraint,
            options: [.usesLineFragmentOrigin, .usesFontLeading],
            attributes: [.font: font ?? UIFont.systemFont(ofSize: 15)],
            context: nil
        )

        let width = min(ceil(rect.width), availableWidth) + textInsets.left + textInsets.right
        let height = ceil(rect.height) + textInsets.top + textInsets.bottom
        return CGSize(width: width, height: height)
    }
}

final class MessageCell: UITableViewCell {
    static let reuseIdentifier = "MessageCell"

    private let bubbleLabel = PaddedLabel()
    private var leadingConstraint: NSLayoutConstraint!
    private var trailingConstraint: NSLayoutConstraint!

    override init(style: UITableViewCell.CellStyle, reuseIdentifier: String?) {
        super.init(style: style, reuseIdentifier: reuseIdentifier)
        setup()
    }

    required init?(coder: NSCoder) {
        super.init(coder: coder)
        setup()
    }

    private func setup() {
        backgroundColor = .clear
        contentView.backgroundColor = .clear
        selectionStyle = .none

        bubbleLabel.numberOfLines = 0
        bubbleLabel.font = .systemFont(ofSize: 15)
        bubbleLabel.layer.cornerRadius = 14
        bubbleLabel.layer.masksToBounds = true
        bubbleLabel.translatesAutoresizingMaskIntoConstraints = false
        contentView.addSubview(bubbleLabel)

        leadingConstraint = bubbleLabel.leadingAnchor.constraint(equalTo: contentView.leadingAnchor, constant: 16)
        trailingConstraint = contentView.trailingAnchor.constraint(equalTo: bubbleLabel.trailingAnchor, constant: 16)

        NSLayoutConstraint.activate([
            bubbleLabel.topAnchor.constraint(equalTo: contentView.topAnchor, constant: 6),
            bubbleLabel.bottomAnchor.constraint(equalTo: contentView.bottomAnchor, constant: -6),
        ])
    }

    func configure(with message: ChatMessage, streaming: Bool) {
        let isUser = message.role.isUser
        let suffix = streaming ? "▍" : ""
        bubbleLabel.text = message.content + suffix

        if isUser {
            bubbleLabel.textColor = .appAccentText
            bubbleLabel.backgroundColor = .appAccent
            leadingConstraint.isActive = false
            trailingConstraint.isActive = true
        } else {
            bubbleLabel.textColor = .appTextPrimary
            bubbleLabel.backgroundColor = .appSurface
            trailingConstraint.isActive = false
            leadingConstraint.isActive = true
        }
    }
}
