"""统一日志核心（合并历史 logger_config.py 与 logging.py，唯一出口）。

特性保留：
- AsyncLogger：异步队列（SimpleQueue，无锁）写盘，不阻塞业务线程；
- 历史日志按天/小时切片并自动 gzip 滚动压缩（约省 10x 空间）；
- 控制台彩色 + 文件 JSON（经 pythonjsonlogger，orjson 加速）双写；
- 全局统一工厂 get_logger(name)：幂等初始化，杜绝各自配置。

用法：
    from app.core.logger import get_logger
    logger = get_logger("app.tasks")
"""
from __future__ import annotations

import atexit
import logging
import os
import shutil
import sys
from pathlib import Path
from queue import SimpleQueue

try:
    import gzip
except ImportError:  # pragma: no cover - 标准库，仅防极端裁剪环境
    gzip = None  # type: ignore[assignment]

from logging.handlers import QueueHandler, QueueListener, TimedRotatingFileHandler
from typing import Optional

from pythonjsonlogger import jsonlogger

try:
    import orjson

    HAS_ORJSON = True
except ImportError:
    HAS_ORJSON = False


# -------------------------- 项目路径 & 日志目录 --------------------------
BASE_DIR = Path(__file__).resolve().parent.parent.parent
LOG_DIR = BASE_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)


# -------------------------- 历史日志 gzip 压缩 --------------------------
def _gzip_namer(default_name: str) -> str:
    """rotation 时把目标文件名追加 .gz"""
    return default_name + ".gz"


def _gzip_rotator(source: str, dest: str) -> None:
    """rotation 时把 source 内容压缩到 dest（dest 已含 .gz 后缀）。"""
    try:
        if gzip is not None:
            with open(source, "rb") as f_in, gzip.open(dest, "wb", compresslevel=6) as f_out:
                shutil.copyfileobj(f_in, f_out)
            os.remove(source)
            return
    except Exception:
        pass
    # 压缩失败时回退到普通 rename，避免日志写入直接挂掉
    try:
        os.replace(source, dest.removesuffix(".gz"))
    except Exception:
        pass


# -------------------------- 颜色配置 --------------------------
LOG_COLORS = {
    logging.DEBUG: "\033[36m",
    logging.INFO: "\033[32m",
    logging.WARNING: "\033[33m",
    logging.ERROR: "\033[31m",
    logging.CRITICAL: "\033[35m",
}
RESET_COLOR = "\033[0m"


class ColoredFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        original_levelname = record.levelname
        color = LOG_COLORS.get(record.levelno, RESET_COLOR)
        record.levelname = f"{color}{record.levelname}{RESET_COLOR}"
        formatted_msg = super().format(record)
        record.levelname = original_levelname
        return formatted_msg


class FastJsonFormatter(jsonlogger.JsonFormatter):
    """极速 JSON 序列化器（优先 orjson）。"""

    def jsonify(self, log_record, **kwargs):  # noqa: ANN001
        if HAS_ORJSON:
            return orjson.dumps(log_record).decode("utf-8")
        return super().jsonify(log_record, **kwargs)


