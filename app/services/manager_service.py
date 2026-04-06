import time
import uuid
import traceback
from app.models import db, Event, Task, Action, Summary
from app.services.llm_service import call_llm, parse_yaml_response
from app.services.prompt_service import PromptService
from app.services.ttt_service import (
    TTT_STATUS_TODO,
    TTT_STATUS_IN_PROGRESS,
    get_latest_ttt_snapshot,
    list_leaf_nodes_by_status,
    save_ttt_snapshot,
    select_next_todo_leaf,
    set_node_status,
)
from app.utils.message_utils import create_standard_message
from app.utils.mq_utils import RabbitMQPublisher
import pika
import yaml
import logging
logger = logging.getLogger(__name__)


def get_pending_tasks():
    """获取待处理的任务，按照event_id和round_id分组
    
    Returns:
        字典，键为(event_id, round_id)元组，值为该组的任务列表
    """
    pending_tasks = Task.query.filter_by(task_status='pending').order_by(Task.created_at.asc()).all()
    grouped_tasks = {}
    for task in pending_tasks:
        key = (task.event_id, task.round_id)
        if key not in grouped_tasks:
            grouped_tasks[key] = []
        grouped_tasks[key].append(task)
    return grouped_tasks


def has_dispatched_task_for_round(event_id: str, round_id: int) -> bool:
    """当前轮次是否已下发过任务（确保每轮最多从TTT抽取1个节点）"""
    round_task_count = Task.query.filter(
        Task.event_id == event_id,
        Task.round_id == round_id
    ).count()
    return round_task_count > 0


def _is_captain_ttt_ready_for_round(event_id: str, round_id: int) -> bool:
    """确保当前轮次的TTT已由Captain更新，避免Manager抢跑"""
    latest_ttt = get_latest_ttt_snapshot(event_id)
    if not latest_ttt:
        return False
    if latest_ttt.updated_by != '_captain':
        return False

    tree_json = latest_ttt.tree_json if isinstance(latest_ttt.tree_json, dict) else {}
    tree_round = tree_json.get("round_id")
    try:
        return int(tree_round) == int(round_id)
    except Exception:
        return False


