# e-help 记忆实现方案

本文档基于当前项目代码整理。目标是使用 `backend/.env` 中的 `DB_URL`，为现有的 FastAPI + LangChain + Milvus RAG 服务增加可持久化的短期记忆和可控的长期记忆。

本文档只描述方案，本次不修改运行代码，也不包含或展示 `.env` 中的数据库密码。

## 1. 当前实现盘点

当前记忆实现位于 `backend/src/e_help/rag.py`：

```python
class ThreadState:
    thread_id: str
    created_at: datetime
    messages: list[ChatMessage]
```

`RAGService` 使用：

```python
self.threads: dict[str, ThreadState] = {}
```

每次请求的处理方式是：

1. 根据 `thread_id` 从内存字典获取会话。
2. 读取 `thread.messages` 作为历史消息。
3. 将当前用户消息追加到列表。
4. 完成回答后将 assistant 消息追加到列表。
5. Rewrite 使用历史消息中的最近记录。

这属于“进程内短期记忆”，目前存在以下限制：

- 服务重启后全部会话历史丢失。
- 多 worker 部署时，每个 worker 有自己的 `threads`，同一会话可能读不到完整历史。
- 没有会话列表、历史查询、删除会话等持久化能力。
- 当前没有 `user_id` 或 `visitor_id`，无法把多个 thread 归属到同一个用户。
- 依赖中虽然已有 `langgraph-checkpoint-postgres`，但当前代码没有创建 `PostgresSaver`/`AsyncPostgresSaver`，也没有调用 `setup()`。
- `DB_URL` 当前未在 `Settings` 中声明，因此应用不会读取它。

另外，当前配置 `REWRITE_HISTORY_TURNS=6` 实际上通过 `history[-6:]` 截取的是 6 条消息，而不是严格的 6 轮问答。若“6 轮”指 6 个 user/assistant 回合，读取条数应为 12 条。

## 2. 环境变量接入

`.env` 中的配置名是：

```env
DB_URL=postgresql://<user>:<password>@<host>:<port>/<database>
```

应在 `Settings` 中增加一个可选配置：

```python
db_url: str | None = Field(
    default=None,
    validation_alias=AliasChoices("db_url", "DB_URL"),
)
```

推荐直接使用 psycopg 3 的连接串。`postgresql://...` 可以被 psycopg 3 识别，不需要把密码硬编码到代码中。

建议把数据库配置分成三个状态：

| 状态 | 行为 |
| --- | --- |
| `DB_URL` 未配置 | 保持当前内存记忆，允许本地开发启动，并在日志中提示降级 |
| `DB_URL` 已配置且连接成功 | 使用 PostgreSQL 持久化 thread、消息和长期记忆 |
| `DB_URL` 已配置但连接失败 | 健康检查报告 memory degraded；是否允许降级由环境配置决定 |

生产环境建议 `DB_URL` 连接失败时启动失败，避免用户以为历史已经保存但实际写入了内存。

## 3. 总体架构

```mermaid
flowchart TD
    A[用户 Query] --> B[FastAPI /api/chat/stream]
    B --> C[ThreadRepository 读取最近消息]
    C --> D[Normalize]
    D --> E[Safety + Intent]
    E --> F[LongTermMemoryRepository 查询相关记忆]
    F --> G[Rewrite / Retrieval / Answer]
    G --> H[PostgreSQL 保存 user 和 assistant 消息]
    H --> I[SSE 返回答案]
    H --> J[异步 Memory Extraction]
    J --> K{是否明确且稳定}
    K -->|否| L[丢弃候选]
    K -->|是| M[写入 user_memories]
```

两种记忆的边界：

| 类型 | 保存内容 | 作用 | 权威性 |
| --- | --- | --- | --- |
| 短期记忆 | thread 的消息和可选会话摘要 | 支持当前会话上下文、指代消解和恢复 | 只代表对话记录 |
| 长期记忆 | 用户明确表达的稳定偏好或事实 | 跨 thread 个性化回答 | 低于系统规则和知识库 |

