import base64
import json
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
        self.config = config if config is not None else {}

        # 主要映射：文件名 -> 标签
        self.image_tags: Dict[str, str] = {}
        # 反查索引：标签 -> 图片完整路径
        self.tag_to_image: Dict[str, str] = {}

        self.image_dir: Optional[Path] = None
        self.mapping_file: Optional[Path] = None

        self._load_config()

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
            f"/{PLUGIN_NAME}/update_tag", self.handle_update_tag, ["POST"], "修改标签"
        )
        context.register_web_api(
            f"/{PLUGIN_NAME}/rebuild", self.handle_rebuild, ["POST"], "重建标签"
        )
        logger.info(f"[{PLUGIN_NAME}] Web API 已注册")

    # ==================== 生命周期 ====================

    async def initialize(self):
        self._scan_images()
        logger.info(
            f"[{PLUGIN_NAME}] 已就绪 | 图片目录: {self.image_dir} | 图片数: {len(self.image_tags)}"
        )

    def _load_config(self):
        default_dir = str(Path(__file__).parent / "data" / "images")
        image_dir_str = self.config.get("image_dir", default_dir)
        self.image_dir = Path(image_dir_str)
        if not self.image_dir.is_absolute():
            self.image_dir = Path(__file__).parent / image_dir_str
        self.image_dir.mkdir(parents=True, exist_ok=True)

        self.mapping_file = Path(__file__).parent / "data" / "mapping.json"
        self.mapping_file.parent.mkdir(parents=True, exist_ok=True)

    # ==================== 映射持久化 ====================

    def _load_mapping(self) -> Dict[str, str]:
        if not self.mapping_file or not self.mapping_file.exists():
            return {}
        try:
            with open(self.mapping_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return {str(k): str(v) for k, v in data.items()}
        except Exception as e:
            logger.error(f"读取 mapping.json 失败: {e}")
        return {}

    def _save_mapping(self):
        if not self.mapping_file:
            return
        try:
            with open(self.mapping_file, "w", encoding="utf-8") as f:
                json.dump(self.image_tags, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"保存 mapping.json 失败: {e}")

    # ==================== 扫描与索引 ====================

    def _scan_images(self):
        """扫描图片目录，读取/合并 mapping.json，重建标签索引。"""
        if not self.image_dir or not self.image_dir.exists():
            self.image_tags = {}
            self.tag_to_image = {}
            return

        exts = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp"}
        stored = self._load_mapping()

        current_files = {
            p.name
            for p in self.image_dir.iterdir()
            if p.is_file() and p.suffix.lower() in exts
        }

        new_mapping: Dict[str, str] = {
            name: tag for name, tag in stored.items() if name in current_files
        }

        for name in current_files:
            if name not in new_mapping:
                new_mapping[name] = Path(name).stem.lower()

        self.image_tags = new_mapping
        self._rebuild_tag_index()
        self._save_mapping()

    def _rebuild_tag_index(self):
        """根据 image_tags 重建 tag -> image_path 反查索引。"""
        self.tag_to_image = {}
        for filename, tag in self.image_tags.items():
            path = self.image_dir / filename
            self.tag_to_image[tag] = str(path)

    def _find_images(self, query: str) -> List[str]:
        q = query.lower().strip()
        if not q:
            return []
        if q in self.tag_to_image:
            return [self.tag_to_image[q]]
        results = []
        for tag, path in self.tag_to_image.items():
            if tag in q or q in tag:
                results.append(path)
        return results

    # ==================== 知识库检索 ====================

    async def _find_knowledge(self, query: str) -> Optional[str]:
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
            f"标签索引已重建，共 {len(self.tag_to_image)} 个标签。"
        )

    @filter.command("查看标签")
    async def cmd_list_tags(self, event: AstrMessageEvent):
        if not self.tag_to_image:
            yield event.plain_result("当前没有任何标签，请先上传图片。")
            return
        lines = sorted(self.tag_to_image.keys())
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
        total_tags = len(self.tag_to_image)
        unique_images = len(self.image_tags)
        kb_name = self.config.get("kb_name", "")
        msg = (
            f"📊 图片标签知识库统计\n"
            f"图片目录：{self.image_dir}\n"
            f"标签总数：{total_tags}\n"
            f"图片文件数：{unique_images}\n"
            f"知识库：{kb_name or '（未配置）'}"
        )
        yield event.plain_result(msg)

    # ==================== Web API ====================

    async def handle_upload(self):
        """接收管理面板上传的图片。"""
        try:
            if not self.image_dir:
                return error_response("插件未初始化")

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
        """返回图片列表，包含文件名、标签和 Base64 缩略图。"""
        try:
            if not self.image_dir or not self.image_dir.exists():
                return json_response({"files": []})

            files = sorted(
                p.name for p in self.image_dir.iterdir() if p.is_file()
            )
            result = []
            for name in files:
                file_path = self.image_dir / name
                tag = self.image_tags.get(name, "")
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
                    result.append({"name": name, "thumb": thumb, "tag": tag})
                except Exception as e:
                    logger.error(f"读取图片失败 {name}: {e}")
                    result.append({"name": name, "thumb": None, "tag": tag})

            return json_response({"files": result})
        except Exception as e:
            logger.error(f"[{PLUGIN_NAME}] handle_list 失败: {e}")
            return error_response(f"列出图片失败: {e}")

    async def handle_tags(self):
        """返回当前所有标签，附带对应图片文件名。"""
        try:
            tags = [
                {"tag": tag, "filename": Path(path).name}
                for tag, path in self.tag_to_image.items()
            ]
            tags.sort(key=lambda x: x["tag"])
            return json_response({"tags": tags})
        except Exception as e:
            logger.error(f"[{PLUGIN_NAME}] handle_tags 失败: {e}")
            return error_response(f"列出标签失败: {e}")

    async def handle_update_tag(self):
        """修改某个图片的标签。"""
        try:
            # 使用 request.json() 解析请求体（AstrBot 官方推荐用法）
            payload = await request.json(default={})
            if not isinstance(payload, dict):
                payload = {}

            filename = str(payload.get("filename", "")).strip()
            new_tag = str(payload.get("tag", "")).strip().lower()

            if not filename:
                return error_response("缺少文件名参数")
            if not new_tag:
                return error_response("标签不能为空")

            safe_name = Path(filename).name
            if safe_name not in self.image_tags:
                return error_response("图片不存在")

            # 检查标签是否被其他图片占用
            for other_file, other_tag in self.image_tags.items():
                if other_file != safe_name and other_tag == new_tag:
                    return error_response(
                        f"标签「{new_tag}」已被 {other_file} 使用"
                    )

            self.image_tags[safe_name] = new_tag
            self._rebuild_tag_index()
            self._save_mapping()
            logger.info(f"[{PLUGIN_NAME}] 标签更新: {safe_name} -> {new_tag}")
            return json_response({"filename": safe_name, "tag": new_tag})
        except Exception as e:
            logger.error(f"[{PLUGIN_NAME}] handle_update_tag 失败: {e}")
            return error_response(f"修改标签失败: {e}")

    async def handle_delete(self):
        """删除指定图片。"""
        try:
            if not self.image_dir:
                return error_response("插件未初始化")

            # 使用 request.json() 解析请求体（AstrBot 官方推荐用法）
            payload = await request.json(default={})
            if not isinstance(payload, dict):
                payload = {}

            filename = payload.get("filename", "")
            if not filename:
                return error_response("缺少文件名参数")

            safe_name = Path(filename).name
            target = self.image_dir / safe_name
            if not target.exists():
                return error_response("文件不存在")

            target.unlink()
            self.image_tags.pop(safe_name, None)
            self._rebuild_tag_index()
            self._save_mapping()
            logger.info(f"[{PLUGIN_NAME}] 已删除: {safe_name}")
            return json_response({"deleted": safe_name})
        except Exception as e:
            logger.error(f"[{PLUGIN_NAME}] handle_delete 失败: {e}")
            return error_response(f"删除失败: {e}")

    async def handle_rebuild(self):
        try:
            self._scan_images()
            return json_response({"tag_count": len(self.tag_to_image)})
        except Exception as e:
            logger.error(f"[{PLUGIN_NAME}] handle_rebuild 失败: {e}")
            return error_response(f"重建失败: {e}")