def select_ttt_node_by_llm(event: Event, round_id: int, todo_nodes: list, publisher: RabbitMQPublisher):
    """让LLM在todo节点中选择一个最高优先级节点"""
    if not todo_nodes:
        return None

    latest_ttt_snapshot = get_latest_ttt_snapshot(event.event_id)
    latest_ttt_tree = latest_ttt_snapshot.tree_json if latest_ttt_snapshot else {}

    last_round_summary_text = ""
    if round_id and round_id > 1:
        last_round_summary = Summary.query.filter(
            Summary.event_id == event.event_id,
            Summary.round_id < round_id
        ).order_by(Summary.round_id.desc(), Summary.created_at.desc()).first()
        if last_round_summary and last_round_summary.event_summary:
            last_round_summary_text = last_round_summary.event_summary

    request_data = {
        "type": "select_highest_priority_ttt_node",
        "req_id": str(uuid.uuid4()),
        "res_id": str(uuid.uuid4()),
        "event_id": event.event_id,
        "event_round": round_id,
        "event_name": event.event_name,
        "event_message": event.message,
        "event_context": event.context if event.context else "无",
        "event_source": event.source if event.source else "无",
        "event_severity": event.severity if event.severity else "无",
        "last_round_summary": last_round_summary_text if last_round_summary_text else "无",
        "latest_ttt": latest_ttt_tree if latest_ttt_tree else {},
        "todo_nodes": [
            {
                "node_id": n.get("node_id"),
                "title": n.get("title"),
                "status": n.get("status"),
                "task_type": n.get("task_type"),
                "assignee": n.get("assignee"),
                "path": n.get("path"),
            }
            for n in todo_nodes
        ],
    }
    yaml_data = yaml.dump(request_data, allow_unicode=True, default_flow_style=False, indent=2)

    selector_user_prompt = f"""```yaml
{yaml_data}
```
当前阶段：NODE_SELECTION
请结合TTT、MCP工具能力、上一轮结果、背景知识，从todo节点中选出最高优先级的一个节点，只能选一个。
必须输出 response_type: NODE_SELECTION。"""

    llm_req_content = {
        "text": f"安全经理正在请求大模型从TTT中选择最高优先级节点(Event: {event.event_id}, Round: {round_id})。"
    }
    db_message_llm_req = create_standard_message(
        event_id=event.event_id,
        message_from='system',
        round_id=round_id,
        message_type='manager_ttt_select_llm_request',
        content_data=llm_req_content
    )
    if db_message_llm_req and publisher:
        try:
            routing_key = f"notifications.frontend.{db_message_llm_req.event_id}.{db_message_llm_req.message_from}.{db_message_llm_req.message_type}"
            publisher.publish_message(message_body=db_message_llm_req.to_dict(), routing_key=routing_key)
        except Exception as e_pub:
            logger.error(f"发布TTT选择请求消息失败: {e_pub}")

    prompt_service = PromptService('_manager')
    system_prompt = prompt_service.get_system_prompt()
    response = call_llm(system_prompt, selector_user_prompt)
    parsed_response = parse_yaml_response(response)

    db_message_llm_resp = create_standard_message(
        event_id=event.event_id,
        message_from='_manager',
        round_id=round_id,
        message_type='manager_ttt_select_llm_response',
        content_data=parsed_response if isinstance(parsed_response, dict) else {"raw_response": response}
    )
    if db_message_llm_resp and publisher:
        try:
            routing_key = f"notifications.frontend.{db_message_llm_resp.event_id}.{db_message_llm_resp.message_from}.{db_message_llm_resp.message_type}"
            publisher.publish_message(message_body=db_message_llm_resp.to_dict(), routing_key=routing_key)
        except Exception as e_pub:
            logger.error(f"发布TTT选择响应消息失败: {e_pub}")

    if not isinstance(parsed_response, dict):
        return None
    if parsed_response.get("response_type") != "NODE_SELECTION":
        logger.warning(f"Manager 节点选择响应类型异常: {parsed_response.get('response_type')}")
        return None
    selected_node_id = parsed_response.get("selected_node_id")
    if not selected_node_id:
        return None

    return next((n for n in todo_nodes if str(n.get("node_id")) == str(selected_node_id)), None)


