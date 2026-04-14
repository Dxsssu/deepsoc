import time
import os
import sqlite3
import uuid
import traceback
from app.models import db, Event, Task, Summary
from app.services.llm_service import call_llm, parse_yaml_response
from app.services.prompt_service import PromptService
from app.services.ttt_service import (
    build_ttt_from_task_list,
    get_latest_ttt_snapshot,
    has_open_work,
    normalize_ttt_tree,
    save_ttt_snapshot,
    validate_ttt_with_reflector,
)
from app.utils.message_utils import create_standard_message
from app.utils.mq_utils import RabbitMQPublisher
import yaml
import pika

import logging
logger = logging.getLogger(__name__)


def _build_sop_query_text(event: Event) -> str:
    parts = []
    if event.event_name:
        parts.append(f"event_name: {event.event_name}")
    if event.message:
        parts.append(f"event_message: {event.message}")
    if event.context:
        parts.append(f"context: {event.context}")
    if event.source:
        parts.append(f"source: {event.source}")
    if event.severity:
        parts.append(f"severity: {event.severity}")
    return "\n".join(parts).strip()


def _resolve_knowledge_sqlite_path() -> str:
    env_path = os.getenv("KNOWLEDGE_SQLITE_DB_PATH", "").strip()
    if env_path:
        return env_path
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    return os.path.join(project_root, "knowledge_sqlite.db")


def _load_sop_catalog_from_sqlite(limit: int = 100) -> list:
    db_path = _resolve_knowledge_sqlite_path()
    if not os.path.exists(db_path):
        logger.warning(f"SOP SQLite库不存在: {db_path}")
        return []

    conn = None
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT alert_type_key, title, content_md, version
            FROM sop
            WHERE is_active = 1
            ORDER BY id ASC
            LIMIT ?
            """,
            (max(int(limit), 1),),
        )
        rows = cursor.fetchall()
        return [
            {
                "alert_type_key": str(row["alert_type_key"] or "").strip(),
                "title": str(row["title"] or "").strip(),
                "content_md": str(row["content_md"] or "").strip(),
                "version": str(row["version"] or "").strip(),
            }
            for row in rows
            if str(row["alert_type_key"] or "").strip()
        ]
    except Exception as e:
        logger.error(f"加载SOP目录失败: {e}")
        logger.error(traceback.format_exc())
        return []
    finally:
        if conn:
            conn.close()


def _retrieve_sop_guidance_for_event(event: Event, top_k: int = 1) -> dict:
    """基于告警输入 + SOP列表，让LLM选出参考SOP流程。"""
    query_text = _build_sop_query_text(event) or "empty_event"
    sop_catalog = _load_sop_catalog_from_sqlite(limit=200)
    if not sop_catalog:
        return {"query_text": query_text, "sop_catalog_size": 0, "selected_sop": {}, "referenced_flow_md": ""}

    event_payload = {
        "event_name": event.event_name or "",
        "event_message": event.message or "",
        "context": event.context or "",
        "source": event.source or "",
        "severity": event.severity or "",
        "query_text": query_text,
    }
    sop_catalog_payload = [
        {
            "alert_type_key": item.get("alert_type_key", ""),
            "title": item.get("title", ""),
            "content_md": item.get("content_md", ""),
            "version": item.get("version", "1.0.0"),
        }
        for item in sop_catalog
    ]
    event_yaml = yaml.dump(event_payload, allow_unicode=True, default_flow_style=False, indent=2)
    sop_catalog_yaml = yaml.dump(sop_catalog_payload, allow_unicode=True, default_flow_style=False, indent=2)
    event_yaml = "\n".join([f"  {line}" if line.strip() else line for line in event_yaml.splitlines()])
    sop_catalog_yaml = "\n".join([f"  {line}" if line.strip() else line for line in sop_catalog_yaml.splitlines()])

    router_system_prompt = """
