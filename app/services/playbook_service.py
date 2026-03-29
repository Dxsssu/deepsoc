import logging
import json
from typing import Dict, Any
from app.models import db, Command, Execution
import uuid

# 导入SOARClient
from app.utils.soar_client import SOARClient

logger = logging.getLogger(__name__)

class PlaybookService:
    def __init__(self):
        self.soar_client = SOARClient()
    
    def execute_playbook(self, command: Command) -> Dict[str, Any]:
        """
        执行SOAR剧本
        
        Args:
            command: 命令对象
        
        Returns:
            执行结果
        """
        try:
            # 获取剧本ID和参数
            playbook_id = command.command_entity.get('playbook_id')
            params = command.command_params or {}
            
            if not playbook_id:
                error_msg = "缺少剧本ID"
                logger.error(error_msg)
                return {
                    "status": "failed",
                    "message": error_msg
                }
            
            # 执行剧本
            logger.info(f"执行剧本: {playbook_id}, 参数: {params}")
            activity_id = self.soar_client.execute_playbook(playbook_id, params)
            
            if not activity_id:
                error_msg = "剧本执行失败，未获取到活动ID"
                logger.error(error_msg)
                return {
                    "status": "failed",
                    "message": error_msg
                }
            
            # 等待剧本执行完成
            result = self.soar_client.wait_for_completion(activity_id)
            
            if not result:
                error_msg = f"剧本执行超时或失败: {activity_id}"
                logger.error(error_msg)
                return {
                    "status": "failed",
                    "message": error_msg
                }
            
            # 记录执行结果
            execution = Execution(
                execution_id=str(uuid.uuid4()),
                command_id=command.command_id,
                action_id=command.action_id,
                task_id=command.task_id,
                event_id=command.event_id,
                round_id=command.round_id,
                execution_result=json.dumps(result),
                execution_summary=f"剧本 {playbook_id} 执行成功",
                execution_status="completed"
            )
            db.session.add(execution)
            db.session.commit()
            
            logger.info(f"剧本 {playbook_id} 执行成功，结果: {result}")
            
            return {
                "status": "success",
                "message": f"剧本 {playbook_id} 执行成功",
                "data": result
            }
            
        except Exception as e:
            error_msg = f"执行剧本时出错: {str(e)}"
            logger.error(error_msg)
            
            # 记录执行失败
            execution = Execution(
                execution_id=str(uuid.uuid4()),
                command_id=command.command_id,
                action_id=command.action_id,
                task_id=command.task_id,
                event_id=command.event_id,
                round_id=command.round_id,
                execution_result=json.dumps({"error": str(e)}),
                execution_summary=error_msg,
                execution_status="failed"
            )
            db.session.add(execution)
            db.session.commit()
            
            return {
                "status": "failed",
                "message": error_msg
            }
