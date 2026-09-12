from __future__ import annotations

from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate

from ..models import QueryContext, RetrievedContext
from ..prompts import CHITCHAT_SYSTEM_PROMPT, RAG_SYSTEM_PROMPT


class AnswerChain:
    def __init__(self, model) -> None:
        self._rag_chain = (
            ChatPromptTemplate.from_messages(
                [
                    ("system", RAG_SYSTEM_PROMPT),
                    (
                        "human",
                        "<retrieved_context>\n{retrieved_context}\n</retrieved_context>\n\n"
                        "用户问题：\n{contextualized_query}",
                    ),
                ]
            )
            | model
            | StrOutputParser() # StrOutputParser() 会从这个消息对象中提取 content，最终得到：content = retrieved_context + 用户问题： + contextualized_query
        )
        ## 这里是在构建一条 LangChain 链，不是立即执行模型调用，而是返回一个链对象
#         构建链的时候：
            # prompt | model | parser
            # 只是把流程定义好；真正执行是在：
            # chain.invoke(...)
        self._chitchat_chain = (
            ChatPromptTemplate.from_messages(
                [
                    ("system", CHITCHAT_SYSTEM_PROMPT),
                    ("human", "{query}"),
                ]
            )
            | model                                 # 这个是调用了什么
            | StrOutputParser()
        )

    def answer_rag(self, context: QueryContext, retrieved: RetrievedContext) -> str:
        return self._rag_chain.invoke(
            {
                "retrieved_context": self._format_retrieved_context(retrieved),
                "contextualized_query": context["contextualized_query"],
            }
        )

    def answer_chitchat(self, query: str) -> str:
        return self._chitchat_chain.invoke({"query": query})

    #声明为静态方法
    @staticmethod
    def _format_retrieved_context(retrieved: RetrievedContext) -> str:
        if not retrieved.chunks:
            return "没有检索到可用上下文。"
        blocks = []
        for index, chunk in enumerate(retrieved.chunks, start=1):
            blocks.append(
                f"[chunk-{index}]\n"
                f"document_id: {chunk.document_id}\n"
                f"chunk_index: {chunk.chunk_index}\n"
                f"score: {chunk.score:.4f}\n"
                f"content:\n{chunk.content}"
            )
        return "\n\n".join(blocks)