你是SOP路由助手。给定一条安全告警和SOP列表，请选出最应参考的SOP，并输出“参考流程”文本。
要求：
- 仅基于输入内容判断，不要编造不存在的SOP。
- 如果无法判断，选择最接近的一条并说明不确定性。
- 只输出YAML，不要输出额外说明。
"""
    router_user_prompt = f"""```yaml
event:
{event_yaml}
sop_catalog:
{sop_catalog_yaml}
```
请输出如下结构：
```yaml
selected_sop:
  alert_type_key: "<从sop_catalog中选择>"
  title: "<从sop_catalog中选择>"
  version: "<可选>"
confidence: <0到1之间的小数>
reasoning: "<简要理由>"
referenced_flow_md: |
  <用于初始化TTT的参考流程Markdown，优先复用所选SOP原文，可做轻微重述>
```
"""

    try:
        response = call_llm(router_system_prompt, router_user_prompt, temperature=0.1)
        parsed = parse_yaml_response(response) if response else None
        parsed = parsed if isinstance(parsed, dict) else {}

        selected_sop = parsed.get("selected_sop") if isinstance(parsed.get("selected_sop"), dict) else {}
        selected_key = str(selected_sop.get("alert_type_key", "")).strip()
        selected_title = str(selected_sop.get("title", "")).strip()

        selected_doc = None
        if selected_key:
            selected_doc = next((x for x in sop_catalog if x.get("alert_type_key") == selected_key), None)
        if not selected_doc and selected_title:
            selected_doc = next((x for x in sop_catalog if x.get("title") == selected_title), None)
        if not selected_doc:
            selected_doc = sop_catalog[0]

        referenced_flow_md = str(parsed.get("referenced_flow_md", "")).strip()
        if not referenced_flow_md:
            referenced_flow_md = str(selected_doc.get("content_md", "")).strip()

        return {
            "query_text": query_text,
            "sop_catalog_size": len(sop_catalog),
            "selected_sop": {
                "alert_type_key": selected_doc.get("alert_type_key", ""),
                "title": selected_doc.get("title", ""),
                "version": selected_doc.get("version", ""),
            },
            "confidence": parsed.get("confidence"),
            "reasoning": str(parsed.get("reasoning", "")).strip(),
            "referenced_flow_md": referenced_flow_md,
        }
    except Exception as e:
        logger.error(f"事件 {event.event_id} 基于LLM路由SOP失败: {e}")
        logger.error(traceback.format_exc())
        fallback = sop_catalog[0] if sop_catalog else {}
        return {
            "query_text": query_text,
            "sop_catalog_size": len(sop_catalog),
            "selected_sop": {
                "alert_type_key": fallback.get("alert_type_key", ""),
                "title": fallback.get("title", ""),
                "version": fallback.get("version", ""),
            },
            "referenced_flow_md": str(fallback.get("content_md", "")).strip(),
            "error": str(e),
        }


def get_events_to_process():
    """获取待处理的安全事件
    
    在新的状态流转设计中，Captain只处理pending状态的事件
    round_finished状态的事件由event_next_round_worker处理并转换为pending
    """
    return Event.query.filter_by(event_status='pending').order_by(Event.created_at.asc()).first()  

def process_event(event, publisher: RabbitMQPublisher):
    """处理单个安全事件
    
    Args:
        event: Event对象
        publisher: RabbitMQPublisher 实例，用于发送消息到队列
    """
    logger.info(f"处理事件: {event.event_id} - {event.event_name}")
    is_first_round = (event.current_round == 1)
    round_id = event.current_round

    # 消息1: LLM 请求通知
    content_for_llm_request_msg = {"text": "Captain正在更新全局溯源任务树(TTT)并制定下一步战略。"}
    db_message_llm_req = create_standard_message(
        event_id=event.event_id,
        message_from='system',
        round_id=round_id,
        message_type='llm_request',
        content_data=content_for_llm_request_msg
    )
    if db_message_llm_req and publisher:
        try:
            routing_key = f"notifications.frontend.{db_message_llm_req.event_id}.{db_message_llm_req.message_from}.{db_message_llm_req.message_type}"
            publisher.publish_message(message_body=db_message_llm_req.to_dict(), routing_key=routing_key)
        except Exception as e_pub:
            logger.error(f"发布消息 [LLM Req] {db_message_llm_req.message_id} 到 RabbitMQ 失败: {e_pub}")
            logger.error(traceback.format_exc())

    # 更新事件状态为处理中
    event.event_status = 'processing'
    db.session.commit()

    latest_ttt_snapshot = get_latest_ttt_snapshot(event.event_id)
    latest_ttt_tree = latest_ttt_snapshot.tree_json if latest_ttt_snapshot else {}
    is_ttt_initialization_round = not bool(latest_ttt_snapshot and latest_ttt_tree and latest_ttt_tree.get("root_nodes"))
    sop_guidance = {}
    if is_ttt_initialization_round:
        sop_guidance = _retrieve_sop_guidance_for_event(event, top_k=1)
        if sop_guidance.get("selected_sop"):
            selected_sop = sop_guidance.get("selected_sop", {})
            logger.info(
                f"事件 {event.event_id} 初始化TTT前LLM选定SOP: "
                f"{selected_sop.get('alert_type_key', 'unknown')} / {selected_sop.get('title', 'unknown')}"
            )
        else:
            logger.info(f"事件 {event.event_id} 初始化TTT前未选出参考SOP，Captain将自主规划。")

    request_data = {
        'type': 'plan_or_update_ttt_by_event',
        'req_id': str(uuid.uuid4()),
        'res_id': str(uuid.uuid4()),
        'event_id': event.event_id,
        'round_id': round_id,
        'event_name': event.event_name if event.event_name else '{ 请根据消息重命名 }',
        'message': event.message,
        'context': event.context if event.context else '无',
        'source': event.source if event.source else '无',
        'severity': event.severity if event.severity else '无',
        'created_at': event.created_at.strftime('%Y-%m-%d %H:%M:%S'),
        'latest_ttt_version': latest_ttt_snapshot.ttt_version if latest_ttt_snapshot else 0,
        'latest_ttt': latest_ttt_tree if latest_ttt_tree else {}
    }
    if is_ttt_initialization_round:
        request_data['sop_guidance'] = sop_guidance

    tasks_history_list = []
    history_tasks_query = Task.query.filter_by(event_id=event.event_id).order_by(Task.created_at.desc()).all()
    for task_item in history_tasks_query:
        tasks_history_list.append({
            "task_id": task_item.task_id,
            "task_name": task_item.task_name,
            "task_type": task_item.task_type,
            "task_status": task_item.task_status,
            "round_id": task_item.round_id,
            "result": task_item.result or {},
            "task_created_at": task_item.created_at.strftime('%Y-%m-%d %H:%M:%S'),
            "task_updated_at": task_item.updated_at.strftime('%Y-%m-%d %H:%M:%S')
        })
    if tasks_history_list:
        request_data['history_tasks'] = tasks_history_list

    last_round_summary_content = ""
    if not is_first_round:
        last_round_summary = Summary.query.filter_by(
            event_id=event.event_id,
            round_id=round_id - 1
        ).order_by(Summary.created_at.desc()).first()
        if last_round_summary:
            last_round_summary_content = f"""