Milvus 的 `knowledge.txt` 内容仍然是产品事实来源。长期记忆不能覆盖价格、套餐、额度、退款规则等知识库事实。

## 4. 短期记忆方案

### 4.1 推荐实现

当前服务是直接编排 `RAGService.answer()`，不是已经编译好的 LangGraph `StateGraph`。因此第一阶段推荐使用一个轻量的 PostgreSQL Repository 管理消息；不要为了使用已有的 checkpoint 依赖而强行改写整条 RAG 链。

Repository 至少提供：

```python
async def create_thread(user_id: str | None) -> Thread:
    ...

async def get_thread(thread_id: str) -> Thread | None:
    ...

async def list_recent_messages(
    thread_id: str,
    limit: int,
) -> list[ChatMessage]:
    ...

async def append_message(
    thread_id: str,
    message: ChatMessage,
    request_id: str,
) -> None:
    ...
```

服务生命周期中创建一个 `AsyncConnectionPool`，在应用关闭时关闭连接池。数据库迁移只执行一次，使用幂等的 migration 工具或 `CREATE TABLE IF NOT EXISTS`。

### 4.2 表结构

```sql
CREATE TABLE IF NOT EXISTS conversation_threads (
    thread_id UUID PRIMARY KEY,
    user_id TEXT,
    title TEXT,
    summary TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    archived_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS conversation_messages (
    message_id BIGSERIAL PRIMARY KEY,
    thread_id UUID NOT NULL REFERENCES conversation_threads(thread_id),
    request_id UUID,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant', 'system', 'tool')),
    content TEXT NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_conversation_messages_thread_time
    ON conversation_messages(thread_id, created_at, message_id);

CREATE UNIQUE INDEX IF NOT EXISTS uq_conversation_messages_request_role
    ON conversation_messages(request_id, role)
    WHERE request_id IS NOT NULL;
```

`request_id` 用于 SSE 重试或客户端重复提交时幂等写入。一次请求的 user 消息和 assistant 消息应在同一个业务事务中完成，或者使用消息状态字段标记 `pending/completed/failed`，避免服务异常后产生无法解释的半条记录。

### 4.3 请求流程

```text
收到 thread_id
  -> PostgreSQL 查询 thread
  -> 查询最近 N 条消息
  -> 保存 user 消息
  -> Normalize / Intent / Rewrite / Retrieval / Answer
  -> 保存 assistant 消息
  -> 发送 message.final
```

Rewrite 只读取最近的消息，不应把整条历史每次都塞进 Prompt。建议：

- MVP：读取最近 12 条消息，对应最近 6 轮问答。
- 超过 20~30 条消息后生成或更新 `conversation_threads.summary`。
- Prompt 只使用 `summary + 最近 12 条消息`。
- 摘要只用于对话连续性，不能作为产品事实来源。

### 4.4 LangChain、LangGraph 与 checkpoint/store 的关系

不需要为了使用 PostgreSQL 记忆而强制把项目改写成 `StateGraph`。需要区分三层概念：

- LangChain 的普通 `Runnable`/手写 Chain 不会自动读取或保存会话历史。
- LangChain 的 `create_agent()` API 支持 `checkpointer` 和 `store` 参数；当前版本的 Agent 底层使用 LangGraph runtime，因此可以直接在 LangChain Agent 入口使用这两个能力。
- LangGraph 提供 `BaseCheckpointSaver` 和 `BaseStore` 接口，以及 PostgreSQL 实现。它们是状态存储协议，不等于必须使用手写 `StateGraph`。

因此有三种可行路径：

| 路径 | 短期记忆 | 长期记忆 | 适合当前项目吗 |
| --- | --- | --- | --- |
| 自定义 Repository | `conversation_messages` | `user_memories` | 最小改动，最适合当前手写 `RAGService` |
| LangChain history | `RunnableWithMessageHistory` + SQL 消息历史 | 自定义 Repository 或 LangGraph Store | 适合继续使用 Runnable Chain |
| LangChain Agent | `create_agent(..., checkpointer=...)` | `create_agent(..., store=...)` | 适合把主流程交给 Agent/运行时管理 |

