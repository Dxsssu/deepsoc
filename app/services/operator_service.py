import time
import uuid
import json
import traceback
from datetime import datetime
from sqlalchemy import func
from app.models import db, Event, Task, Action, Command, Message
from app.services.llm_service import call_llm, parse_yaml_response
from app.services.prompt_service import PromptService
from app.services.mcp_tool_service import MCPToolService
from app.utils.message_utils import create_standard_message
from app.utils.mq_utils import RabbitMQPublisher
import pika
import yaml
import logging
logger = logging.getLogger(__name__)


def _build_mcp_catalog_index():
    """构建MCP工具索引：用于校验LLM返回的server/tool是否真实存在"""
    valid_pairs = set()
    tool_to_servers = {}
    try:
        catalog = MCPToolService().get_tool_catalog()
        servers = catalog.get("mcp_servers", []) if isinstance(catalog, dict) else []
        for server in servers:
            server_name = str(server.get("name", "")).strip()
            if not server_name:
                continue
            for tool in server.get("tools", []) or []:
                tool_name = str(tool.get("name", "")).strip()
                if not tool_name:
                    continue
                valid_pairs.add((server_name, tool_name))
                tool_to_servers.setdefault(tool_name, set()).add(server_name)
    except Exception as e:
        logger.error(f"加载MCP工具目录失败，将跳过目录校验: {e}")
    return valid_pairs, tool_to_servers


def _is_obvious_semantic_mismatch(action_name: str, tool_name: str) -> bool:
    """识别明显语义错配：日志/认证类动作却使用情报信誉类工具"""
    action_text = (action_name or "").lower()
    tool_text = (tool_name or "").lower()
    if not action_text or not tool_text:
        return False

    log_keywords = ["日志", "log", "payload", "认证", "登录", "审计"]
    intel_keywords = ["reputation", "threat", "intel", "geoip", "whois"]

    return any(k in action_text for k in log_keywords) and any(k in tool_text for k in intel_keywords)


def _build_fallback_message(prefix: str, detail: str = "") -> str:
    text = (detail or "").strip()
    return f"{prefix}: {text}" if text else prefix

def get_pending_actions():
    """获取待处理的动作，按照event_id和round_id分组
    
    Returns:
        字典，键为(event_id, round_id)元组，值为该组的动作列表
    """
    # 查询所有pending状态的动作
    pending_actions = Action.query.filter_by(action_status='pending').order_by(Action.created_at.asc()).all()
    
    # 按照event_id和round_id分组
    grouped_actions = {}
    for action in pending_actions:
        key = (action.event_id, action.round_id)
        if key not in grouped_actions:
            grouped_actions[key] = []
        grouped_actions[key].append(action)
    
    return grouped_actions

