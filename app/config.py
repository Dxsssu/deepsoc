import os
from dotenv import load_dotenv
from types import SimpleNamespace

# 加载环境变量
load_dotenv()

# 创建配置对象
config = SimpleNamespace()

# 事件处理配置
config.EVENT_MAX_ROUND = int(os.getenv('EVENT_MAX_ROUND', 3))

# Expert Service Worker Intervals (seconds)
config.EXPERT_EXECUTION_SUMMARY_INTERVAL = int(os.getenv('EXPERT_EXECUTION_SUMMARY_INTERVAL', 10))
config.EXPERT_LIFECYCLE_INTERVAL  = int(os.getenv('EXPERT_LIFECYCLE_INTERVAL', 10)) 

# Knowledge Base / Qdrant 配置
config.QDRANT_URL = os.getenv('QDRANT_URL', 'http://127.0.0.1:6333')
config.QDRANT_API_KEY = os.getenv('QDRANT_API_KEY', '')
config.QDRANT_TIMEOUT = int(os.getenv('QDRANT_TIMEOUT', 30))

config.KB_COLLECTION = os.getenv('KB_COLLECTION', 'traceback_knowledge')
config.SOP_KB_COLLECTION = os.getenv('SOP_KB_COLLECTION', 'sop_knowledge_base')
config.KB_CASES_COLLECTION = os.getenv('KB_CASES_COLLECTION', 'kb_cases')
config.KB_SECURITY_KNOWLEDGE_COLLECTION = os.getenv('KB_SECURITY_KNOWLEDGE_COLLECTION', 'kb_security_knowledge')
config.KB_VECTOR_SIZE = int(os.getenv('KB_VECTOR_SIZE', 384))
config.KB_DEFAULT_TOP_K = int(os.getenv('KB_DEFAULT_TOP_K', 3))
config.KB_EMBEDDING_PROVIDER = os.getenv('KB_EMBEDDING_PROVIDER', 'hash')
config.EMBEDDING_MODEL = os.getenv('EMBEDDING_MODEL', 'text-embedding-3-small')
config.EMBEDDING_API_KEY = os.getenv('EMBEDDING_API_KEY', '')
config.EMBEDDING_BASE_URL = os.getenv('EMBEDDING_BASE_URL', '')
