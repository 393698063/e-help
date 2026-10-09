import UIKit

final class ViewController: UIViewController {
    private static let userIdKey = "e-help:user-id"

    private var threadId = ""
    private let userId = ViewController.loadOrCreateUserId()
    private var messages: [ChatMessage] = []
    private var draftAssistant = ""
    private var isSending = false
    private var currentTask: Task<Void, Never>?

    private let client = RAGClient()

    private let mainStack = UIStackView()
    private let headerStack = UIStackView()
    private let newThreadButton = UIButton(type: .system)
    private let threadLabel = UILabel()
    private let statusLabel = UILabel()
    private let errorLabel = PaddedLabel()
    private let tableView = UITableView()
    private let inputRow = UIStackView()
    private let inputBackground = UIView()
    private let inputTextView = UITextView()
    private let placeholderLabel = UILabel()
    private let sendButton = UIButton(type: .system)
    private var mainStackBottomConstraint: NSLayoutConstraint!

    override func viewDidLoad() {
        super.viewDidLoad()
        view.backgroundColor = .appBackground

        setupHeader()
        setupErrorLabel()
        setupTableView()
        setupInputBar()
        setupMainStack()

        updateHeader()
        updateSendButtonState()
        
        
        
        // Manually register for trait changes
        let displayScaleTrait: [UITrait] = [UITraitDisplayScale.self]
        registerForTraitChanges(displayScaleTrait) {
            (view: UIView, previousTraitCollection: UITraitCollection) in
//            view.cache.invalidate()
        }
    }
    
    override func viewDidLayoutSubviews() {
        super.viewDidLayoutSubviews()
        if traitCollection.horizontalSizeClass == .regular {
            // 较宽布局：例如双栏
            print("屏幕较宽\(view.frame)")
        } else {
            print("屏幕较窄\(view.frame)")
            // 较窄布局：例如单栏
        }
        
        print("traitCollection.displayScale\(traitCollection.displayScale)")
    }

    deinit {
        NotificationCenter.default.removeObserver(self)
    }

