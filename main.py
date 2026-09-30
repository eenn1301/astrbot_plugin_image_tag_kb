import json
from pathlib import Path
from typing import Dict, List, Optional

from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star
from astrbot.api.web import error_response, json_response, request
from astrbot.api import logger
from astrbot.api import AstrBotConfig

PLUGIN_NAME = "astrbot_plugin_image_tag_kb"


class ImageTagKBPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig = None):
        super().__init__(context)
        # 显式接收并保存配置对象
        self.config = config if config is not None else {}

        self.tag_to_images: Dict[str, List[str]] = {}
        self.image_dir: Optional[Path] = None

        # 注册管理面板调用的 Web API
        context.register_web_api(
            f"/{PLUGIN_NAME}/upload", self.handle_upload, ["POST"], "上传图片"
        )
        context.register_web_api(
            f"/{PLUGIN_NAME}/list", self.handle_list, ["GET"], "列出图片"
        )
        context.register_web_api(
            f"/{PLUGIN_NAME}/delete", self.handle_delete, ["POST"], "删除图片"
        )
        context.register_web_api(
            f"/{PLUGIN_NAME}/rebuild", self.handle_rebuild, ["POST"], "重建标签"
        )

    # ==================== 生命周期 ====================

    async def initialize(self):
        self._load_config()
        self._scan_images()
        logger.info(
            f"[{PLUGIN_NAME}] 已就绪 | 标签数: {len(self.tag_to_images)}"
        )

    def _load_config(self):
        image_dir_str = self.config.get(
            "image_dir", str(Path(__file__).parent / "data" / "images")
        )
        self.image_dir = Path(image_dir_str)
        if not self.image_dir.is_absolute():
            self.image_dir = Path(__file__).parent / image_dir_str
        self.image_dir.mkdir(parents=True, exist_ok=True)

    # ==================== 图片标签 ====================

    def _scan_images(self):
        """扫描图片目录，建立 标签 -> 图片路径 的映射。"""
        self.tag_to_images.clear()
        if not self.image_dir or not self.image_dir.exists():
            return
        exts = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp"}
        separators = self.config.get("tag_separators", ["_", "-", " "])

        for img_path in self.image_dir.rglob("*"):
            if img_path.suffix.lower() not in exts:
                continue
            stem = img_path.stem
            # 完整文件名作为一个标签
            self._add_tag(stem, str(img_path))
            # 按分隔符拆分出子标签
            for sep in separators:
                if sep in stem:
                    parts = [p.strip() for p in stem.split(sep) if p.strip()]
                    for part in parts:
                        self._add_tag(part, str(img_path))
                    break

    def _add_tag(self, tag: str, image_path: str):
        tag = tag.lower()
        self.tag_to_images.setdefault(tag, [])
        if image_path not in self.tag_to_images[tag]:
            self.tag_to_images[tag].append(image_path)

    def _find_images(self, query: str) -> List[str]:
        q = query.lower().strip()
        if not q:
            return []
        results = []
        if q in self.tag_to_images:
            results.extend(self.tag_to_images[q])
        else:
            for tag, paths in self.tag_to_images.items():
                if tag in q or q in tag:
                    results.extend(paths)
        seen, unique = set(), []
        for p in results:
            if p not in seen:
                seen.add(p)
                unique.append(p)
        return unique

    # ==================== 知识库检索 ====================

    async def _find_knowledge(self, query: str) -> Optional[str]:
        """从 AstrBot 内置知识库检索答案。"""
        q = query.strip()
        if not q:
            return None

        kb_name = self.config.get("kb_name", "")
        if not kb_name:
            logger.warning("未配置 kb_name，跳过知识库检索")
            return None

        try:
            kb_helper = await self.context.kb_manager.get_kb_by_name(kb_name)
            if not kb_helper:
                logger.warning(f"未找到知识库: {kb_name}")
                return None

            results = await kb_helper.retrieve(
                query=q,
                top_k=self.config.get("kb_top_k", 3),
            )
            if not results:
                return None

            return "\n\n".join(r.text for r in results)
        except Exception as e:
            logger.error(f"知识库检索失败: {e}")
            return None

    # ==================== 消息处理 ====================

    @filter.event_message_type(filter.EventMessageType.ALL, priority=100)
    async def on_message(self, event: AstrMessageEvent):
        text = event.message_str.strip()
        if not text:
            return

        trigger_mode = self.config.get("trigger_mode", "all")
        if trigger_mode == "command":
            prefix = self.config.get("command_prefix", "/查")
            if not text.startswith(prefix):
                return
            text = text[len(prefix):].strip()

        explanation = await self._find_knowledge(text)
        images = self._find_images(text)

        if not explanation and not images:
            return

        if explanation and self.config.get("enable_kb", True):
            yield event.plain_result(explanation)

        if images and self.config.get("enable_image", True):
            max_images = self.config.get("max_images", 1)
            for img_path in images[:max_images]:
                yield event.image_result(img_path)

    # ==================== 聊天指令 ====================

    @filter.command("重建标签")
    async def cmd_rebuild(self, event: AstrMessageEvent):
        self._scan_images()
        yield event.plain_result(
            f"标签索引已重建，共 {len(self.tag_to_images)} 个标签。"
        )

    # ==================== Web API ====================

    async def handle_upload(self):
        """接收管理面板上传的图片。"""
        uploaded = await request.file("file")
        if not uploaded:
            return error_response("未收到文件")

        filename = uploaded.filename
        safe_name = Path(filename).name  # 防止目录穿越
        exts = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp"}
        if Path(safe_name).suffix.lower() not in exts:
            return error_response("不支持的文件类型")

        target = self.image_dir / safe_name
        content = await uploaded.read()
        with open(target, "wb") as f:
            f.write(content)

        self._scan_images()
        return json_response({"saved": safe_name})

    async def handle_list(self):
        if not self.image_dir.exists():
            return json_response({"files": []})
        files = sorted(p.name for p in self.image_dir.iterdir() if p.is_file())
        return json_response({"files": files})

    async def handle_delete(self):
        payload = await request.json(default={})
        filename = payload.get("filename", "")
        safe_name = Path(filename).name
        target = self.image_dir / safe_name
        if not target.exists():
            return error_response("文件不存在")
        target.unlink()
        self._scan_images()
        return json_response({"deleted": safe_name})

    async def handle_rebuild(self):
        self._scan_images()
        return json_response({"tag_count": len(self.tag_to_images)})