<last_round_summary>
{last_round_summary.event_summary}
</last_round_summary>
"""

    yaml_data = yaml.dump(request_data, allow_unicode=True, default_flow_style=False, indent=2)
    init_ttt_instruction = ""
    if is_ttt_initialization_round:
        init_ttt_instruction = """
本轮是TTT初始化轮次：
- 输入中的 sop_guidance 为“告警输入 + SOP列表”经LLM路由得到的参考结果。
- 初始化TTT时优先参考 sop_guidance.referenced_flow_md。
- 将每个流程步骤映射为可执行的TTT节点（必要时拆分为父子节点）。
- 若SOP与事件细节不完全一致，可做小幅调整，但不得偏离事件事实。
"""

    round_analysis_instruction = ""
    if not is_first_round:
        round_analysis_instruction = """
本轮是第2轮或之后的迭代：
- response_text 需要重点分析上一轮执行结果与证据变化；
- 明确说明哪些判断被验证、哪些存在不确定性；
- 你需要读取最新TTT快照 + 上一轮summary + 历史任务信息，给出更新后的TTT快照并继续循环；
- 允许在必要时调整树结构（新增/合并/拆分/重命名节点）以及更新节点任务信息；
- 但必须遵循“最小改动优先”：能不改结构就不改，能小改就不大改；
- 只有在发现新证据、新假设或旧结构已无法表达当前研判时，才进行结构调整。
"""
    strict_three_layer_instruction = """