class AsyncLogger:
    """异步队列日志器：控制台彩色 + 文件（可选 JSON）+ gzip 滚动，全局单队列。"""

    def __init__(
        self,
        name: str = "app",
        level: int = logging.INFO,
        log_dir: str = "",
        file_prefix: str = "app",
        console_log: bool = True,
        json_file_log: bool = False,
        rotate_when: str = "D",  # 'D' 按天切、'H' 按小时切、'midnight' 本地 0 点切
        rotate_interval: int = 1,
        backup_count: int = 180,  # 默认保留 180 天
        gzip_compress: bool = True,
        log_format: str = "%(asctime)s - %(levelname)s - %(filename)s:%(lineno)d - %(message)s",
        date_format: str = "%Y-%m-%d %H:%M:%S",
        custom_file_formatter: Optional[logging.Formatter] = None,
    ) -> None:
        self.logger = logging.getLogger(name)

        # 单例保护，防止同一 logger 重复绑定 Handler 导致重复输出
        if getattr(self.logger, "_is_async_setup", False):
            return

        self.logger.setLevel(level)
        self.logger.propagate = False
        if not log_dir:
            log_dir = str(LOG_DIR / "app")
        os.makedirs(log_dir, exist_ok=True)

        self.log_queue = SimpleQueue()
        self.handlers: list[logging.Handler] = []

        # 1. 控制台（彩色）
        if console_log:
            console_handler = logging.StreamHandler(sys.stdout)
            console_handler.setFormatter(ColoredFormatter(fmt=log_format, datefmt=date_format))
            self.handlers.append(console_handler)

        # 2. 文件（按 rotate_when 切片，gzip 压缩历史）
        file_path = os.path.join(log_dir, f"{file_prefix}.log")
        file_handler = TimedRotatingFileHandler(
            file_path,
            when=rotate_when,
            interval=rotate_interval,
            backupCount=backup_count,
            encoding="utf-8",
            utc=False,
        )
        if gzip_compress:
            file_handler.namer = _gzip_namer
            file_handler.rotator = _gzip_rotator
        if custom_file_formatter:
            file_handler.setFormatter(custom_file_formatter)
        elif json_file_log:
            json_formatter = FastJsonFormatter(
                log_format,
                rename_fields={"levelname": "level", "asctime": "timestamp"},
                timestamp=True,
            )
            file_handler.setFormatter(json_formatter)
        else:
            file_handler.setFormatter(logging.Formatter(fmt=log_format, datefmt=date_format))
        self.handlers.append(file_handler)

        # 3. 启动监听线程（所有 handler 都经队列，性能 & 线程安全）
        self.queue_listener = QueueListener(self.log_queue, *self.handlers, respect_handler_level=True)
        self.queue_listener.start()

        # 4. 队列生产者挂到 logger 与 root（root 让所有命名空间命中同一队列）
        queue_handler = QueueHandler(self.log_queue)
        self.logger.addHandler(queue_handler)
        root_logger = logging.getLogger()
        if root_logger.level == logging.WARNING:
            root_logger.setLevel(level)
        if not any(isinstance(h, QueueHandler) and h.queue is self.log_queue for h in root_logger.handlers):
            root_logger.addHandler(QueueHandler(self.log_queue))

        self.logger._is_async_setup = True  # type: ignore[attr-defined]
        atexit.register(self.shutdown)

    def get_logger(self) -> logging.Logger:
        return self.logger

    def shutdown(self) -> None:
        """优雅关闭，幂等，确保缓冲日志刷盘。"""
        if hasattr(self, "queue_listener") and self.queue_listener:
            try:
                self.queue_listener.stop()
            except AttributeError:
                pass
            finally:
                self.queue_listener = None


# -------------------------- 全局单例 & 统一工厂 --------------------------
_app_logger: AsyncLogger | None = None


def setup_logging(
    name: str = "app",
    level: int = logging.INFO,
    log_dir: str = "",
    file_prefix: str = "app",
    console_log: bool = True,
    json_file_log: bool = False,
    rotate_when: str = "D",
    rotate_interval: int = 1,
    backup_count: int = 180,
    gzip_compress: bool = True,
    log_format: str = "%(asctime)s - %(levelname)s - %(filename)s:%(lineno)d - %(message)s",
    date_format: str = "%Y-%m-%d %H:%M:%S",
    custom_file_formatter: Optional[logging.Formatter] = None,
) -> logging.Logger:
    """显式初始化全局异步日志（幂等；应用启动时调用一次）。"""
    global _app_logger
    _app_logger = AsyncLogger(
        name=name,
        level=level,
        log_dir=log_dir,
        file_prefix=file_prefix,
        console_log=console_log,
        json_file_log=json_file_log,
        rotate_when=rotate_when,
        rotate_interval=rotate_interval,
        backup_count=backup_count,
        gzip_compress=gzip_compress,
        log_format=log_format,
        date_format=date_format,
        custom_file_formatter=custom_file_formatter,
    )
    return _app_logger.get_logger()


def _ensure_init() -> None:
    global _app_logger
    if _app_logger is None:
        _app_logger = AsyncLogger(name="app", log_dir="", file_prefix="app")


def get_logger(name: str | None = "app") -> logging.Logger:
    """全局统一 logger 工厂：首次调用自动初始化，业务模块只依赖本函数。"""
    _ensure_init()
    if not name:
        return _app_logger.get_logger()  # type: ignore[union-attr]
    return logging.getLogger(name)


# 兼容历史命名（原 logger_config / logging 的入口，避免遗留调用手忙脚乱）
get_module_logger = get_logger  # noqa: F811


def setup_app_logging(level: int | str = logging.INFO) -> logging.Logger:
    """应用启动入口：异步队列 + JSON 文件(gzip) + 控制台彩色；返回根 logger。

    level 支持 int 或 "INFO" 字符串，便于从配置直接接入。
    """
    from app.core.config import get_settings

    if isinstance(level, str):
        settings = get_settings()
        level = getattr(logging, getattr(settings, "log_level", "INFO").upper(), logging.INFO)
    return setup_logging(name="app", level=level, log_dir="", file_prefix="app", json_file_log=False)