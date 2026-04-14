from typing import Any, Dict

from fastmcp import FastMCP

from app.services.llm_service import call_llm

mcp = FastMCP("explanatory_analysis_mcp")


@mcp.tool(
    name="instructional_analysis",
    description="通用解释/分析工具：根据 instruction（目的）和 input（输入）给出自然语言分析结果",
)
def instructional_analysis(
    instruction: str,
    input: str,
) -> Dict[str, Any]:
    instruction_text = str(instruction or "").strip()
    input_text = str(input or "").strip()
    if not instruction_text:
        return {"status": "failed", "message": "instruction 不能为空"}
    if not input_text:
        return {"status": "failed", "message": "input 不能为空"}

    system_prompt = """
你是一个通用安全解释与分析助手。
你不会假设任务类型，也不会绑定特定场景。你只根据 instruction（目的）和 input（输入）完成分析。
请直接输出自然语言分析结果，不要使用 YAML/JSON/Markdown 代码块格式。
"""
    user_prompt = (
        "请根据以下目的与输入完成解释/分析：\n"
        f"instruction: {instruction_text}\n"
        f"input: {input_text}\n\n"
    )

    try:
        response_text = call_llm(system_prompt, user_prompt, temperature=0.2)
        return {"status": "success", "output_text": str(response_text or "").strip()}
    except Exception as exc:
        return {"status": "failed", "message": f"LLM分析失败: {exc}"}