def process_action_group(event_id, round_id, actions, publisher: RabbitMQPublisher):
    """处理一组动作
    
    Args:
        event_id: 事件ID
        round_id: 轮次ID
        actions: 动作列表
        publisher: RabbitMQPublisher instance
    """
    logger.info(f"处理事件 {event_id} 轮次 {round_id} 的动作组，共 {len(actions)} 个动作")
    
    # 获取事件信息
    event = Event.query.filter_by(event_id=event_id).first()
    if not event:
        logger.error(f"事件 {event_id} 不存在，无法处理动作组.")
        for action_item in actions:
            action_item.action_status = 'error_event_not_found'
        db.session.commit()
        return
    
    actions_data = []
    for action in actions:
        # 获取关联的任务
        task = Task.query.filter_by(task_id=action.task_id).first()
        task_name = task.task_name if task else "未知任务"
        
        actions_data.append({
            'action_id': action.action_id,
            'action_name': action.action_name,
            'action_type': action.action_type,
            'task_id': action.task_id,
            'task_name': task_name
        })

    request_data = {
        'type': 'generate_commands_by_actions',
        'req_id': str(uuid.uuid4()),
        'res_id': str(uuid.uuid4()),
        'event_id': event_id,
        'event_round': round_id,
        'event_name': event.event_name,
        'event_message': event.message,
        'actions': actions_data
    }
    
    yaml_data = yaml.dump(request_data, allow_unicode=True, default_flow_style=False, indent=2)

    # 构建用户提示词
    user_prompt = f"""
```yaml
{yaml_data}
```

请根据以上动作要求，输出可以供`_executor`通过机器执行的`COMMAND`。
"""
    logger.info(f"Operator User prompt for event {event_id}, round {round_id}:\n{user_prompt}")
    logger.info("--------------------------------")
    
    # 调用大模型
    prompt_service = PromptService('_operator')
    system_prompt = prompt_service.get_system_prompt()

    logger.info(f"请求大模型生成命令：事件 {event_id} - 轮次 {round_id}")

    # 消息1: LLM 请求通知
    llm_req_content = {"text": f"安全操作员正在请求大模型分析行动(Event: {event_id}, Round: {round_id})并生成具体指令。"}
    db_message_llm_req = create_standard_message(
        event_id=event_id, message_from='system', round_id=round_id,
        message_type='operator_llm_request', content_data=llm_req_content
    )
    if db_message_llm_req and publisher:
        try:
            routing_key = f"notifications.frontend.{db_message_llm_req.event_id}.{db_message_llm_req.message_from}.{db_message_llm_req.message_type}"
            publisher.publish_message(message_body=db_message_llm_req.to_dict(), routing_key=routing_key)
            logger.info(f"消息 [Operator LLM Req] {db_message_llm_req.message_id} 已发布. RK: {routing_key}")
        except Exception as e_pub: logger.error(f"发布消息 [Operator LLM Req] {db_message_llm_req.message_id} 失败: {e_pub}"); logger.error(traceback.format_exc())

    response = call_llm(system_prompt, user_prompt)
    logger.info(f"Operator LLM Response for event {event_id}, round {round_id}:\n{response}")
    logger.info("--------------------------------")

    # 解析响应
    parsed_response = parse_yaml_response(response)
    if not parsed_response:
        logger.error(f"Operator解析LLM响应失败 for event {event_id}: {response}")
        error_content = {"text": "安全操作员未能正确解析LLM响应数据。", "original_response": response}
        db_message_parse_err = create_standard_message(
            event_id=event_id, message_from='_operator', round_id=round_id,
            message_type='error_internal', content_data=error_content
        )
        if db_message_parse_err and publisher:
            try:
                routing_key = f"notifications.frontend.{db_message_parse_err.event_id}.{db_message_parse_err.message_from}.{db_message_parse_err.message_type}"
                publisher.publish_message(message_body=db_message_parse_err.to_dict(), routing_key=routing_key)
                logger.info(f"消息 [Operator LLM Parse Err] {db_message_parse_err.message_id} 已发布. RK: {routing_key}")
            except Exception as e_pub: logger.error(f"发布消息 [Operator LLM Parse Err] {db_message_parse_err.message_id} 失败: {e_pub}")
        for action_item in actions:
            action_item.action_status = 'error_llm_parse'
        db.session.commit()
        return
    
    # 消息2: LLM 响应内容通知
    db_message_llm_resp = create_standard_message(
        event_id=event_id, message_from='_operator', round_id=round_id, # Assuming LLM response for operator aligns with current round
        message_type='operator_llm_response', content_data=parsed_response
    )
    if db_message_llm_resp and publisher:
        try:
            routing_key = f"notifications.frontend.{db_message_llm_resp.event_id}.{db_message_llm_resp.message_from}.{db_message_llm_resp.message_type}"
            publisher.publish_message(message_body=db_message_llm_resp.to_dict(), routing_key=routing_key)
            logger.info(f"消息 [Operator LLM Resp] {db_message_llm_resp.message_id} 已发布. RK: {routing_key}")
        except Exception as e_pub: logger.error(f"发布消息 [Operator LLM Resp] {db_message_llm_resp.message_id} 失败: {e_pub}"); logger.error(traceback.format_exc())
    
    process_operator_response(parsed_response, actions, publisher, event_id, round_id)

