import hashlib
import json
import math
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
from openai import OpenAI
from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    MatchValue,
    PointStruct,
    VectorParams,
)

# 加载环境变量，确保脚本/服务独立运行时可读取配置
load_dotenv()


DEFAULT_SOP_SEED_DOCUMENTS: List[Dict[str, Any]] = [
    {
        "sop_name": "异常登录尝试（服务器/网关）溯源与响应 SOP",
        "sop_index": "异常登录 登录失败 暴力破解 VPN网关 SSH RDP",
        "workflow_steps": [
            {
                "step_name": "阶段一：查询威胁情报（定性攻击源）",
                "step_content": "查询该源IP的外部威胁情报与内部全局历史行为，重点确认是否具备扫描节点/爆破机器/僵尸网络标签、IP地理归属，以及近期是否在内网触发其他安全事件。",
            },
            {
                "step_name": "阶段二：查询资产信息（定性受害目标）",
                "step_content": "根据告警目的IP明确资产属性，重点确认资产重要级别、承载业务系统、责任人，以及是否存在违规开放公网管理端口等暴露面问题。",
            },
            {
                "step_name": "阶段三：搜集告警上下文（刻画攻击行为）",
                "step_content": "调取目标资产告警前后时间段的认证日志与网络流量日志，统计总尝试次数、爆破频率和持续时间，分析尝试账号字典特征，并关联同一时间段该源IP对其他内网资产的横向试探行为。",
            },
            {
                "step_name": "阶段四：失陷判定（绝对红线）",
                "step_content": "在告警后合理时间窗内交叉比对是否出现该源IP登录成功记录；一旦发现成功登录，立即将事件由攻击尝试升级为防线告破/系统失陷，并触发主机级排查（高危命令、可疑子进程、未知外联）。",
            },
            {
                "step_name": "阶段五：研判决策与响应阻断",
                "step_content": "未失陷场景：判定为自动化扫描或未遂攻击，联动防火墙/WAF封禁恶意源IP并结束当前任务树；已失陷场景：立即实施网络微隔离、冻结被爆破成功账号，并升级进入深度应急响应（IR）流程。",
            },
        ],
        "version": "1.0.0",
    },
    {
        "sop_name": "WebShell上传告警溯源与响应 SOP",
        "sop_index": "WebShell 上传 文件落地 命令执行 后门",
        "workflow_steps": [
            {
                "step_name": "阶段一：上传入口核验",
                "step_content": "核验告警对应的上传接口与请求参数，确认是否存在绕过校验的恶意文件上传行为。",
            },
            {
                "step_name": "阶段二：文件落地确认",
                "step_content": "结合Web目录变更与文件完整性日志，确认可疑文件落地路径、创建时间与操作者来源。",
            },
            {
                "step_name": "阶段三：命令执行确认",
                "step_content": "检索Web访问日志和主机进程日志，确认可疑文件是否被访问并触发命令执行。",
            },
            {
                "step_name": "阶段四：影响范围判定",
                "step_content": "排查同主机是否存在新增后门、计划任务、异常账户或可疑外联，评估扩散风险。",
            },
            {
                "step_name": "阶段五：阻断与清理",
                "step_content": "隔离受害主机、删除恶意文件、封堵上传入口并完成同类资产排查与加固。",
            },
        ],
        "version": "1.0.0",
    },
    {
        "sop_name": "SQL注入告警溯源与响应 SOP",
        "sop_index": "SQL注入 注入payload 数据库审计 敏感数据",
        "workflow_steps": [
            {
                "step_name": "阶段一：攻击入口核验",
                "step_content": "还原HTTP请求与参数，确认注入payload是否真实命中业务入口并具备可利用性。",
            },
            {
                "step_name": "阶段二：数据库行为核验",
                "step_content": "调取数据库审计日志，确认告警时间窗内是否存在异常查询、报错回显或高危语句执行。",
            },
            {
                "step_name": "阶段三：数据影响评估",
                "step_content": "评估敏感表读取、批量导出、权限变更等行为，明确潜在数据泄露范围与影响等级。",
            },
            {
                "step_name": "阶段四：失陷判定",
                "step_content": "判断攻击是否已造成数据库侧成功利用或应用侧持久化风险，并确认是否升级为失陷事件。",
            },
            {
                "step_name": "阶段五：阻断与加固",
                "step_content": "实施WAF规则加固、修复注入点、最小化数据库权限并开展同类接口复查。",
            },
        ],
        "version": "1.0.0",
    },
    {
        "sop_name": "可疑外联流量告警溯源与响应 SOP",
        "sop_index": "可疑外联 C2 反连 异常DNS",
        "workflow_steps": [
            {
                "step_name": "阶段一：外联目标情报查询",
                "step_content": "查询外联目标IP/域名的威胁情报与历史信誉，判断是否具备C2或恶意基础设施特征。",
            },
            {
                "step_name": "阶段二：受害主机识别",
                "step_content": "定位发起外联的主机、账户和业务进程，明确外联发生时间窗与会话频次。",
            },
            {
                "step_name": "阶段三：进程与连接关联",
                "step_content": "关联主机进程树、网络连接与DNS日志，确认外联是否由可疑进程持续触发。",
            },
            {
                "step_name": "阶段四：入侵判定",
                "step_content": "综合告警上下文判断是否存在远控落地或数据回传行为，确认事件是否升级为入侵。",
            },
            {
                "step_name": "阶段五：封禁与隔离",
                "step_content": "封禁恶意目标、隔离受害主机并开展IOC扩散排查，完成根因定位与修复。",
            },
        ],
        "version": "1.0.0",
    },
    {
        "sop_name": "内网横向移动告警溯源与响应 SOP",
        "sop_index": "横向移动 远程执行 凭证滥用 SMB WMI",
        "workflow_steps": [
            {
                "step_name": "阶段一：横向路径识别",
                "step_content": "基于源主机、目标主机与时间线识别横向访问链路，确认传播方向与关键跳板。",
            },
            {
                "step_name": "阶段二：凭证与账户核验",
                "step_content": "核查涉及账户的登录方式、权限变化与凭证使用痕迹，识别是否存在凭证滥用。",
            },
            {
                "step_name": "阶段三：多资产日志关联",
                "step_content": "关联认证、进程、文件共享与远程执行日志，确认横向技术手段（如SMB/WMI/远程服务）。",
            },
            {
                "step_name": "阶段四：扩散范围判定",
                "step_content": "确定已受影响资产范围、关键系统触达情况与持续风险，评估是否进入应急状态。",
            },
            {
                "step_name": "阶段五：处置与收敛",
                "step_content": "执行网络隔离、账号重置、凭证轮换与路径封堵，完成全网收敛与后续加固。",
            },
        ],
        "version": "1.0.0",
    },
]