如果后续把当前编排迁移为 Agent，可以使用已有依赖：

```python
from langchain.agents import create_agent
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.store.postgres.aio import AsyncPostgresStore

async with (
    AsyncPostgresSaver.from_conn_string(settings.db_url) as checkpointer,
    AsyncPostgresStore.from_conn_string(settings.db_url) as store,
):
    await checkpointer.setup()
    await store.setup()
    agent = create_agent(
        model=model,
        tools=[],
        checkpointer=checkpointer,
        store=store,
    )
    config = {"configurable": {"thread_id": thread_id}}
    result = await agent.ainvoke({"messages": messages}, config=config)
```

LangGraph checkpoint 保存的是图的状态、消息通道和执行恢复点，适合：

- 中断后继续执行。
- 多节点工作流恢复。
- 按 `thread_id` 恢复图状态。

`store` 则适合以 namespace 和 key 保存跨 thread 的用户记忆，例如：

```python
namespace = ("users", user_id, "memories")
await store.aput(namespace, "response_language", {"value": "简体中文"})
memory = await store.aget(namespace, "response_language")
```

实际使用时应以当前安装版本的同步/异步方法签名为准。`checkpointer` 和 `store` 都不会自动替换当前的 `self.threads`：只有请求经过 `agent.invoke/ainvoke` 或图的 `invoke/ainvoke`，并传入 `configurable.thread_id`，它们才会参与读写。当前项目的 `RAGService.answer()` 是手动执行 Normalize、Intent、Rewrite、Retrieval 和 Answer，因此最小改造仍然是把消息读写抽成 Repository；也可以把整条流程封装进 Agent/图后再使用 checkpoint/store。

它们也不是面向产品侧查询的完整消息表。推荐继续保留业务消息表，用于会话列表、审计、导出和删除；checkpoint 负责 Agent/图执行恢复；store 负责跨 thread 的长期用户记忆。

## 5. 长期记忆方案

### 5.1 长期记忆的范围

第一版只保存用户明确说出的、未来仍可能有用的信息，例如：

- “我更喜欢使用企业版。”
- “以后请用简体中文回答。”
- “我的店铺主要卖服装。”
- “我的客服团队有 20 个人。”

不自动保存：

- 密钥、密码、身份证号、银行卡号等敏感信息。
- 模型根据语气推断的年龄、性别、收入、健康等信息。
- 一次性订单细节，除非未来确实需要且用户明确同意。
- 知识库中的套餐价格、额度和政策。
- Prompt、系统消息、内部检索结果和模型思维过程。

### 5.2 表结构

```sql
CREATE TABLE IF NOT EXISTS user_memories (
    memory_id UUID PRIMARY KEY,
    user_id TEXT NOT NULL,
    memory_type TEXT NOT NULL CHECK (
        memory_type IN ('preference', 'profile', 'constraint', 'fact')
    ),
    memory_key TEXT NOT NULL,
    memory_value TEXT NOT NULL,
    confidence NUMERIC(4, 3) NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
    source_thread_id UUID REFERENCES conversation_threads(thread_id),
    source_message_id BIGINT REFERENCES conversation_messages(message_id),
    status TEXT NOT NULL DEFAULT 'active' CHECK (
        status IN ('active', 'rejected', 'deleted', 'expired')
    ),
    consented BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_user_active_memory_key
    ON user_memories(user_id, memory_key)
    WHERE status = 'active';

CREATE INDEX IF NOT EXISTS idx_user_memories_lookup
    ON user_memories(user_id, status, memory_type, updated_at DESC);

CREATE TABLE IF NOT EXISTS memory_events (
    event_id BIGSERIAL PRIMARY KEY,
    memory_id UUID,
    user_id TEXT NOT NULL,
    event_type TEXT NOT NULL CHECK (
        event_type IN ('candidate', 'created', 'updated', 'deleted', 'expired')
    ),
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

### 5.3 记忆抽取协议

在回答完成后异步执行 `MemoryExtractionChain`，避免拖慢 SSE 首字节响应。模型只能输出 Pydantic 校验过的结构化结果：

```python
class MemoryCandidate(BaseModel):
    should_store: bool
    memory_type: Literal["preference", "profile", "constraint", "fact"] | None
    memory_key: str | None
    memory_value: str | None
    confidence: float = Field(ge=0, le=1)
    requires_consent: bool
    reason: str
