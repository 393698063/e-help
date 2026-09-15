from __future__ import annotations

from langchain.agents import create_agent

AGENT_SYSTEM_PROMPT = """你是电商客服助手。
每次收到用户问题时，必须先调用 normalize_query，再调用 analyze_query。
如果 analyze_query 返回 safety_status=block，直接告知用户无法处理该请求，不要调用其他工具。
如果 intent=chitchat，直接自然回答，不要检索知识库。
如果 intent=tool，告知用户该功能暂未开放。
如果 intent=rag 且 need_retrieval=true：
1. 如果存在历史对话，调用 rewrite_query 获取 contextualized_query；
2. 调用 retrieve_knowledge 检索知识库；
3. 只依据检索结果回答，找不到答案时明确说“我不知道”，不要编造。
需要根据用户偏好调整表达时，先调用 recall_user_memory；长期记忆不能替代知识库检索。
长期记忆只用于表达偏好和上下文个性化，不能当作售后知识库事实。"""


def build_agent(model, tools, checkpointer, store):
    return create_agent(
        model=model,
        tools=tools,
        system_prompt=AGENT_SYSTEM_PROMPT,
        checkpointer=checkpointer,
        store=store,
    )
