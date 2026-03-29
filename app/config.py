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
