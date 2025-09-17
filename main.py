import asyncio
import base64
import secrets
from io import BytesIO
from multiprocessing import Process, Queue
from typing import Any

import aiohttp
from aiohttp import ClientTimeout
from PIL import Image as ImageP

import astrbot.core.message.components as Comp
from astrbot.api import logger
from astrbot.api.star import Context, Star, register
from astrbot.core.config.astrbot_config import AstrBotConfig
from astrbot.core.message.message_event_result import MessageChain

from .api import run_server


@register(
    "astrbot_plugin_push_lite",
    "Next-Page-Vi/Raven95676",
    "Astrbot轻量级推送插件(增强版)",
    "0.1.0",
)
class PushLite(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.in_queue: Queue | None = None
        self.process: Process | None = None
        self._running = False

    async def initialize(self):
        """初始化插件"""
        if not self.config["api"].get("token"):
            self.config["api"]["token"] = secrets.token_urlsafe(32)
            self.config.save_config()

        self.in_queue = Queue()
        self.process = Process(
            target=run_server,
            args=(
                self.config["api"]["token"],
                self.config["api"].get("host", "0.0.0.0"),
                self.config["api"].get("port", 9966),
                self.in_queue,
            ),
            daemon=True,
        )
        self.notification_umo_list = self.config.get("notification_umo_list", [])
        self.process.start()
        self._running = True
        asyncio.create_task(self._process_messages())

    async def _send_message(self, message_chain: MessageChain):
        """发送消息到指定umo"""
        if not self.notification_umo_list:
            logger.warning("未配置通知umo，跳过通知")
            return
        for umo in self.notification_umo_list:
            logger.info(f"{message_chain} to {umo}")
            await self.context.send_message(umo, message_chain)

    async def _process_messages(self) -> None:
        """处理来自子进程的消息"""
        if self.in_queue is None:
            return
        while self._running:
            message = await asyncio.get_event_loop().run_in_executor(
                None, self.in_queue.get
            )
            logger.info(f"正在处理消息: {message['message_id']}")
            result = {"message_id": message["message_id"], "success": True}
            message_chain: MessageChain = MessageChain(chain=[])
            try:
                if message.get("content"):
                    message_chain.chain.append(Comp.Plain(message["content"]))
                if message.get("image"):
                    logger.debug("处理图片消息")
                    try:
                        if message["image"].startswith(("http://", "https://")):
                            message_chain.chain.append(
                                Comp.Image.fromURL(message["image"])
                            )
                        else:
                            image = base64.b64decode(message["image"])
                            ImageP.open(BytesIO(image)).verify()
                            message_chain.chain.append(Comp.Image.fromBytes(image))
                    except Exception:
                        raise Exception("不支持的图片格式")

                await self._send_message(message_chain)
                logger.info(f"消息处理完成: {message['message_id']}")
            except Exception as e:
                logger.error(f"消息发送失败: {str(e)}")
                result.update({"success": False, "error": str(e)})
            finally:
                if callback_url := message.get("callback_url"):
                    await self._send_callback(callback_url, result)

    async def _send_callback(self, url: str, data: dict[str, Any]):
        """发送回调通知"""
        try:
            timeout = ClientTimeout(total=5)
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=data, timeout=timeout) as resp:
                    if resp.status >= 400:
                        logger.warning(f"回调失败: 状态码 {resp.status}")
        except Exception as e:
            logger.error(f"回调错误: {str(e)}")

    async def terminate(self):
        """停止插件"""
        self._running = False
        if self.process:
            self.process.terminate()
            self.process.join(5)
        if self.in_queue:
            while not self.in_queue.empty():
                self.in_queue.get()
