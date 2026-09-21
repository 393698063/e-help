"use client";

import { useEffect, useMemo, useRef, useState } from "react";

type ChatMessage = {
  role: "user" | "assistant" | "system";
  content: string;
};

type SSEEvent = {
  event: string;
  data: Record<string, unknown>;
};

const USER_ID_STORAGE_KEY = "e-help:user-id";

function createId(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }

  const bytes = new Uint8Array(16);
  if (typeof crypto !== "undefined" && typeof crypto.getRandomValues === "function") {
    crypto.getRandomValues(bytes);
  } else {
    for (let index = 0; index < bytes.length; index += 1) {
      bytes[index] = Math.floor(Math.random() * 256);
    }
  }

  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;

  const hex = Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0"));
  return [
    hex.slice(0, 4).join(""),
    hex.slice(4, 6).join(""),
    hex.slice(6, 8).join(""),
    hex.slice(8, 10).join(""),
    hex.slice(10, 16).join(""),
  ].join("-");
}

function getOrCreateUserId(): string {
  if (typeof localStorage === "undefined") return "";

  const existing = localStorage.getItem(USER_ID_STORAGE_KEY);
  if (existing) return existing;

  const generated = createId();
  localStorage.setItem(USER_ID_STORAGE_KEY, generated);
  return generated;
}

export default function ChatApp() {
  const [threadId, setThreadId] = useState<string>("");
  const [userId] = useState<string>(() => getOrCreateUserId());
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [inputValue, setInputValue] = useState("");
  const [isSending, setIsSending] = useState(false);
  const [status, setStatus] = useState("准备就绪");
  const [error, setError] = useState("");
  const [draftAssistant, setDraftAssistant] = useState("");
  const listRef = useRef<HTMLDivElement | null>(null);

  const canSend = useMemo(() => inputValue.trim().length > 0 && !isSending, [inputValue, isSending]);

  useEffect(() => {
    listRef.current?.scrollTo({ top: listRef.current.scrollHeight, behavior: "smooth" });
  }, [messages, draftAssistant]);


// const eventSource = new EventSource("/api/events");
// delta 增量
// eventSource.addEventListener("message.delta", (event) => {
//   const data = JSON.parse(event.data);
//   console.log(data);
// });

  async function createThread() {
    setError("");
    setStatus("正在新建对话");
    const response = await fetch("/api/threads", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        user_id: userId || null,
        request_id: createId(),
      }),
    });
    const data = await response.json();
    setThreadId(data.thread_id);
    setMessages([]);
    setDraftAssistant("");
    setStatus("新对话已创建");
  }

  async function sendMessage() {
    if (!canSend) return;
    const text = inputValue.trim();
    setInputValue("");
    setError("");
    setIsSending(true);
    setStatus("正在检索并回答");
    setMessages((current) => [...current, { role: "user", content: text }]);

    let currentThreadId = threadId;
    const payload = {
      thread_id: currentThreadId || null,
      user_id: userId || null,
      // 同一个 request_id 重试时，后端可以复用已经完成的结果。
      request_id: createId(),
      message: text,
    };

    const response = await fetch("/api/chat/stream", {
      method: "POST",
      headers: {
        "content-type": "application/json",
      },
      body: JSON.stringify(payload),
    });

    const reader = response.body?.getReader();
    if (!reader) {
      setError("无法读取服务端响应");
      setIsSending(false);
      return;
    }

    const decoder = new TextDecoder();
    let buffer = "";
    let assistantText = "";

    while (true) {
      /**
       * done: true 不是后端发送的 SSE 字段，也不是通过 parseSSE() 解析出来的，
       * 而是浏览器的 ReadableStreamDefaultReader.read() 返回的状态。
       * reader.read() 每次返回一个 Promise，结果大致是：
      *     {
              value: Uint8Array | undefined,
              done: boolean           # false 表示还有数据未读完
            }
       */
      const { value, done } = await reader.read();
      console.log("接收到SSE事件: ", value, "done:", done)
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      const events = buffer.split("\n\n");
      console.log("事件: ", events);
      buffer = events.pop() ?? "";

      for (const rawEvent of events) {
        const parsed = parseSSE(rawEvent);
        if (!parsed) continue;

        if (parsed.event === "thread.created") {
          const nextThreadId = String(parsed.data.thread_id ?? "");
          if (nextThreadId) {
            currentThreadId = nextThreadId;
            setThreadId(nextThreadId);
          }
        }

        if (parsed.event === "retrieval.completed") {
          setStatus("已检索到上下文");
        }

        if (parsed.event === "tool.started") {
          setStatus(`正在调用 ${String(parsed.data.tool_name ?? "工具")}`);
        }

        if (parsed.event === "tool.completed") {
          setStatus(`已完成 ${String(parsed.data.tool_name ?? "工具")}`);
        }

        if (parsed.event === "tool.failed") {
          setStatus(`工具调用失败：${String(parsed.data.tool_name ?? "未知工具")}`);
        }

        if (parsed.event === "message.delta") {
          console.log("qg-message.delta: ", parsed.data.content);
          assistantText += String(parsed.data.content ?? "");
          setDraftAssistant(assistantText);
        }

        if (parsed.event === "message.final") {
          assistantText = String(parsed.data.content ?? assistantText);
          setDraftAssistant("");
          setMessages((current) => [...current, { role: "assistant", content: assistantText }]);
          setStatus("完成");
        }

        if (parsed.event === "error") {
          setError(String(parsed.data.message ?? "服务出错"));
          setStatus("请求失败");
        }
      }
    }

    setIsSending(false);
  }

  return (
    <main className="flex min-h-screen flex-col bg-slate-950 text-slate-100">
      <header className="flex items-center justify-between border-b border-slate-800 px-4 py-3">
        <div className="flex items-center gap-3">
          <button
            className="rounded-md bg-cyan-500 px-3 py-2 text-sm font-medium text-slate-950 transition hover:bg-cyan-400"
            onClick={createThread}
          >
            新建对话
          </button>
          <div className="text-sm text-slate-400">{threadId ? `thread: ${threadId}` : "未创建对话"}</div>
        </div>
        <div className="text-sm text-slate-400">{status}</div>
      </header>

      <section ref={listRef} className="flex flex-1 flex-col gap-4 overflow-y-auto px-4 py-5">
        {messages.length === 0 && !draftAssistant ? (
          <div className="rounded-lg border border-dashed border-slate-700 p-6 text-sm text-slate-400">
            先新建一个对话，然后发问。
          </div>
        ) : null}

        {messages.map((message, index) => (
          <MessageBubble key={`${message.role}-${index}`} message={message} />
        ))}

        {draftAssistant ? <MessageBubble message={{ role: "assistant", content: draftAssistant }} streaming /> : null}

        {error ? <div className="rounded-md border border-rose-800 bg-rose-950 px-4 py-3 text-sm text-rose-200">{error}</div> : null}
      </section>

      <footer className="border-t border-slate-800 px-4 py-4">
        <form
          className="mx-auto flex max-w-4xl gap-3"
          onSubmit={(event) => {
            event.preventDefault();
            void sendMessage();
          }}
        >
          <textarea
            className="min-h-14 flex-1 resize-none rounded-lg border border-slate-700 bg-slate-900 px-4 py-3 text-sm outline-none placeholder:text-slate-500 focus:border-cyan-500"
            placeholder="输入问题，回车发送"
            value={inputValue}
            onChange={(event) => setInputValue(event.target.value)}
          />
          <button
            className="self-end rounded-lg bg-slate-100 px-4 py-3 text-sm font-medium text-slate-950 transition hover:bg-white disabled:cursor-not-allowed disabled:opacity-50"
            disabled={!canSend}
            type="submit"
          >
            发送
          </button>
        </form>
      </footer>
    </main>
  );
}

