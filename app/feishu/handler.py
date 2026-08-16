"""飞书消息处理主逻辑。"""
import json
from loguru import logger

from app.ai.deepseek_client import chat, is_available, get_fallback_menu
from app.ai.tools import TOOL_REGISTRY, get_tools_for_deepseek
from app.feishu.context import build_system_context
from app.feishu.history import get_history, save_history


async def handle_message(user_text: str, db) -> str:
    """
    处理飞书用户消息。

    流程：
    1. DeepSeek 不可用 → 返回快捷菜单
    2. 组装系统上下文 + 对话历史 → 调 DeepSeek
    3. DeepSeek 返回 tool_calls → Python 执行
    4. 写入类工具不执行，返回确认卡片 JSON
    5. 查询类工具执行，结果发回 DeepSeek 生成自然语言回复
    6. DeepSeek 直接回复 → 直接返回文本
    """
    # 1. DeepSeek 降级
    if not is_available():
        return get_fallback_menu()

    # 2. 组装消息
    system_ctx = await build_system_context(db)
    history_msgs, summary = get_history()

    messages = []
    if summary:
        messages.append({"role": "system", "content": f"[历史对话摘要]\n{summary}"})
    messages.append({"role": "system", "content": system_ctx})
    messages.extend(history_msgs)
    messages.append({"role": "user", "content": user_text})

    tools = get_tools_for_deepseek()

    # 3. 第一轮：DeepSeek 理解意图
    response = chat(messages, tools)
    if not response["ok"]:
        # API 失败，如果是连续失败超过阈值，返回菜单
        if not is_available():
            return get_fallback_menu()
        return f"抱歉，AI 服务调用失败：{response['error']}。请稍候重试。"

    # 4. 无 tool_calls → 纯文字回复
    if not response["tool_calls"]:
        reply = response["content"] or "抱歉，我没有理解你的意思。"
        save_history(user_text, reply)
        return reply

    # 5. 有 tool_calls → 执行
    # 先记录 assistant 的 tool_calls 到历史
    messages.append({
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": tc["id"],
                "type": "function",
                "function": {"name": tc["name"], "arguments": json.dumps(tc["arguments"], ensure_ascii=False)}
            }
            for tc in response["tool_calls"]
        ]
    })

    all_tool_results = []
    write_cards = []

    for tc in response["tool_calls"]:
        tool_name = tc["name"]
        tool_def = TOOL_REGISTRY.get(tool_name)

        if tool_def is None:
            result = {"ok": False, "error": f"未知工具: {tool_name}"}
        else:
            try:
                # 调用工具处理函数，传入 db
                kwargs = dict(tc["arguments"])
                if tool_def.category == "query" or tool_def.category == "admin":
                    kwargs["db"] = db
                else:
                    kwargs["db"] = db  # 写入类也需要 db 做校验

                result_obj = await tool_def.handler(**kwargs)
                result = {
                    "ok": result_obj.success,
                    "data": result_obj.data,
                    "error": result_obj.error,
                }

                # 写入类：收集确认卡片，不实际执行
                if tool_def.category == "write" and result["ok"] and result.get("data", {}).get("action") == "confirm_card":
                    write_cards.append(result["data"])
                    # 改为返回"请用户确认"的信息
                    result["data"] = {"message": "已生成确认卡片，等待用户确认"}

            except Exception as e:
                logger.error(f"Tool {tool_name} error: {e}")
                result = {"ok": False, "error": str(e)}

        all_tool_results.append(result)

        # 每执行完一个工具，追加 tool 消息
        messages.append({
            "role": "tool",
            "tool_call_id": tc["id"],
            "content": json.dumps(result, ensure_ascii=False, default=str),
        })

    # 如果是写入类操作需要确认卡片，直接返回卡片 JSON（飞书客户端渲染为交互卡片）
    if write_cards:
        card_json = _build_confirmation_card(write_cards[0])
        # 不保存对话历史（等用户确认后才算完成一轮）
        return card_json

    # 6. 第二轮：DeepSeek 基于执行结果生成回复
    response2 = chat(messages)  # 无需再传 tools
    reply = response2.get("content") or "操作已完成。"
    save_history(user_text, reply)
    return reply


def _build_confirmation_card(data: dict) -> str:
    """构造飞书确认卡片的 JSON 字符串。"""
    card_type = data.get("card_type", "")

    if card_type == "add_portfolio":
        return json.dumps({
            "msg_type": "interactive",
            "card": {
                "header": {"title": {"tag": "plain_text", "content": "📝 确认添加持仓"}, "template": "blue"},
                "elements": [
                    {"tag": "div", "text": {"tag": "lark_md",
                        "content": f"**{data['stock_name']}** {data['stock_code']}\n"
                                   f"├─ 数量：{data['quantity']} 股\n"
                                   f"├─ 成本价：¥{data['cost_price']}\n"
                                   f"└─ 总成本：¥{data['total_cost']:,.2f}"}},
                    {"tag": "action", "actions": [
                        {"tag": "button", "text": {"tag": "plain_text", "content": "✅ 确认"},
                         "type": "primary",
                         "value": {"action": "confirm_add", "stock_code": data['stock_code'],
                                   "quantity": data['quantity'], "cost_price": data['cost_price']}},
                        {"tag": "button", "text": {"tag": "plain_text", "content": "❌ 取消"},
                         "type": "default",
                         "value": {"action": "cancel"}},
                    ]},
                    {"tag": "note", "elements": [
                        {"tag": "plain_text", "content": "⚠️ 仅供理论学习和理论练习使用，不构成投资建议"}
                    ]},
                ]
            }
        }, ensure_ascii=False)

    if card_type == "set_preference":
        return json.dumps({
            "msg_type": "interactive",
            "card": {
                "header": {"title": {"tag": "plain_text", "content": "⚙️ 确认切换交易偏好"}, "template": "blue"},
                "elements": [
                    {"tag": "div", "text": {"tag": "lark_md",
                        "content": f"将交易偏好切换为：**{data['mode_name']}**\n各策略参数将自动调整。"}},
                    {"tag": "action", "actions": [
                        {"tag": "button", "text": {"tag": "plain_text", "content": "✅ 确认"},
                         "type": "primary", "value": {"action": "confirm_preference", "mode": data['mode']}},
                        {"tag": "button", "text": {"tag": "plain_text", "content": "❌ 取消"},
                         "type": "default", "value": {"action": "cancel"}},
                    ]},
                ]
            }
        }, ensure_ascii=False)

    # 默认返回简单文本
    return json.dumps({"msg_type": "text", "content": {"text": json.dumps(data, ensure_ascii=False)}},
                      ensure_ascii=False)