def dispatch_one_ttt_todo(event: Event, publisher: RabbitMQPublisher) -> bool:
    """从TTT中抽取一个todo叶子节点，转为Task并立即进入现有Manager处理链路"""
    if not event:
        return False

    event_id = event.event_id
    round_id = event.current_round

    if has_dispatched_task_for_round(event_id, round_id):
        return False

    if not _is_captain_ttt_ready_for_round(event_id, round_id):
        logger.debug(
            f"跳过TTT分发：当前轮次TTT尚未由Captain就绪。event={event_id}, round={round_id}"
        )
        return False

    latest_ttt = get_latest_ttt_snapshot(event_id, with_for_update=True)
    if not latest_ttt:
        return False

    todo_nodes = list_leaf_nodes_by_status(latest_ttt.tree_json or {}, TTT_STATUS_TODO)
    if not todo_nodes:
        return False

    selected = select_ttt_node_by_llm(event, round_id, todo_nodes, publisher)
    if not selected:
        logger.warning(f"LLM未能选出TTT节点，回退本地优先级选择。event={event_id}")
        selected = select_next_todo_leaf(latest_ttt.tree_json or {})
    if not selected:
        return False

    updated_tree, updated = set_node_status(
        latest_ttt.tree_json or {},
        selected.get('node_id'),
        TTT_STATUS_IN_PROGRESS,
        extra_fields={
            "claimed_by": "_manager",
            "claimed_round_id": round_id
        }
    )
    if not updated:
        logger.warning(f"TTT节点状态更新失败，event={event_id}, node_id={selected.get('node_id')}")
        db.session.rollback()
        return False

    ttt_snapshot = save_ttt_snapshot(
        event_id=event_id,
        tree_json=updated_tree,
        updated_by='_manager',
        auto_commit=False
    )

    task_id = str(uuid.uuid4())
    task_type = selected.get('task_type') or 'query'
    task_name = selected.get('title') or f"TTT节点任务-{selected.get('node_id', '')}"
    task = Task(
        task_id=task_id,
        event_id=event_id,
        task_name=task_name,
        task_type=task_type,
        task_assignee='_manager',
        task_status='pending',
        round_id=round_id,
        result={
            "ttt_node_id": selected.get('node_id'),
            "ttt_path": selected.get('path'),
            "ttt_version": ttt_snapshot.ttt_version,
            "ttt_selected_by": "_manager"
        }
    )
    db.session.add(task)
    db.session.commit()

    selected_content = {
        "text": f"Manager从TTT中抽取了节点并创建Task: {task_name}",
        "task_id": task.task_id,
        "task_name": task.task_name,
        "task_type": task.task_type,
        "ttt_node_id": selected.get('node_id'),
        "ttt_path": selected.get('path'),
        "ttt_version": ttt_snapshot.ttt_version
    }
    db_message_selected = create_standard_message(
        event_id=event_id,
        message_from='_manager',
        round_id=round_id,
        message_type='ttt_task_selected',
        content_data=selected_content
    )
    if db_message_selected and publisher:
        try:
            routing_key = f"notifications.frontend.{db_message_selected.event_id}.{db_message_selected.message_from}.{db_message_selected.message_type}"
            publisher.publish_message(message_body=db_message_selected.to_dict(), routing_key=routing_key)
        except Exception as e_pub:
            logger.error(f"发布TTT节点抽取消息失败: {e_pub}")

    process_task_group(event_id, round_id, [task], publisher)
    return True

