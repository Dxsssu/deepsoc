import time
import uuid
import traceback
from app.models import db, Event, Task, Summary
from app.services.llm_service import call_llm, parse_yaml_response
from app.services.prompt_service import PromptService
from app.services.ttt_service import (
    apply_status_updates_from_candidate,
    build_ttt_from_task_list,
    get_latest_ttt_snapshot,
    has_open_work,
    normalize_ttt_tree,
    save_ttt_snapshot,
)
from app.utils.message_utils import create_standard_message
from app.utils.mq_utils import RabbitMQPublisher
import yaml
import pika

import logging
logger = logging.getLogger(__name__)


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
    round_analysis_instruction = ""
    if not is_first_round:
        round_analysis_instruction = """
本轮是第2轮或之后的迭代：
- response_text 需要重点分析上一轮执行结果与证据变化；
- 明确说明哪些判断被验证、哪些存在不确定性；
- 然后给出更新后的TTT快照并继续循环；
- 只允许更新已有节点的status，不要新增/删除/重命名节点，不要调整层级结构。
"""
    user_prompt = f"""```yaml
{yaml_data}
```
{last_round_summary_content}
{round_analysis_instruction}
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

    # 结构锁：从已有TTT开始的后续轮次，只允许更新节点状态，不允许改结构
    if latest_ttt_snapshot and isinstance(latest_ttt_tree, dict) and latest_ttt_tree.get("root_nodes"):
        normalized_ttt = apply_status_updates_from_candidate(
            base_tree_json=latest_ttt_tree,
            candidate_tree_json=normalized_ttt,
            event_id=event.event_id,
            round_id=response_round_id,
        )
        logger.info(
            f"事件 {event.event_id} R{response_round_id}: 已启用TTT结构锁，仅同步节点状态。"
        )

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
