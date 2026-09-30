import base64
from pathlib import Path
from typing import Dict, List, Optional

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star
from astrbot.api.web import error_response, json_response, request


PLUGIN_NAME = "astrbot_plugin_image_tag_kb"


class ImageTagKBPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig = None):
        super().__init__(context)
        # 显式接收并保存配置对象
        self.config = config if config is not None else {}

        self.tag_to_images: Dict[str, List[str]] = {}
        self.image_dir: Optional[Path] = None

        # 提前加载配置，避免 Web API 在 initialize 之前访问到 None
        self._load_config()

        # 注册管理面板调用的 Web API
        context.register_web_api(
            f"/{PLUGIN_NAME}/upload", self.handle_upload, ["POST"], "上传图片"
        )
        context.register_web_api(
            f"/{PLUGIN_NAME}/list", self.handle_list, ["GET"], "列出图片"
        )
        context.register_web_api(
            f"/{PLUGIN_NAME}/tags", self.handle_tags, ["GET"], "列出标签"
        )
        context.register_web_api(
            f"/{PLUGIN_NAME}/delete", self.handle_delete, ["POST"], "删除图片"
        )
        context.register_web_api(
            f"/{PLUGIN_NAME}/rebuild", self.handle_rebuild, ["POST"], "重建标签"
        )
        logger.info(f"[{PLUGIN_NAME}] Web API 已注册")

    # ==================== 生命周期 ====================

    async def initialize(self):
        self._scan_images()
        logger.info(
            f"[{PLUGIN_NAME}] 已就绪 | 图片目录: {self.image_dir} | 标签数: {len(self.tag_to_images)}"
        )

    def _load_config(self):
        default_dir = str(Path(__file__).parent / "data" / "images")
        image_dir_str = self.config.get("image_dir", default_dir)
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
            self._add_tag(stem, str(img_path))
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

    @filter.command("查看标签")
    async def cmd_list_tags(self, event: AstrMessageEvent):
        if not self.tag_to_images:
            yield event.plain_result("当前没有任何标签，请先上传图片。")
            return
        lines = [
            f"{tag}（{len(self.tag_to_images[tag])} 张）"
            for tag in sorted(self.tag_to_images.keys())
        ]
        max_show = 80
        msg = f"当前标签列表（共 {len(lines)} 个）：\n" + "\n".join(lines[:max_show])
        if len(lines) > max_show:
            msg += f"\n…… 仅显示前 {max_show} 个"
        yield event.plain_result(msg)

    @filter.command("查看图片")
    async def cmd_list_images(self, event: AstrMessageEvent, tag: str = ""):
        if not tag:
            yield event.plain_result("用法：/查看图片 标签名")
            return
        images = self._find_images(tag)
        if not images:
            yield event.plain_result(f"没有找到与「{tag}」匹配的图片。")
            return
        lines = [Path(p).name for p in images]
        msg = f"「{tag}」匹配到 {len(lines)} 张图片：\n" + "\n".join(lines)
        yield event.plain_result(msg)

    @filter.command("图片统计")
    async def cmd_stats(self, event: AstrMessageEvent):
        total_tags = len(self.tag_to_images)
        total_assoc = sum(len(v) for v in self.tag_to_images.values())
        unique_images = set()
        for paths in self.tag_to_images.values():
            unique_images.update(paths)
        kb_name = self.config.get("kb_name", "")
        msg = (
            f"📊 图片标签知识库统计\n"
            f"图片目录：{self.image_dir}\n"
            f"标签总数：{total_tags}\n"
            f"图片文件数：{len(unique_images)}\n"
            f"标签-图片关联数：{total_assoc}\n"
            f"知识库：{kb_name or '（未配置）'}"
        )
        yield event.plain_result(msg)

    # ==================== Web API ====================

    async def handle_upload(self):
        """接收管理面板上传的图片。"""
        try:
            if not self.image_dir:
                return error_response("插件未初始化")

            # AstrBot 4.27.5：使用 request.files() 获取上传文件字典
            files = await request.files()
            uploaded = files.get("file")
            if not uploaded:
                return error_response("未收到文件")

            filename = uploaded.filename
            safe_name = Path(filename).name
            exts = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp"}
            if Path(safe_name).suffix.lower() not in exts:
                return error_response("不支持的文件类型")

            target = self.image_dir / safe_name
            content = await uploaded.read()
            with open(target, "wb") as f:
                f.write(content)

            self._scan_images()
            logger.info(f"[{PLUGIN_NAME}] 上传成功: {safe_name}")
            return json_response({"saved": safe_name})
        except Exception as e:
            logger.error(f"[{PLUGIN_NAME}] handle_upload 失败: {e}")
            return error_response(f"上传失败: {e}")

    async def handle_list(self):
        """返回图片列表，包含文件名和 Base64 缩略图。"""
        try:
            if not self.image_dir or not self.image_dir.exists():
                return json_response({"files": []})

            files = sorted(
                p.name for p in self.image_dir.iterdir() if p.is_file()
            )
            result = []
            for name in files:
                file_path = self.image_dir / name
                try:
                    raw = file_path.read_bytes()
                    if len(raw) > 2 * 1024 * 1024:
                        thumb = None
                    else:
                        b64 = base64.b64encode(raw).decode("utf-8")
                        ext = file_path.suffix.lower()
                        mime = {
                            ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                            ".png": "image/png", ".gif": "image/gif",
                            ".bmp": "image/bmp", ".webp": "image/webp",
                        }.get(ext, "image/jpeg")
                        thumb = f"data:{mime};base64,{b64}"
                    result.append({"name": name, "thumb": thumb})
                except Exception as e:
                    logger.error(f"读取图片失败 {name}: {e}")
                    result.append({"name": name, "thumb": None})

            return json_response({"files": result})
        except Exception as e:
            logger.error(f"[{PLUGIN_NAME}] handle_list 失败: {e}")
            return error_response(f"列出图片失败: {e}")

    async def handle_tags(self):
        """返回当前所有标签及其对应图片数量。"""
        try:
            tags = [
                {"tag": tag, "count": len(paths)}
                for tag, paths in self.tag_to_images.items()
            ]
            tags.sort(key=lambda x: x["tag"])
            return json_response({"tags": tags})
        except Exception as e:
            logger.error(f"[{PLUGIN_NAME}] handle_tags 失败: {e}")
            return error_response(f"列出标签失败: {e}")

    async def handle_delete(self):
        try:
            if not self.image_dir:
                return error_response("插件未初始化")

            payload = await request.json(default={})
            filename = payload.get("filename", "")
            safe_name = Path(filename).name
            target = self.image_dir / safe_name
            if not target.exists():
                return error_response("文件不存在")
            target.unlink()
            self._scan_images()
            logger.info(f"[{PLUGIN_NAME}] 已删除: {safe_name}")
            return json_response({"deleted": safe_name})
        except Exception as e:
            logger.error(f"[{PLUGIN_NAME}] handle_delete 失败: {e}")
            return error_response(f"删除失败: {e}")

    async def handle_rebuild(self):
        try:
            self._scan_images()
            return json_response({"tag_count": len(self.tag_to_images)})
        except Exception as e:
            logger.error(f"[{PLUGIN_NAME}] handle_rebuild 失败: {e}")
            return error_response(f"重建失败: {e}")