def process_task_group(event_id, round_id, tasks, publisher: RabbitMQPublisher):
    """处理一组任务
    
    Args:
        event_id: 事件ID
        round_id: 轮次ID
        tasks: 任务列表
        publisher: RabbitMQPublisher instance
    """
    logger.info(f"处理事件 {event_id} 轮次 {round_id} 的任务组，共 {len(tasks)} 个任务")
    event = Event.query.filter_by(event_id=event_id).first()
    if not event:
        logger.error(f"事件 {event_id} 不存在，无法处理任务组.")
        # Potentially update task statuses to error or requeue if applicable
        for task_item in tasks:
            task_item.task_status = 'error_event_not_found'
        db.session.commit()
        return

    tasks_data = []
    for task in tasks:
        task_result = task.result if isinstance(task.result, dict) else {}
        tasks_data.append({
            'task_id': task.task_id,
            'task_name': task.task_name,
            'task_type': task.task_type,
            'ttt_node_id': task_result.get('ttt_node_id', ''),
            'ttt_path': task_result.get('ttt_path', '')
        })

    request_data = {
        'type': 'generate_actions_by_tasks',
        'req_id': str(uuid.uuid4()),
        'res_id': str(uuid.uuid4()),
        'event_id': event_id,
        'event_round': round_id,
        'event_name': event.event_name,
        'event_message': event.message,
        'event_context': event.context if event.context else '无',
        'event_source': event.source if event.source else '无',
        'event_severity': event.severity if event.severity else '无',
        'tasks': tasks_data
    }
    latest_ttt_snapshot = get_latest_ttt_snapshot(event_id)
    if latest_ttt_snapshot:
        request_data['latest_ttt_version'] = latest_ttt_snapshot.ttt_version
        request_data['latest_ttt'] = latest_ttt_snapshot.tree_json or {}
    if round_id and round_id > 1:
        last_round_summary = Summary.query.filter(
            Summary.event_id == event_id,
            Summary.round_id < round_id
        ).order_by(Summary.round_id.desc(), Summary.created_at.desc()).first()
        request_data['last_round_summary'] = (
            last_round_summary.event_summary
            if last_round_summary and last_round_summary.event_summary
            else '无'
        )
    yaml_data = yaml.dump(request_data, allow_unicode=True, default_flow_style=False, indent=2)

    user_prompt = f"""```yaml
{yaml_data}
```

分析来自`_captain`的任务要求，生成可供`_operator`操作的具体的`ACTION`。
当前阶段：ACTION_PLANNING
优先输出单条Action；仅当单条无法完成目标时，才输出多条Action。
所有Action必须仅围绕当前task_id，不要扩展到其他节点或无关目标。
Action内容必须与对应TTT节点语义强一致（目标对象、时间范围、日志类型要一致），禁止改题或泛化成不相关查询。
不要指定具体MCP工具名称，只描述要完成的安全动作目标（例如：查询某IP威胁情报、查询某资产归属）。
"""
    logger.info(f"Manager User prompt for event {event_id}, round {round_id}:\n{user_prompt}")
    logger.info("--------------------------------")
    
    prompt_service = PromptService('_manager')
    system_prompt = prompt_service.get_system_prompt()
    logger.info(f"请求大模型进行任务分解：事件 {event_id} - 轮次 {round_id}")
    
    # 消息1: LLM 请求通知
    llm_req_content = {"text": f"安全经理正在请求大模型分析任务(Event: {event_id}, Round: {round_id})并拆分为具体行动。"}
    db_message_llm_req = create_standard_message(
        event_id=event_id,
        message_from='system', # Or '_manager' if manager initiates
        round_id=round_id,
        message_type='manager_llm_request',
        content_data=llm_req_content
    )
    if db_message_llm_req and publisher:
        try:
            routing_key = f"notifications.frontend.{db_message_llm_req.event_id}.{db_message_llm_req.message_from}.{db_message_llm_req.message_type}"
            publisher.publish_message(message_body=db_message_llm_req.to_dict(), routing_key=routing_key)
            logger.info(f"消息 [Manager LLM Req] {db_message_llm_req.message_id} 已发布到 RabbitMQ. RK: {routing_key}")
        except Exception as e_pub:
            logger.error(f"发布消息 [Manager LLM Req] {db_message_llm_req.message_id} 到 RabbitMQ 失败: {e_pub}")
            logger.error(traceback.format_exc())

    response = call_llm(system_prompt, user_prompt)
    logger.info(f"Manager LLM Response for event {event_id}, round {round_id}:\n{response}")
    logger.info("--------------------------------")
    
    parsed_response = parse_yaml_response(response)
    if not parsed_response:
        logger.error(f"Manager解析LLM响应失败 for event {event_id}: {response}")
        error_content = {"text": "安全经理未能正确解析LLM响应数据，请检查日志。", "original_response": response}
        db_message_parse_err = create_standard_message(
            event_id=event_id, message_from='_manager',
            round_id=round_id, message_type='error_internal',
            content_data=error_content
        )
        if db_message_parse_err and publisher:
            try:
                routing_key = f"notifications.frontend.{db_message_parse_err.event_id}.{db_message_parse_err.message_from}.{db_message_parse_err.message_type}"
                publisher.publish_message(message_body=db_message_parse_err.to_dict(), routing_key=routing_key)
                logger.info(f"消息 [Manager LLM Parse Err] {db_message_parse_err.message_id} 已发布到 RabbitMQ. RK: {routing_key}")
            except Exception as e_pub:
                logger.error(f"发布消息 [Manager LLM Parse Err] {db_message_parse_err.message_id} 到 RabbitMQ 失败: {e_pub}")
        # Update task statuses to error
        for task_item in tasks:
            task_item.task_status = 'error_llm_parse'
        db.session.commit()
        return
    
    # 消息2: LLM 响应内容通知
    db_message_llm_resp = create_standard_message(
        event_id=event_id,
        message_from='_manager',
        round_id=round_id, # Assuming LLM response for manager aligns with current round
        message_type='manager_llm_response',
        content_data=parsed_response
    )
    if db_message_llm_resp and publisher:
        try:
            routing_key = f"notifications.frontend.{db_message_llm_resp.event_id}.{db_message_llm_resp.message_from}.{db_message_llm_resp.message_type}"
            publisher.publish_message(message_body=db_message_llm_resp.to_dict(), routing_key=routing_key)
            logger.info(f"消息 [Manager LLM Resp] {db_message_llm_resp.message_id} 已发布到 RabbitMQ. RK: {routing_key}")
        except Exception as e_pub:
            logger.error(f"发布消息 [Manager LLM Resp] {db_message_llm_resp.message_id} 到 RabbitMQ 失败: {e_pub}")
            logger.error(traceback.format_exc()) # Log full traceback for publish errors
    
    process_manager_response(parsed_response, tasks, publisher, event_id, round_id)