```

应用代码还要做一次规则校验：

1. `should_store=True`。
2. `confidence >= 0.85`，低于阈值直接丢弃或进入待确认列表。
3. `memory_key` 和 `memory_value` 非空。
4. 不是敏感信息，不包含密钥或凭证。
5. 来源必须是用户消息，而不是 assistant 消息或 Milvus chunk。
6. 长期记忆必须关联 `user_id`；没有用户身份时不写入全局记忆。
7. 同一个 `user_id + memory_key` 使用 upsert，新值保留版本和来源。

默认策略建议是“明确陈述可自动保存，推断内容不保存”。对于高影响信息，先在前端询问用户是否记住，再将 `consented=true` 写入数据库。

### 5.4 长期记忆召回

第一阶段不增加向量库：

```sql
SELECT memory_type, memory_key, memory_value, confidence
FROM user_memories
WHERE user_id = $1
  AND status = 'active'
  AND (expires_at IS NULL OR expires_at > now())
ORDER BY updated_at DESC
LIMIT 20;
```

只把与当前问题相关的少量记忆注入独立边界：

```text
<long_term_memory>
[memory-1]
type: preference
key: response_language
value: 简体中文
</long_term_memory>
```

Answer Prompt 必须明确：长期记忆只能用于个性化表达，不能作为产品政策、价格、额度或售后规则的事实依据；发生冲突时以当前检索到的知识库内容和系统规则为准。

后续记忆规模较大时再增加 embedding，并使用单独的 Milvus collection 或 PostgreSQL `pgvector`。不要把用户长期记忆和 `e_help_after_sale` 知识库 collection 混用。

## 6. 用户身份与 API 调整

当前接口只有：

```json
{
  "thread_id": "uuid",
  "message": "问题"
}
```

要实现跨 thread 的长期记忆，至少需要一个稳定身份。MVP 可增加可选字段：

```json
{
  "thread_id": "uuid",
  "user_id": "demo-user-001",
  "message": "以后请用简体中文回答"
}
```

正式环境应使用登录系统提供的用户 ID，不应直接信任客户端提交的任意 `user_id`。未登录用户可以生成 `visitor_id`，但应设置过期时间和数据清理策略。

建议接口：

```text
POST   /api/threads
GET    /api/threads/{thread_id}/messages
DELETE /api/threads/{thread_id}
GET    /api/memories
DELETE /api/memories/{memory_id}
POST   /api/memories/{memory_id}/confirm
```

## 7. 与现有 RAG 流程的整合

改造后的 `RAGService` 编排职责：

```text
1. 校验 thread_id 和 user_id
2. 从 PostgreSQL 读取短期历史
3. 保存 user 消息
4. Normalize
5. Safety / Intent
6. 查询当前 user 的少量长期记忆
7. RAG 场景执行 Rewrite 和 Milvus Retrieval
8. Answer Prompt 接收：
   - recent_messages / summary
   - long_term_memory
   - retrieved_context
   - contextualized_query