TTT结构必须严格为三层树，且只能是：
- L1（战略层 / Phase）：单一核心目标，不得复合目标。
- L2（战术层 / Sub-Goal）：为达成L1而需要验证的假设/子问题，不写具体动作。
- L3（执行层 / Atomic Intent）：具体证据搜集意图或业务动作意图，必须可执行且明确。

强制约束：
- 仅允许 L1 -> L2 -> L3，禁止出现第4层及以上层级。
- L3必须是叶子节点（children必须为空数组）。
- L1/L2不得直接承载可执行动作。
- 每个L3节点必须包含 task_type（query/write/notify）和 assignee（默认_operator）。
- 若需要结构调整，请保持原有node_id尽量稳定，仅为新增节点分配新node_id。
- 结构调整后仍需保证TTT可执行、可追踪、可最小化回滚。
- 已经执行完成（status=done）的节点视为冻结节点，禁止修改其标题、层级、任务信息和状态。
"""
    user_prompt = f"""```yaml
{yaml_data}
```
{last_round_summary_content}
{init_ttt_instruction}
{round_analysis_instruction}
{strict_three_layer_instruction}
你是总规划师，请维护并输出完整TTT（Traceback Task Tree）快照，作为共享黑板给_manager使用。
如果事件已经处置完成，请返回 response_type: MISSION_COMPLETE。
"""
    logger.info(f"User prompt for event {event.event_id}, round {round_id}:\n{user_prompt}")
    logger.info("--------------------------------")

    prompt_service = PromptService('_captain')
    system_prompt = prompt_service.get_system_prompt()
    response = call_llm(system_prompt, user_prompt)

    logger.info(f"LLM Response for event {event.event_id}, round {round_id}:\n{response}")
    logger.info("--------------------------------")

    parsed_response = parse_yaml_response(response)
    if not parsed_response:
        logger.error(f"解析LLM响应失败 for event {event.event_id}: {response}")
        error_content = {"text": "AI指挥官未能正确解析LLM响应数据，请检查日志。", "original_response": response}
        db_message_parse_err = create_standard_message(
            event_id=event.event_id,
            message_from='_captain',
            round_id=round_id,
            message_type='error_internal',
            content_data=error_content
        )
        if db_message_parse_err and publisher:
            try:
                routing_key = f"notifications.frontend.{db_message_parse_err.event_id}.{db_message_parse_err.message_from}.{db_message_parse_err.message_type}"
                publisher.publish_message(message_body=db_message_parse_err.to_dict(), routing_key=routing_key)
            except Exception as e_pub:
                logger.error(f"发布消息 [LLM Parse Err] {db_message_parse_err.message_id} 到 RabbitMQ 失败: {e_pub}")
        event.event_status = 'error_processing'
        db.session.commit()
        return

    response_type = parsed_response.get('response_type')
    response_round_id = parsed_response.get('round_id', round_id)

    # 消息2: LLM 响应内容通知
    db_message_llm_resp = create_standard_message(
        event_id=event.event_id,
        message_from='_captain',
        round_id=response_round_id,
        message_type='llm_response',
        content_data=parsed_response
    )
    if db_message_llm_resp and publisher:
        try:
            routing_key = f"notifications.frontend.{db_message_llm_resp.event_id}.{db_message_llm_resp.message_from}.{db_message_llm_resp.message_type}"
            publisher.publish_message(message_body=db_message_llm_resp.to_dict(), routing_key=routing_key)
        except Exception as e_pub:
            logger.error(f"发布消息 [LLM Resp] {db_message_llm_resp.message_id} 到 RabbitMQ 失败: {e_pub}")
            logger.error(traceback.format_exc())

    event_name_from_llm = parsed_response.get('event_name', event.event_name)
    if event_name_from_llm and event_name_from_llm != event.event_name:
        event.event_name = event_name_from_llm

    if response_type == 'MISSION_COMPLETE':
        event.event_status = 'completed'
        db.session.commit()
        logger.info(f"事件 {event.event_id} 已被Captain标记为 'completed' 基于 LLM 响应.")
        completion_content = {
            "text": f"事件 {event.event_id} ({event.event_name}) 已由AI指挥官分析并标记为完成。",
            "details": parsed_response.get('response_text')
        }
        db_message_completed = create_standard_message(
            event_id=event.event_id,
            message_from='_captain',
            round_id=response_round_id,
            message_type='event_completed_by_captain',
            content_data=completion_content
        )
        if db_message_completed and publisher:
            try:
                routing_key = f"notifications.frontend.{db_message_completed.event_id}.{db_message_completed.message_from}.{db_message_completed.message_type}"
                publisher.publish_message(message_body=db_message_completed.to_dict(), routing_key=routing_key)
            except Exception as e_pub:
                logger.error(f"发布消息 [Event Completed] {db_message_completed.message_id} 到 RabbitMQ 失败: {e_pub}")
        return

    # 新协议优先：ttt 字段为完整树快照
    ttt_tree = (
        parsed_response.get('ttt')
        or parsed_response.get('ttt_tree')
        or parsed_response.get('tree')
    )

    # 兼容旧协议：如果仍返回 TASK，则转换为一层TTT，不再由Captain直接创建Task
    if not isinstance(ttt_tree, dict):
        if parsed_response.get('response_type') == 'TASK' and isinstance(parsed_response.get('tasks'), list):
            ttt_tree = build_ttt_from_task_list(event.event_id, response_round_id, parsed_response.get('tasks', []))
        elif latest_ttt_tree:
            ttt_tree = latest_ttt_tree
        else:
            ttt_tree = build_ttt_from_task_list(event.event_id, response_round_id, [])

    normalized_ttt = normalize_ttt_tree(ttt_tree, event_id=event.event_id, round_id=response_round_id)

    reflector_mode = "init" if is_ttt_initialization_round else "update"
    reflector_report = validate_ttt_with_reflector(
        candidate_tree=normalized_ttt,
        previous_tree=latest_ttt_tree if isinstance(latest_ttt_tree, dict) and latest_ttt_tree else None,
        mode=reflector_mode,
    )

    for warning_item in reflector_report.get("warnings", []):
        logger.warning(f"TTT Reflector warning (event={event.event_id}, round={response_round_id}): {warning_item}")

    if not reflector_report.get("is_valid", False):
        errors = reflector_report.get("errors", [])
        logger.error(
            f"TTT Reflector 校验失败 event={event.event_id}, round={response_round_id}, mode={reflector_mode}: {errors}"
        )

        reject_content = {
            "text": "TTT Reflector 校验失败，本次候选树被拒绝。",
            "mode": reflector_mode,
            "errors": errors,
            "warnings": reflector_report.get("warnings", []),
            "stats": reflector_report.get("stats", {}),
        }
        db_message_reflect_reject = create_standard_message(
            event_id=event.event_id,
            message_from="_captain",
            round_id=response_round_id,
            message_type="ttt_reflector_rejected",
            content_data=reject_content,
        )
        if db_message_reflect_reject and publisher:
            try:
                routing_key = (
                    f"notifications.frontend.{db_message_reflect_reject.event_id}."
                    f"{db_message_reflect_reject.message_from}.{db_message_reflect_reject.message_type}"
                )
                publisher.publish_message(message_body=db_message_reflect_reject.to_dict(), routing_key=routing_key)
            except Exception as e_pub:
                logger.error(f"发布 TTT Reflector 拒绝消息失败: {e_pub}")

        if reflector_mode == "update" and isinstance(latest_ttt_tree, dict) and latest_ttt_tree.get("root_nodes"):
            normalized_ttt = normalize_ttt_tree(
                latest_ttt_tree,
                event_id=event.event_id,
                round_id=response_round_id
            )
            logger.warning(
                f"TTT Reflector 回退到上一版快照 event={event.event_id}, round={response_round_id}, "
                f"source_version={latest_ttt_snapshot.ttt_version if latest_ttt_snapshot else 'N/A'}"
            )
        else:
            event.event_status = 'error_processing'
            db.session.commit()
            return

    snapshot = save_ttt_snapshot(event.event_id, normalized_ttt, updated_by='_captain', auto_commit=False)
    db.session.commit()

    ttt_message_content = {
        "text": f"Captain更新了TTT共享黑板，当前版本: v{snapshot.ttt_version}",
        "ttt_version": snapshot.ttt_version,
        "ttt_schema_version": snapshot.ttt_schema_version,
        "ttt": snapshot.tree_json,
        "response_type": response_type
    }
    db_message_ttt = create_standard_message(
        event_id=event.event_id,
        message_from='_captain',
        round_id=response_round_id,
        message_type='ttt_snapshot',
        content_data=ttt_message_content
    )
    if db_message_ttt and publisher:
        try:
            routing_key = f"notifications.frontend.{db_message_ttt.event_id}.{db_message_ttt.message_from}.{db_message_ttt.message_type}"
            publisher.publish_message(message_body=db_message_ttt.to_dict(), routing_key=routing_key)
        except Exception as e_pub:
            logger.error(f"发布消息 [TTT Snapshot] {db_message_ttt.message_id} 到 RabbitMQ 失败: {e_pub}")

    if not has_open_work(snapshot.tree_json):
        logger.info(f"事件 {event.event_id} TTT中没有待办节点，标记为completed。")
        event.event_status = 'completed'
        db.session.commit()

def run_captain():
    """运行Captain服务"""
    logger.info("启动Captain服务...")
    
    from main import app # For app_context
    
    publisher = None
    try:
        publisher = RabbitMQPublisher() # Initialize publisher
        logger.info("RabbitMQ Publisher for Captain initialized.")
        
        with app.app_context(): # Ensure DB operations are within app context
            while True:
                try:
                    event = get_events_to_process()
                    if event:
                        process_event(event, publisher) # Pass publisher to process_event
                        # 每处理完一个事件后提交/回滚一次，确保事务结束，释放行锁，下一轮查询能看到最新数据
                        try:
                            db.session.commit()
                        except Exception as loop_commit_err:
                            logger.error(f"Captain 主循环提交事务失败: {loop_commit_err}")
                            db.session.rollback()
                    else:
                        # logger.debug("Captain: 没有待处理事件，等待中...") # reduce noise
                        # 如果本轮没有事件，也显式地回滚事务，避免长事务导致快照不可见
                        db.session.rollback()
                        time.sleep(5)
                except pika.exceptions.AMQPConnectionError as amqp_err:
                    logger.error(f"Captain服务 RabbitMQ连接错误: {amqp_err}. Publisher 会尝试重连。")
                    # Publisher has internal retries for connect and publish, 
                    # so we might just sleep and let the loop continue for it to retry.
                    time.sleep(10) # Wait before next cycle if major MQ error
                except Exception as e:
                    logger.error(f"Captain服务在事件处理循环中发生错误: {e}")
                    logger.error(traceback.format_exc())
                    time.sleep(5) # Wait a bit before retrying the loop
                    
    except pika.exceptions.AMQPConnectionError as amqp_startup_err:
        logger.critical(f"Captain服务启动失败：无法连接到RabbitMQ. 请检查RabbitMQ服务和配置. Error: {amqp_startup_err}")
        logger.critical(traceback.format_exc())
        # Service cannot run without MQ, so exiting or stopping might be an option here
        # For now, it will just log and terminate if __name__ == '__main__' or if called directly.
    except Exception as e_startup:
        logger.critical(f"Captain服务启动时发生未知严重错误: {e_startup}")
        logger.critical(traceback.format_exc())
    finally:
        if publisher:
            logger.info("Captain服务正在关闭RabbitMQ publisher...")
            publisher.close()
        logger.info("Captain服务已停止。")

# This allows running the service independently for testing if needed
# However, typically it's run via main.py -role _captain
# if __name__ == '__main__':
#     # Basic logging config for direct run testing
#     logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
#     run_captain()