DEFAULT_CASE_SEED_DOCUMENTS: List[Dict[str, Any]] = [
    {
        "id": "case-ssh-bruteforce-001",
        "alert_type": "SSH暴力破解",
        "severity": "high",
        "alert_payload": "源IP在10分钟内触发大量SSH认证失败告警，目标资产为公网暴露运维网关。",
        "actions": {
            "summary": "完成源IP信誉研判、历史行为关联、边界封禁与凭证审计。",
            "ttt_snapshot_ref": "TTT_v5",
        },
        "closed_at": "2026-03-15T18:30:00",
    },
    {
        "id": "case-webshell-002",
        "alert_type": "WebShell上传",
        "severity": "critical",
        "alert_payload": "检测到可疑脚本文件落地并伴随异常Web请求链路。",
        "actions": {
            "summary": "确认文件落地与命令执行链路，完成隔离、清理与入口加固。",
            "ttt_snapshot_ref": "TTT_v7",
        },
        "closed_at": "2026-02-08T09:45:00",
    },
]

DEFAULT_SECURITY_KNOWLEDGE_SEED_DOCUMENTS: List[Dict[str, Any]] = [
    {
        "id": "sk-attack-technique-bruteforce",
        "title": "暴力破解攻击（Brute Force）",
        "content": "暴力破解是攻击者通过高频尝试用户名/密码组合来获取未授权访问权限的攻击方式。",
        "knowledge_type": "attack_technique",
    },
    {
        "id": "sk-cve-2024-example",
        "title": "CVE-2024-0001 示例漏洞说明",
        "content": "该漏洞通常由输入校验不严导致，可能引发远程代码执行或信息泄露风险。",
        "knowledge_type": "cve",
    },
]