def process_operator_response(response, actions, publisher: RabbitMQPublisher, event_id: str, round_id: int):
    """处理操作员响应，创建命令，并发送相关通知"""
    response_type = response.get('response_type')
    if response_type == 'COMMAND':
        valid_mcp_pairs, tool_to_servers = _build_mcp_catalog_index()
        commands_data = response.get('commands', [])
        created_command_ids = []
        for command_detail in commands_data:
            action_id = command_detail.get('action_id')
            action = next((a for a in actions if a.action_id == action_id), None)
            if not action:
                logger.error(f"动作 {action_id} (来自LLM command) 在提供的动作列表中未找到。跳过此command。LLM Response: {command_detail}")
                error_content = {"text": f"安全操作员AI尝试为不存在的行动 {action_id} 创建指令。", "details": command_detail}
                db_msg_cmd_err = create_standard_message(event_id=event_id, message_from='_operator', round_id=round_id, message_type='error_command_creation', content_data=error_content)
                if db_msg_cmd_err and publisher: publisher.publish_message(message_body=db_msg_cmd_err.to_dict(), routing_key=f"notifications.frontend.{event_id}._operator.error_command_creation")
                continue
            
            normalized_command_type = command_detail.get('command_type')
            normalized_command_name = command_detail.get('command_name') or ''
            fallback_message = str(command_detail.get('fallback_message') or '').strip()
            normalized_entity = command_detail.get('command_entity', {})
            if not isinstance(normalized_entity, dict):
                normalized_entity = {}

            normalized_params = command_detail.get('command_params', {})
            if not isinstance(normalized_params, dict):
                normalized_params = {}

            def mark_fallback(message_text: str):
                nonlocal normalized_command_type, normalized_command_name
                nonlocal normalized_entity, normalized_params, fallback_message
                normalized_command_type = 'mcp'
                normalized_command_name = 'fallback'
                normalized_entity = {}
                normalized_params = {}
                if not fallback_message:
                    fallback_message = message_text

            if str(normalized_command_name).strip().lower() == 'fallback':
                mark_fallback(_build_fallback_message(
                    "no_suitable_mcp_tool",
                    fallback_message or "operator_declared_fallback"
                ))

            # 统一仅使用mcp：manual/未知类型都转为mcp失败回退模式，并通知Expert
            if normalized_command_type != 'mcp':
                logger.warning(f"Operator返回了非mcp命令类型({normalized_command_type})，系统将自动转换为mcp失败回退模式。")
                mark_fallback(_build_fallback_message(
                    "no_suitable_mcp_tool",
                    f"operator_output_non_mcp_command_type={normalized_command_type}"
                ))

            # MCP目录校验：LLM返回不存在的工具/服务时，转回退，不允许硬选
            raw_server = str(normalized_entity.get('server', '')).strip()
            raw_tool = str(normalized_entity.get('tool', '')).strip()
            if raw_tool:
                if raw_server:
                    if valid_mcp_pairs and (raw_server, raw_tool) not in valid_mcp_pairs:
                        logger.warning(f"Operator返回了无效MCP工具组合: server={raw_server}, tool={raw_tool}")
                        mark_fallback(_build_fallback_message(
                            "no_suitable_mcp_tool",
                            f"tool_not_in_mcp_catalog_or_server_mismatch server={raw_server}, tool={raw_tool}"
                        ))
                else:
                    candidate_servers = list(tool_to_servers.get(raw_tool, []))
                    if len(candidate_servers) == 1:
                        normalized_entity['server'] = candidate_servers[0]
                    elif len(candidate_servers) == 0:
                        logger.warning(f"Operator返回了未注册MCP工具: tool={raw_tool}")
                        mark_fallback(_build_fallback_message(
                            "no_suitable_mcp_tool",
                            f"tool_not_in_mcp_catalog tool={raw_tool}"
                        ))
                    else:
                        logger.warning(f"Operator返回工具缺少server且存在多服务同名工具: tool={raw_tool}")
                        mark_fallback(_build_fallback_message(
                            "no_suitable_mcp_tool",
                            f"ambiguous_tool_server_mapping tool={raw_tool}, servers={candidate_servers}"
                        ))

            # 语义兜底：明显错配时直接回退，避免“为执行而执行”
            resolved_tool = str(normalized_entity.get('tool', '')).strip()
            if resolved_tool and _is_obvious_semantic_mismatch(action.action_name, resolved_tool):
                logger.warning(
                    f"Operator命令与动作语义明显不匹配，转fallback。action={action.action_name}, tool={resolved_tool}"
                )
                mark_fallback(_build_fallback_message(
                    "no_suitable_mcp_tool",
                    f"tool_semantic_mismatch_with_action action={action.action_name}, tool={resolved_tool}"
                ))

            if not normalized_entity.get('tool'):
                if not fallback_message:
                    mark_fallback(_build_fallback_message(
                        "no_suitable_mcp_tool",
                        f"no_matching_tool_for_action action={action.action_name}"
                    ))
                fallback_content = {
                    "text": "Operator未匹配到合适MCP工具，已按mcp失败回退模式反馈Expert。",
                    "event_id": event_id,
                    "round_id": round_id,
                    "task_id": action.task_id,
                    "action_id": action.action_id,
                    "fallback_message": fallback_message,
                    "action_name": action.action_name
                }
                db_msg_fallback = create_standard_message(
                    event_id=event_id,
                    message_from='_operator',
                    round_id=round_id,
                    message_type='operator_tool_fallback_to_expert',
                    content_data=fallback_content
                )
                if db_msg_fallback and publisher:
                    try:
                        routing_key = f"notifications.frontend.{db_msg_fallback.event_id}.{db_msg_fallback.message_from}.{db_msg_fallback.message_type}"
                        publisher.publish_message(message_body=db_msg_fallback.to_dict(), routing_key=routing_key)
                    except Exception as e_pub_fallback:
                        logger.error(f"发布Operator fallback消息失败: {e_pub_fallback}")

            new_command_id = str(uuid.uuid4())
            is_fallback = (normalized_command_name == 'fallback')
            command = Command(
                command_id=new_command_id,
                command_type=normalized_command_type,
                command_name=normalized_command_name or command_detail.get('command_name') or '',
                command_assignee=command_detail.get('command_assignee', '_executor'), # Default to executor
                action_id=action.action_id,
                task_id=action.task_id, # Get task_id from action
                round_id=action.round_id, # Get round_id from action
                event_id=action.event_id, # Get event_id from action
                command_entity=normalized_entity if not is_fallback else {},
                command_params=normalized_params if not is_fallback else {},
                command_result={"fallback_message": fallback_message} if (is_fallback and fallback_message) else None,
                command_status='pending'
            )
            db.session.add(command)
            action.action_status = 'processing'
            created_command_ids.append(new_command_id)

            # 消息3: 新 Command 创建通知
            cmd_created_content = {"text": f"安全操作员已为行动 {action.action_name} (ID: {action.action_id}) 创建新指令: {command.command_name} (ID: {command.command_id})。", "command_details": command.to_dict()}
            db_msg_cmd_new = create_standard_message(event_id=command.event_id, message_from='_operator', round_id=command.round_id, message_type='command_created', content_data=cmd_created_content)
            if db_msg_cmd_new and publisher:
                try:
                    routing_key = f"notifications.frontend.{db_msg_cmd_new.event_id}.{db_msg_cmd_new.message_from}.{db_msg_cmd_new.message_type}"
                    publisher.publish_message(message_body=db_msg_cmd_new.to_dict(), routing_key=routing_key)
                    logger.info(f"消息 [Command Created] {db_msg_cmd_new.message_id} 已发布. RK: {routing_key}")
                except Exception as e_pub: logger.error(f"发布新Command消息失败 for {db_msg_cmd_new.message_id}: {e_pub}"); logger.error(traceback.format_exc())
        
        if created_command_ids:
            db.session.commit()
            logger.info(f"为事件 {event_id} 轮次 {round_id} 创建了 {len(created_command_ids)} 个命令: {created_command_ids}")
        else:
            logger.warning(f"LLM响应类型为COMMAND，但未提供有效commands数据。Event: {event_id}")
    else:
        logger.warning(f"Operator LLM未返回预期的COMMAND响应类型: {response_type}. Event: {event_id}")
        unexpected_resp_content = {"text": f"安全操作员AI返回了未知响应类型: {response_type}。", "llm_response": response}
        db_msg_unexpected = create_standard_message(event_id=event_id, message_from='_operator', round_id=round_id, message_type='llm_unexpected_response', content_data=unexpected_resp_content)
        if db_msg_unexpected and publisher: publisher.publish_message(message_body=db_msg_unexpected.to_dict(), routing_key=f"notifications.frontend.{event_id}._operator.llm_unexpected_response")