def process_manager_response(response, tasks, publisher: RabbitMQPublisher, event_id: str, round_id: int):
    """处理管理员响应，创建动作，并发送相关通知
    
    Args:
        response: 解析后的响应对象
        tasks: 任务列表
        publisher: RabbitMQPublisher instance
        event_id: current event_id for messaging
        round_id: current round_id for messaging
    """
    response_type = response.get('response_type')
    if response_type == 'ACTION':
        actions_data = response.get('actions', [])
        created_action_ids = []
        for action_detail in actions_data:
            task_id = action_detail.get('task_id')
            task = next((t for t in tasks if t.task_id == task_id), None)
            if not task:
                logger.error(f"任务 {task_id} (来自LLM action) 在提供的任务列表中未找到。跳过此action。LLM Response: {action_detail}")
                # Send an error message to frontend about this mismatch
                error_content = {"text": f"安全经理AI尝试为不存在的任务 {task_id} 创建行动，请检查LLM配置或响应。", "details": action_detail}
                db_message_action_err = create_standard_message(
                    event_id=event_id, message_from='_manager', round_id=round_id,
                    message_type='error_action_creation', content_data=error_content)
                if db_message_action_err and publisher:
                    try:
                        routing_key = f"notifications.frontend.{db_message_action_err.event_id}.{db_message_action_err.message_from}.{db_message_action_err.message_type}"
                        publisher.publish_message(message_body=db_message_action_err.to_dict(), routing_key=routing_key)
                    except Exception as e_pub_err: logger.error(f"发布 Action创建错误消息失败: {e_pub_err}")
                continue
            
            new_action_id = str(uuid.uuid4())
            action = Action(
                action_id=new_action_id,
                task_id=task.task_id,
                event_id=task.event_id,
                round_id=task.round_id,
                action_name=action_detail.get('action_name', ''),
                action_type=action_detail.get('action_type', ''),
                action_assignee=action_detail.get('action_assignee', '_operator'),
                action_status='pending'
            )
            db.session.add(action)
            task.task_status = 'processing' # Mark task as processing since actions are created
            created_action_ids.append(new_action_id)
            
            # 消息3: 新 Action 创建通知
            action_created_content = {"text": f"安全经理已为任务 {task.task_name} (ID: {task.task_id}) 创建新行动: {action.action_name} (ID: {action.action_id})。", "action_details": action.to_dict()}
            db_message_action_new = create_standard_message(
                event_id=action.event_id, message_from='_manager',
                round_id=action.round_id, message_type='action_created',
                content_data=action_created_content
            )
            if db_message_action_new and publisher:
                try:
                    routing_key = f"notifications.frontend.{db_message_action_new.event_id}.{db_message_action_new.message_from}.{db_message_action_new.message_type}"
                    publisher.publish_message(message_body=db_message_action_new.to_dict(), routing_key=routing_key)
                    logger.info(f"消息 [Action Created] {db_message_action_new.message_id} 已发布. RK: {routing_key}")
                except Exception as e_pub_new_action:
                    logger.error(f"发布新Action消息失败 for {db_message_action_new.message_id}: {e_pub_new_action}")
                    logger.error(traceback.format_exc())
        
        if created_action_ids:
            db.session.commit()
            logger.info(f"为事件 {event_id} 轮次 {round_id} 创建了 {len(created_action_ids)} 个动作: {created_action_ids}")
        else:
            logger.warning(f"LLM响应类型为ACTION，但未提供有效actions数据或未能匹配任务。Event: {event_id}, Round: {round_id}")
    else:
        logger.warning(f"Manager LLM未返回预期的ACTION响应类型，而是: {response_type}. Event: {event_id}, Round: {round_id}")
        # Send a message about unexpected LLM response type
        unexpected_resp_content = {"text": f"安全经理AI返回了未知的响应类型: {response_type}。", "llm_response": response}
        db_message_unexpected = create_standard_message(
            event_id=event_id, message_from='_manager', round_id=round_id,
            message_type='llm_unexpected_response', content_data=unexpected_resp_content)
        if db_message_unexpected and publisher:
            try:
                routing_key = f"notifications.frontend.{db_message_unexpected.event_id}.{db_message_unexpected.message_from}.{db_message_unexpected.message_type}"
                publisher.publish_message(message_body=db_message_unexpected.to_dict(), routing_key=routing_key)
            except Exception as e_pub_unexp: logger.error(f"发布LLM意外响应类型消息失败: {e_pub_unexp}")