class KnowledgeBaseService:
    """基于Qdrant的可扩展溯源知识库服务。"""

    def __init__(self):
        self.qdrant_url = os.getenv("QDRANT_URL", "http://127.0.0.1:6333")
        self.qdrant_api_key = os.getenv("QDRANT_API_KEY") or None
        self.qdrant_timeout = int(os.getenv("QDRANT_TIMEOUT", 30))

        self.embedding_provider = os.getenv("KB_EMBEDDING_PROVIDER", "hash").strip().lower()
        self.embedding_model = os.getenv("EMBEDDING_MODEL", "text-embedding-3-small").strip()
        self.embedding_api_key = os.getenv("EMBEDDING_API_KEY") or os.getenv("LLM_API_KEY")
        self.embedding_base_url = os.getenv("EMBEDDING_BASE_URL") or os.getenv("LLM_BASE_URL")
        self.vector_size = int(os.getenv("KB_VECTOR_SIZE", 384))
        self.default_top_k = int(os.getenv("KB_DEFAULT_TOP_K", 3))
        self.default_score_threshold = float(os.getenv("KB_SCORE_THRESHOLD", 0.9))

        self.default_collection = os.getenv("KB_COLLECTION", "traceback_knowledge")
        self.sop_collection = os.getenv("SOP_KB_COLLECTION", "sop_knowledge_base")
        self.cases_collection = os.getenv("KB_CASES_COLLECTION", "kb_cases")
        self.security_knowledge_collection = os.getenv(
            "KB_SECURITY_KNOWLEDGE_COLLECTION",
            "kb_security_knowledge",
        )

        self.client = QdrantClient(
            url=self.qdrant_url,
            api_key=self.qdrant_api_key,
            timeout=self.qdrant_timeout,
        )
        self._embedding_client: Optional[OpenAI] = None

    def ensure_collection(self, collection_name: str, recreate: bool = False):
        """确保collection存在，可选重建。"""
        exists = self._collection_exists(collection_name)
        if recreate and exists:
            self.client.delete_collection(collection_name=collection_name)
            exists = False

        if not exists:
            self.client.create_collection(
                collection_name=collection_name,
                vectors_config=VectorParams(size=self.vector_size, distance=Distance.COSINE),
            )

    def upsert_sop_knowledge(
        self,
        documents: List[Dict[str, Any]],
        collection_name: Optional[str] = None,
        recreate_collection: bool = False,
    ) -> Dict[str, Any]:
        """写入SOP知识文档。"""
        target_collection = collection_name or self.sop_collection
        return self.upsert_knowledge(
            knowledge_type="sop",
            documents=documents,
            collection_name=target_collection,
            recreate_collection=recreate_collection,
        )

    def search_sop(
        self,
        query_text: str,
        limit: Optional[int] = None,
        collection_name: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """检索SOP知识。"""
        target_collection = collection_name or self.sop_collection
        return self.search_knowledge(
            query_text=query_text,
            knowledge_type="sop",
            limit=limit,
            collection_name=target_collection,
        )

    def search_sop_for_event(
        self,
        event_name: str,
        event_message: str,
        limit: Optional[int] = None,
        collection_name: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """按事件信息检索最相近SOP。"""
        event_query = self._build_event_query_text(event_name=event_name, event_message=event_message)
        return self.search_sop(
            query_text=event_query,
            limit=limit,
            collection_name=collection_name,
        )

    def upsert_case_knowledge(
        self,
        documents: List[Dict[str, Any]],
        collection_name: Optional[str] = None,
        recreate_collection: bool = False,
    ) -> Dict[str, Any]:
        """写入历史案例知识（kb_cases）。"""
        target_collection = collection_name or self.cases_collection
        return self.upsert_knowledge(
            knowledge_type="case",
            documents=documents,
            collection_name=target_collection,
            recreate_collection=recreate_collection,
        )

    def search_case_knowledge(
        self,
        query_text: str,
        alert_type: Optional[str] = None,
        severity: Optional[str] = None,
        limit: Optional[int] = None,
        collection_name: Optional[str] = None,
        score_threshold: Optional[float] = None,
    ) -> List[Dict[str, Any]]:
        """检索历史案例知识（kb_cases）。"""
        target_collection = collection_name or self.cases_collection
        payload_filters: Dict[str, Any] = {}
        if alert_type:
            payload_filters["alert_type"] = alert_type
        if severity:
            payload_filters["severity"] = severity

        return self.search_knowledge(
            query_text=query_text,
            limit=limit,
            collection_name=target_collection,
            payload_filters=payload_filters or None,
            score_threshold=(
                float(score_threshold)
                if score_threshold is not None
                else self.default_score_threshold
            ),
        )

    def upsert_security_knowledge(
        self,
        documents: List[Dict[str, Any]],
        collection_name: Optional[str] = None,
        recreate_collection: bool = False,
    ) -> Dict[str, Any]:
        """写入安全知识/术语（kb_security_knowledge）。"""
        target_collection = collection_name or self.security_knowledge_collection
        return self.upsert_knowledge(
            knowledge_type="security_knowledge",
            documents=documents,
            collection_name=target_collection,
            recreate_collection=recreate_collection,
        )

    def search_security_knowledge(
        self,
        query_text: str,
        knowledge_type: Optional[str] = None,
        limit: Optional[int] = None,
        collection_name: Optional[str] = None,
        score_threshold: Optional[float] = None,
    ) -> List[Dict[str, Any]]:
        """检索安全知识/术语（kb_security_knowledge）。"""
        target_collection = collection_name or self.security_knowledge_collection
        payload_filters: Dict[str, Any] = {}
        if knowledge_type:
            payload_filters["knowledge_type"] = knowledge_type

        return self.search_knowledge(
            query_text=query_text,
            limit=limit,
            collection_name=target_collection,
            payload_filters=payload_filters or None,
            score_threshold=(
                float(score_threshold)
                if score_threshold is not None
                else self.default_score_threshold
            ),
        )

    def upsert_knowledge(
        self,
        knowledge_type: str,
        documents: List[Dict[str, Any]],
        collection_name: Optional[str] = None,
        recreate_collection: bool = False,
    ) -> Dict[str, Any]:
        """写入通用知识文档（当前支持: sop/case/security_knowledge）。"""
        if not documents:
            return {"collection": collection_name or self.default_collection, "upserted": 0, "point_ids": []}

        target_collection = collection_name or self.default_collection
        self.ensure_collection(target_collection, recreate=recreate_collection)

        points: List[PointStruct] = []
        point_ids: List[str] = []

        for doc in documents:
            payload = self._normalize_document(knowledge_type=knowledge_type, raw_doc=doc)
            embedding_text = self._build_embedding_text(payload)
            vector = self._embed_text(embedding_text)
            point_id = self._build_point_id(payload)
            points.append(PointStruct(id=point_id, vector=vector, payload=payload))
            point_ids.append(point_id)

        self.client.upsert(collection_name=target_collection, points=points, wait=True)
        return {"collection": target_collection, "upserted": len(points), "point_ids": point_ids}

    def search_knowledge(
        self,
        query_text: str,
        knowledge_type: Optional[str] = None,
        limit: Optional[int] = None,
        collection_name: Optional[str] = None,
        payload_filters: Optional[Dict[str, Any]] = None,
        score_threshold: Optional[float] = None,
    ) -> List[Dict[str, Any]]:
        """按语义相似度 + payload过滤进行检索。"""
        target_collection = collection_name or self.default_collection
        top_k = limit or self.default_top_k
        query_vector = self._embed_text(query_text)
        threshold = (
            None
            if score_threshold is None
            else max(-1.0, min(1.0, float(score_threshold)))
        )

        must_conditions = []
        if knowledge_type:
            must_conditions.append(
                FieldCondition(key="knowledge_type", match=MatchValue(value=knowledge_type))
            )
        if payload_filters:
            for key, value in payload_filters.items():
                if value is None:
                    continue
                must_conditions.append(
                    FieldCondition(key=str(key), match=MatchValue(value=value))
                )

        query_filter = Filter(must=must_conditions) if must_conditions else None

        if hasattr(self.client, "search"):
            results = self.client.search(
                collection_name=target_collection,
                query_vector=query_vector,
                query_filter=query_filter,
                score_threshold=threshold,
                with_payload=True,
                with_vectors=False,
                limit=top_k,
            )
        else:
            query_response = self.client.query_points(
                collection_name=target_collection,
                query=query_vector,
                query_filter=query_filter,
                score_threshold=threshold,
                with_payload=True,
                with_vectors=False,
                limit=top_k,
            )
            results = getattr(query_response, "points", []) or []

        items: List[Dict[str, Any]] = []
        for hit in results:
            score = float(hit.score)
            if threshold is not None and score < threshold:
                continue
            items.append(
                {
                    "id": str(hit.id),
                    "score": score,
                    "payload": hit.payload or {},
                }
            )
        return items

    def list_collections(self) -> List[str]:
        """返回当前Qdrant中的collection列表。"""
        collection_info = self.client.get_collections()
        return [item.name for item in collection_info.collections]

    def _collection_exists(self, collection_name: str) -> bool:
        try:
            return bool(self.client.collection_exists(collection_name))
        except AttributeError:
            pass
        except Exception:
            return False

        try:
            self.client.get_collection(collection_name=collection_name)
            return True
        except Exception:
            return False

    def _normalize_document(self, knowledge_type: str, raw_doc: Dict[str, Any]) -> Dict[str, Any]:
        kb_type = (knowledge_type or "").strip().lower()
        if not kb_type:
            raise ValueError("knowledge_type不能为空")

        doc = dict(raw_doc or {})
        if kb_type == "sop":
            return self._normalize_sop_document(doc)
        if kb_type == "case":
            return self._normalize_case_document(doc)
        if kb_type in {"security_knowledge", "security"}:
            return self._normalize_security_knowledge_document(doc)
        raise ValueError(f"不支持的knowledge_type: {kb_type}")

    def _normalize_sop_document(self, doc: Dict[str, Any]) -> Dict[str, Any]:
        sop_name = str(doc.get("sop_name", "")).strip()
        if not sop_name:
            raise ValueError("SOP知识文档必须包含sop_name")

        sop_index = str(doc.get("sop_index", "")).strip()
        if not sop_index:
            raise ValueError("SOP知识文档必须包含sop_index")

        workflow_steps = self._as_workflow_steps(doc.get("workflow_steps"))
        if not workflow_steps:
            raise ValueError("SOP知识文档必须包含workflow_steps，且每步包含step_name和step_content")

        return {
            "knowledge_type": "sop",
            "sop_name": sop_name,
            "sop_index": sop_index,
            "workflow_steps": workflow_steps,
            "version": str(doc.get("version", "1.0.0")).strip(),
            "source": str(doc.get("source", "deepsoc_sop_seed")).strip(),
            "updated_at": str(doc.get("updated_at") or self._utc_now_text()),
        }

    def _normalize_case_document(self, doc: Dict[str, Any]) -> Dict[str, Any]:
        case_id = str(doc.get("id", "")).strip() or str(uuid.uuid4())
        alert_type = str(doc.get("alert_type", "")).strip()
        if not alert_type:
            raise ValueError("案例知识文档必须包含 alert_type")

        severity = str(doc.get("severity", "")).strip() or "unknown"
        alert_payload = doc.get("alert_payload", "")
        actions = doc.get("actions", "")
        closed_at = str(doc.get("closed_at", "")).strip()

        return {
            "id": case_id,
            "alert_type": alert_type,
            "severity": severity,
            "alert_payload": alert_payload,
            "actions": actions,
            "closed_at": closed_at,
        }

    def _normalize_security_knowledge_document(self, doc: Dict[str, Any]) -> Dict[str, Any]:
        item_id = str(doc.get("id", "")).strip() or str(uuid.uuid4())
        title = str(doc.get("title", "")).strip()
        content = str(doc.get("content", "")).strip()
        knowledge_type = str(doc.get("knowledge_type", "")).strip().lower()
        valid_types = {"attack_technique", "cve", "term", "tool"}

        if not title:
            raise ValueError("安全知识文档必须包含 title")
        if not content:
            raise ValueError("安全知识文档必须包含 content")
        if knowledge_type not in valid_types:
            raise ValueError(
                f"安全知识文档 knowledge_type 仅支持 {sorted(valid_types)}，收到: {knowledge_type}"
            )

        return {
            "id": item_id,
            "title": title,
            "content": content,
            "knowledge_type": knowledge_type,
        }

    def _build_embedding_text(self, payload: Dict[str, Any]) -> str:
        if "sop_name" in payload and "sop_index" in payload:
            sections = [
                payload.get("knowledge_type", ""),
                payload.get("sop_name", ""),
                payload.get("sop_index", ""),
            ]
            for step in payload.get("workflow_steps", []):
                if not isinstance(step, dict):
                    continue
                sections.append(str(step.get("step_name", "")).strip())
                sections.append(str(step.get("step_content", "")).strip())
            return "\n".join([item for item in sections if item]).strip()

        if "alert_type" in payload and "actions" in payload:
            sections = [
                str(payload.get("alert_type", "")).strip(),
                str(payload.get("severity", "")).strip(),
                self._serialize_embedding_fragment(payload.get("alert_payload")),
                self._serialize_embedding_fragment(payload.get("actions")),
                str(payload.get("closed_at", "")).strip(),
            ]
            return "\n".join([item for item in sections if item]).strip()

        if "title" in payload and "content" in payload and "knowledge_type" in payload:
            sections = [
                str(payload.get("knowledge_type", "")).strip(),
                str(payload.get("title", "")).strip(),
                str(payload.get("content", "")).strip(),
            ]
            return "\n".join([item for item in sections if item]).strip()

        sections = [
            str(payload.get("id", "")).strip(),
            str(payload.get("title", "")).strip(),
            self._serialize_embedding_fragment(payload),
        ]
        return "\n".join([item for item in sections if item]).strip()

    def _build_point_id(self, payload: Dict[str, Any]) -> Any:
        direct_id = str(payload.get("id", "")).strip()
        if direct_id:
            if direct_id.isdigit():
                return int(direct_id)
            try:
                return str(uuid.UUID(direct_id))
            except ValueError:
                # 兼容业务自定义字符串ID：稳定映射到UUID，原始ID保留在payload.id
                return str(uuid.uuid5(uuid.NAMESPACE_URL, f"qdrant-point:{direct_id}"))

        unique_key = (
            f"{payload.get('knowledge_type')}|"
            f"{payload.get('sop_name')}|{payload.get('version')}"
        )
        return str(uuid.uuid5(uuid.NAMESPACE_URL, unique_key))

    @staticmethod
    def _serialize_embedding_fragment(value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            return value.strip()
        if isinstance(value, (dict, list)):
            return json.dumps(value, ensure_ascii=False, sort_keys=True)
        return str(value).strip()

    @staticmethod
    def _build_event_query_text(event_name: str, event_message: str) -> str:
        parts = [str(event_name or "").strip(), str(event_message or "").strip()]
        query_text = "\n".join([p for p in parts if p]).strip()
        return query_text or "empty_event"

    def _embed_text(self, text: str) -> List[float]:
        content = (text or "").strip()
        if not content:
            content = "empty"

        if self.embedding_provider == "openai":
            return self._embed_with_openai(content)
        return self._embed_with_hash(content)

    def _embed_with_openai(self, text: str) -> List[float]:
        if not self.embedding_api_key:
            raise ValueError("EMBEDDING_API_KEY未配置，无法使用openai embedding provider")

        if self._embedding_client is None:
            self._embedding_client = OpenAI(
                api_key=self.embedding_api_key,
                base_url=self.embedding_base_url,
            )

        response = self._embedding_client.embeddings.create(
            model=self.embedding_model,
            input=text,
        )
        embedding = list(response.data[0].embedding)
        if len(embedding) != self.vector_size:
            raise ValueError(
                f"embedding维度与KB_VECTOR_SIZE不一致: got={len(embedding)} expected={self.vector_size}"
            )
        return embedding

    def _embed_with_hash(self, text: str) -> List[float]:
        # 轻量离线embedding：用于无额外模型时快速落地，可后续切换openai provider
        vector = [0.0] * self.vector_size
        words = re.findall(r"[a-z0-9_]+|[\u4e00-\u9fff]", text.lower())
        if not words:
            words = [text.lower().strip() or "empty"]

        for token in words:
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            bucket = int.from_bytes(digest[:4], byteorder="big") % self.vector_size
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            weight = 1.0 + (digest[5] / 255.0)
            vector[bucket] += sign * weight

        norm = math.sqrt(sum(value * value for value in vector))
        if norm <= 1e-12:
            return vector
        return [value / norm for value in vector]

    @staticmethod
    def _as_workflow_steps(value: Any) -> List[Dict[str, Any]]:
        if not isinstance(value, list):
            return []
        steps = []
        for item in value:
            if not isinstance(item, dict):
                continue
            step_name = str(item.get("step_name", "")).strip()
            step_content = str(item.get("step_content", "")).strip()
            if step_name and step_content:
                steps.append({"step_name": step_name, "step_content": step_content})
        return steps

    @staticmethod
    def _utc_now_text() -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