function MessageBubble({
  message,
  streaming = false,
}: {
  message: ChatMessage;
  streaming?: boolean;
}) {
  const isUser = message.role === "user";
  return (
    <div className={`flex ${isUser ? "justify-end" : "justify-start"}`}>
      <div
        className={`max-w-3xl rounded-2xl px-4 py-3 text-sm leading-6 ${
          isUser ? "bg-cyan-500 text-slate-950" : "bg-slate-800 text-slate-100"
        }`}
      >
        <div className="mb-1 text-[11px] uppercase tracking-wide text-slate-400">
          {isUser ? "user" : "assistant"}
        </div>
        <div className="whitespace-pre-wrap">{message.content}</div>
        {streaming ? <span className="ml-1 animate-pulse">▍</span> : null}
      </div>
    </div>
  );
}

function parseSSE(chunk: string): SSEEvent | null {
  const lines = chunk.split("\n");
  let event = "message";
  const dataLines: string[] = [];

  for (const line of lines) {
    if (line.startsWith("event:")) {
      event = line.slice(6).trim();
      continue;
    }
    if (line.startsWith("data:")) {
      dataLines.push(line.slice(5).trim());
    }
  }

  if (dataLines.length === 0) return null;

  try {
    return {
      event,
      data: JSON.parse(dataLines.join("\n")),
    };
  } catch {
    return null;
  }
}