def run_operator():
    """运行_operator服务"""
    logger.info("启动_operator服务...")
    
    # 导入Flask应用
    from main import app
    
    publisher = None
    try:
        publisher = RabbitMQPublisher()
        logger.info("RabbitMQ Publisher for Operator initialized.")
        with app.app_context():
            while True:
                try:
                    # 获取待处理动作组
                    grouped_actions = get_pending_actions()
                    
                    if grouped_actions:
                        logger.info(f"Operator发现 {len(grouped_actions)} 组待处理动作")
                        
                        # 处理每组动作
                        for (event_id, round_id), actions in grouped_actions.items():
                            process_action_group(event_id, round_id, actions, publisher)
                            # 一组动作处理完后提交事务，释放锁
                            try:
                                db.session.commit()
                            except Exception as loop_commit_err:
                                logger.error(f"Operator 主循环提交事务失败: {loop_commit_err}")
                                db.session.rollback()
                    else:
                        logger.info("没有待处理动作，等待中...")
                        # 回滚以结束事务，确保下一次能看到最新数据
                        db.session.rollback()
                        time.sleep(5)
                except pika.exceptions.AMQPConnectionError as amqp_err:
                    logger.error(f"Operator服务 RabbitMQ连接错误: {amqp_err}.")
                    time.sleep(10)
                except Exception as e:
                    logger.error(f"Operator服务在动作处理循环中发生错误: {e}")
                    logger.error(traceback.format_exc())
                    time.sleep(5)
    except pika.exceptions.AMQPConnectionError as amqp_startup_err:
        logger.critical(f"Operator服务启动失败：无法连接到RabbitMQ. Error: {amqp_startup_err}")
        logger.critical(traceback.format_exc())
    except Exception as e_startup:
        logger.critical(f"Operator服务启动时发生未知严重错误: {e_startup}")
        logger.critical(traceback.format_exc())
    finally:
        if publisher:
            logger.info("Operator服务正在关闭RabbitMQ publisher...")
            publisher.close()
        logger.info("Operator服务已停止。")

# if __name__ == '__main__':
#     logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
#     run_operator()