def run_manager():
    """运行_manager服务"""
    logger.info("启动_manager服务...")
    from main import app # For app_context

    publisher = None
    try:
        publisher = RabbitMQPublisher()
        logger.info("RabbitMQ Publisher for Manager initialized.")

        with app.app_context():
            while True:
                try:
                    did_work = False

                    # 新流程：从TTT中抽取一个todo节点，转成任务后走既有动作分解链路
                    processing_events = Event.query.filter_by(event_status='processing').order_by(Event.updated_at.asc()).all()
                    for event in processing_events:
                        if dispatch_one_ttt_todo(event, publisher):
                            did_work = True

                    # 兼容旧流程：如仍存在历史pending任务，一次只处理1条，避免并发下发多任务
                    one_pending_task = Task.query.filter_by(task_status='pending').order_by(Task.created_at.asc()).first()
                    if one_pending_task:
                        did_work = True
                        logger.info("Manager兼容模式处理1条pending任务")
                        process_task_group(
                            one_pending_task.event_id,
                            one_pending_task.round_id,
                            [one_pending_task],
                            publisher
                        )

                    if did_work:
                        try:
                            db.session.commit()
                        except Exception as loop_commit_err:
                            logger.error(f"Manager 主循环提交事务失败: {loop_commit_err}")
                            db.session.rollback()
                    else:
                        db.session.rollback()
                        time.sleep(5)
                except pika.exceptions.AMQPConnectionError as amqp_err:
                    logger.error(f"Manager服务 RabbitMQ连接错误: {amqp_err}. Publisher会尝试重连。")
                    time.sleep(10) 
                except Exception as e:
                    logger.error(f"Manager服务在任务处理循环中发生错误: {e}")
                    logger.error(traceback.format_exc())
                    time.sleep(5)

    except pika.exceptions.AMQPConnectionError as amqp_startup_err:
        logger.critical(f"Manager服务启动失败：无法连接到RabbitMQ. Error: {amqp_startup_err}")
        logger.critical(traceback.format_exc())
    except Exception as e_startup:
        logger.critical(f"Manager服务启动时发生未知严重错误: {e_startup}")
        logger.critical(traceback.format_exc())
    finally:
        if publisher:
            logger.info("Manager服务正在关闭RabbitMQ publisher...")
            publisher.close()
        logger.info("Manager服务已停止。")

# if __name__ == '__main__':
#     logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
#     run_manager() 