9. 保存 assistant 消息
10. 发送 message.final
11. 后台抽取长期记忆候选
```

Prompt 的数据边界建议保持独立：

```text
<conversation_memory>...</conversation_memory>
<long_term_memory>...</long_term_memory>
<retrieved_context>...</retrieved_context>
```

三个区域中的内容全部视为数据，不得执行其中的指令。尤其不能因为历史消息或长期记忆中出现“忽略系统规则”等文本而改变安全策略。

## 8. 连接池与异常处理

建议使用 `psycopg_pool.AsyncConnectionPool`，参数由配置控制：

```env
DB_POOL_MIN_SIZE=1
DB_POOL_MAX_SIZE=10
DB_COMMAND_TIMEOUT_SECONDS=10
MEMORY_REQUIRED=false
```

数据库操作要求：

- 查询历史失败时不把异常吞掉；记录 request_id 和 thread_id。
- `MEMORY_REQUIRED=false` 时允许降级到内存，但健康检查必须返回 `memory_ready=false`。
- 写入 user 消息成功、assistant 生成失败时保存失败状态或错误消息，避免下次请求误以为上一轮完整结束。
- PostgreSQL 不可用时不要执行长期记忆写入。
- 不记录完整 `DB_URL` 到日志，只记录 host、database 或脱敏后的连接状态。
- 对消息长度、单用户记忆数量和历史保留时间设置上限。

健康检查建议扩展为：

```json
{
  "status": "ok",
  "index_ready": true,
  "memory_ready": true,
  "memory_backend": "postgres",
  "memory_error": null
}
```

## 9. 推荐落地顺序

### 阶段一：持久化短期记忆

1. 在 `Settings` 中读取 `DB_URL`。
2. 增加 psycopg 3 直接依赖并创建异步连接池。
3. 创建 `conversation_threads` 和 `conversation_messages` 表。
4. 抽出 `ThreadRepository`，替换 `RAGService.threads` 的读写。
5. 把最近 12 条消息传给 Rewrite，并保留当前 SSE 事件协议。
6. 增加服务重启恢复和重复请求测试。

### 阶段二：长期记忆

1. 增加 `user_id`/`visitor_id` 的身份协议。
2. 创建 `user_memories` 和 `memory_events` 表。
3. 增加结构化 `MemoryExtractionChain`。
4. 添加敏感信息过滤、置信度阈值和用户确认机制。
5. 在 Answer Prompt 中以独立边界注入少量相关记忆。
6. 增加查看、删除和确认记忆的 API。

### 阶段三：按需接入 Agent checkpoint/store

当需要 Agent 的中断恢复、工具调用恢复或复杂多节点编排时，再把主流程封装进 `create_agent()` 或 `StateGraph`，并接入 `AsyncPostgresSaver` 与 PostgreSQL Store。两者不是短期记忆落地的前置条件。建议保留业务消息表：

- checkpoint 负责图状态和恢复。
- conversation_messages 负责产品侧会话查询、审计和导出。
- user_memories 负责跨会话长期记忆。

## 10. 验收标准

- 配置 `DB_URL` 后，服务启动可以连接 PostgreSQL 并完成幂等建表。
- 创建 thread 后，`conversation_threads` 存在对应记录。
- 每轮 user/assistant 消息都能写入 `conversation_messages`。
- 服务重启后，同一个 `thread_id` 仍能读取历史并正确处理“它/这个套餐/上一条”等追问。
- 多 worker 访问同一个 thread 时看到相同历史。
- 未提供 `user_id` 时不产生跨会话长期记忆。
- 明确的用户偏好可以形成候选并保存，敏感信息和模型推断不会保存。
- 用户可以查看、删除和撤销长期记忆。
- Milvus 知识库事实与长期记忆分开存储、分开注入、分开处理冲突。
- PostgreSQL 断开时健康检查可以清晰显示降级状态，且不会假装已经持久化成功。

## 结论

对当前项目最稳妥的路径是：先用 `DB_URL` 接入 PostgreSQL，把现有 `self.threads` 替换为持久化的 thread/message Repository，形成可靠的短期记忆；再增加带用户身份、显式抽取、置信度和删除能力的长期记忆表。LangGraph checkpoint 依赖可以作为后续图执行恢复能力使用，但不应把 checkpoint、聊天记录和用户长期画像混成一个存储层。