    private func setupHeader() {
        headerStack.axis = .horizontal
        headerStack.alignment = .center
        headerStack.spacing = 12

        newThreadButton.setTitle("新建对话", for: .normal)
        newThreadButton.setTitleColor(.appAccentText, for: .normal)
        newThreadButton.backgroundColor = .appAccent
        newThreadButton.titleLabel?.font = .systemFont(ofSize: 13, weight: .medium)
        newThreadButton.layer.cornerRadius = 8
        newThreadButton.contentEdgeInsets = UIEdgeInsets(top: 6, left: 12, bottom: 6, right: 12)
        newThreadButton.setContentHuggingPriority(.required, for: .horizontal)
        newThreadButton.setContentCompressionResistancePriority(.required, for: .horizontal)
        newThreadButton.addTarget(self, action: #selector(createNewThread), for: .touchUpInside)

        threadLabel.font = .systemFont(ofSize: 12)
        threadLabel.textColor = .appTextMuted
        threadLabel.numberOfLines = 1
        threadLabel.lineBreakMode = .byTruncatingMiddle

        statusLabel.font = .systemFont(ofSize: 12)
        statusLabel.textColor = .appTextMuted
        statusLabel.textAlignment = .right
        statusLabel.numberOfLines = 1
        statusLabel.text = "准备就绪"
        statusLabel.setContentCompressionResistancePriority(.defaultLow, for: .horizontal)

        headerStack.addArrangedSubview(newThreadButton)
        headerStack.addArrangedSubview(threadLabel)
        headerStack.addArrangedSubview(statusLabel)
    }

    private func setupErrorLabel() {
        errorLabel.font = .systemFont(ofSize: 13)
        errorLabel.textColor = .appErrorText
        errorLabel.backgroundColor = .appErrorBackground
        errorLabel.numberOfLines = 0
        errorLabel.textInsets = UIEdgeInsets(top: 8, left: 12, bottom: 8, right: 12)
        errorLabel.maxLayoutWidth = UIScreen.main.bounds.width - 32
        errorLabel.layer.cornerRadius = 8
        errorLabel.layer.masksToBounds = true
        errorLabel.isHidden = true
    }

    private func setupTableView() {
        tableView.backgroundColor = .clear
        tableView.separatorStyle = .none
        tableView.keyboardDismissMode = .interactive
        tableView.rowHeight = UITableView.automaticDimension
        tableView.estimatedRowHeight = 64
        tableView.dataSource = self
        tableView.delegate = self
        tableView.register(MessageCell.self, forCellReuseIdentifier: MessageCell.reuseIdentifier)
        tableView.setContentHuggingPriority(UILayoutPriority(1), for: .vertical)
        tableView.setContentCompressionResistancePriority(UILayoutPriority(1), for: .vertical)
    }

    private func setupInputBar() {
        inputBackground.backgroundColor = .appInput
        inputBackground.layer.cornerRadius = 12
        inputBackground.layer.borderWidth = 1
        inputBackground.layer.borderColor = UIColor.appBorder.cgColor
        inputBackground.heightAnchor.constraint(equalToConstant: 52).isActive = true

        inputTextView.backgroundColor = .clear
        inputTextView.font = .systemFont(ofSize: 15)
        inputTextView.textColor = .appTextPrimary
        inputTextView.textContainerInset = UIEdgeInsets(top: 6, left: 4, bottom: 6, right: 4)
        inputTextView.isScrollEnabled = true
        inputTextView.delegate = self
        inputTextView.translatesAutoresizingMaskIntoConstraints = false
        inputBackground.addSubview(inputTextView)

        placeholderLabel.font = .systemFont(ofSize: 15)
        placeholderLabel.textColor = .appTextMuted
        placeholderLabel.text = "输入问题"
        placeholderLabel.translatesAutoresizingMaskIntoConstraints = false
        inputBackground.addSubview(placeholderLabel)

        sendButton.setTitle("发送", for: .normal)
        sendButton.setTitleColor(.appAccentText, for: .normal)
        sendButton.backgroundColor = .appTextPrimary
        sendButton.titleLabel?.font = .systemFont(ofSize: 14, weight: .medium)
        sendButton.layer.cornerRadius = 12
        sendButton.setContentHuggingPriority(.required, for: .horizontal)
        sendButton.setContentCompressionResistancePriority(.required, for: .horizontal)
        sendButton.addTarget(self, action: #selector(sendTapped), for: .touchUpInside)

        inputRow.axis = .horizontal
        inputRow.alignment = .fill
        inputRow.spacing = 8
        inputRow.addArrangedSubview(inputBackground)
        inputRow.addArrangedSubview(sendButton)

        NSLayoutConstraint.activate([
            sendButton.widthAnchor.constraint(equalToConstant: 64),
            sendButton.heightAnchor.constraint(equalToConstant: 52),

            inputTextView.leadingAnchor.constraint(equalTo: inputBackground.leadingAnchor, constant: 8),
            inputTextView.trailingAnchor.constraint(equalTo: inputBackground.trailingAnchor, constant: -8),
            inputTextView.topAnchor.constraint(equalTo: inputBackground.topAnchor, constant: 4),
            inputTextView.bottomAnchor.constraint(equalTo: inputBackground.bottomAnchor, constant: -4),

            placeholderLabel.leadingAnchor.constraint(equalTo: inputTextView.leadingAnchor, constant: 4),
            placeholderLabel.centerYAnchor.constraint(equalTo: inputTextView.centerYAnchor),
        ])
    }

    private func setupMainStack() {
        mainStack.axis = .vertical
        mainStack.alignment = .fill
        mainStack.spacing = 8
        mainStack.translatesAutoresizingMaskIntoConstraints = false
        view.addSubview(mainStack)

        mainStack.addArrangedSubview(headerStack)
        mainStack.addArrangedSubview(errorLabel)
        mainStack.addArrangedSubview(tableView)
        mainStack.addArrangedSubview(inputRow)

        mainStackBottomConstraint = mainStack.bottomAnchor.constraint(
            equalTo: view.safeAreaLayoutGuide.bottomAnchor,
            constant: -12
        )

        NSLayoutConstraint.activate([
            mainStack.topAnchor.constraint(equalTo: view.safeAreaLayoutGuide.topAnchor, constant: 8),
            mainStack.leadingAnchor.constraint(equalTo: view.leadingAnchor, constant: 16),
            mainStack.trailingAnchor.constraint(equalTo: view.trailingAnchor, constant: -16),
            mainStackBottomConstraint,
        ])

        NotificationCenter.default.addObserver(
            self,
            selector: #selector(keyboardWillChange(_:)),
            name: UIResponder.keyboardWillChangeFrameNotification,
            object: nil
        )
    }

    @objc private func createNewThread() {
        currentTask?.cancel()
        isSending = false
        setError("")
        statusLabel.text = "正在新建对话"

        let requestId = UUID().uuidString.lowercased()
        Task {
            do {
                let response = try await client.createThread(userId: userId, requestId: requestId)
                threadId = response.threadId
                messages.removeAll()
                draftAssistant = ""
                tableView.reloadData()
                statusLabel.text = "新对话已创建"
                updateHeader()
            } catch {
                setError(error.localizedDescription)
                statusLabel.text = "新建对话失败"
            }
            updateSendButtonState()
        }
    }

    @objc private func sendTapped() {
        let text = inputTextView.text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty, !isSending else { return }

        inputTextView.text = ""
        placeholderLabel.isHidden = false
        updateSendButtonState()
        setError("")

        let userMessage = ChatMessage(role: .user, content: text)
        messages.append(userMessage)
        draftAssistant = ""
        isSending = true
        statusLabel.text = "正在检索并回答"
        tableView.reloadData()
        scrollToBottom()

        let payload = ChatRequest(
            threadId: threadId.isEmpty ? nil : threadId,
            userId: userId.isEmpty ? nil : userId,
            requestId: UUID().uuidString.lowercased(),
            message: text
        )

        currentTask?.cancel()
        currentTask = Task { [weak self] in
            guard let self else { return }
            do {
                for try await event in client.streamChat(payload) {
                    handle(event)
                }
            } catch is CancellationError {
                // 用户主动新建对话或取消，忽略。
            } catch {
                setError(error.localizedDescription)
                statusLabel.text = "请求失败"
            }
//            await MainActor.run {
//                [weak self] in
//                self?.updateSendButtonState()
//            }
            isSending = false
            updateSendButtonState()
        }
    }

    @objc private func keyboardWillChange(_ notification: Notification) {
        guard let userInfo = notification.userInfo,
              let endFrame = userInfo[UIResponder.keyboardFrameEndUserInfoKey] as? CGRect,
              let duration = userInfo[UIResponder.keyboardAnimationDurationUserInfoKey] as? TimeInterval else {
            return
        }

        let converted = view.convert(endFrame, from: nil)
        let overlap = max(0, view.bounds.maxY - converted.minY)
        let safeAreaBottom = view.safeAreaInsets.bottom
        let inset = max(0, overlap - safeAreaBottom) + 12

        mainStackBottomConstraint.constant = -inset
        UIView.animate(withDuration: duration) {
            self.view.layoutIfNeeded()
        }
    }

    private func handle(_ event: SSEEvent) {
        switch event.event {
        case "thread.created":
            if let id = event.data["thread_id"] as? String, !id.isEmpty {
                threadId = id
                updateHeader()
            }
        case "intent.completed":
            if let intent = event.data["intent"] as? String {
                statusLabel.text = "意图识别：\(intent)"
            }
        case "rewrite.completed":
            statusLabel.text = "已完成问题改写"
        case "retrieval.completed":
            statusLabel.text = "已检索到上下文"
        case "tool.started":
            let name = event.data["tool_name"] as? String ?? "工具"
            statusLabel.text = "正在调用 \(name)"
        case "tool.completed":
            let name = event.data["tool_name"] as? String ?? "工具"
            statusLabel.text = "已完成 \(name)"
        case "tool.failed":
            let name = event.data["tool_name"] as? String ?? "工具"
            statusLabel.text = "工具调用失败：\(name)"
        case "message.delta":
            if let content = event.data["content"] as? String {
                let wasEmpty = draftAssistant.isEmpty
                draftAssistant += content
                if wasEmpty {
                    tableView.insertRows(at: [IndexPath(row: messages.count, section: 0)], with: .none)
                } else {
                    tableView.reloadRows(at: [IndexPath(row: messages.count, section: 0)], with: .none)
                }
                scrollToBottom()
            }
        case "message.final":
            let final = event.data["content"] as? String ?? draftAssistant
            if !final.isEmpty {
                messages.append(ChatMessage(role: .assistant, content: final))
            }
            draftAssistant = ""
            statusLabel.text = "完成"
            tableView.reloadData()
            scrollToBottom()
        case "error":
            setError(event.data["message"] as? String ?? "服务出错")
            statusLabel.text = "请求失败"
        default:
            break
        }
    }

    private func updateHeader() {
        threadLabel.text = threadId.isEmpty ? "未创建对话" : "thread: \(threadId)"
    }

    private func updateSendButtonState() {
        let hasText = !inputTextView.text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
        let canSend = hasText && !isSending
        sendButton.isEnabled = canSend
        sendButton.alpha = canSend ? 1.0 : 0.5
    }

    private func setError(_ message: String) {
        errorLabel.text = message
        errorLabel.isHidden = message.isEmpty
    }

    private func scrollToBottom(animated: Bool = true) {
        let rowCount = numberOfRows()
        guard rowCount > 0 else { return }
        let indexPath = IndexPath(row: rowCount - 1, section: 0)
        tableView.scrollToRow(at: indexPath, at: .bottom, animated: animated)
    }

    private func numberOfRows() -> Int {
        return messages.count + (draftAssistant.isEmpty ? 0 : 1)
    }

    private func message(at index: Int) -> (ChatMessage, Bool) {
        if index < messages.count {
            return (messages[index], false)
        }
        return (ChatMessage(role: .assistant, content: draftAssistant), true)
    }

    private static func loadOrCreateUserId() -> String {
        if let existing = UserDefaults.standard.string(forKey: userIdKey), !existing.isEmpty {
            return existing
        }
        let generated = UUID().uuidString.lowercased()
        UserDefaults.standard.set(generated, forKey: userIdKey)
        return generated
    }
}

extension ViewController: UITableViewDataSource, UITableViewDelegate {
    func tableView(_ tableView: UITableView, numberOfRowsInSection section: Int) -> Int {
        return numberOfRows()
    }

    func tableView(_ tableView: UITableView, cellForRowAt indexPath: IndexPath) -> UITableViewCell {
        let cell = tableView.dequeueReusableCell(
            withIdentifier: MessageCell.reuseIdentifier,
            for: indexPath
        ) as! MessageCell
        let (message, streaming) = message(at: indexPath.row)
        cell.configure(with: message, streaming: streaming)
        return cell
    }
}

extension ViewController: UITextViewDelegate {
    func textViewDidChange(_ textView: UITextView) {
        placeholderLabel.isHidden = !textView.text.isEmpty
        updateSendButtonState()
    }
}
