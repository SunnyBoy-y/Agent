import sys
import logging
from src.config import Config

def setup_logger(name: str = "agent_backend"):
    logger = logging.getLogger(name)
    logger.setLevel(Config.LOG_LEVEL)

    # 避免重复添加 handler
    if not logger.handlers:
        # 控制台 Handler
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(Config.LOG_LEVEL)
        
        # 格式化器
        formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s'
        )
        console_handler.setFormatter(formatter)
        
        logger.addHandler(console_handler)
        
    return logger

logger = setup_logger()
