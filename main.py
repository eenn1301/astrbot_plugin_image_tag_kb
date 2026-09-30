import base64
import json
import inspect
from pathlib import Path
from typing import Any, Dict, List, Optional

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star
from astrbot.api.web import error_response, json_response, request


PLUGIN_NAME = "astrbot_plugin_image_tag_kb"


class ImageTagKBPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig = None):
        super().__init__(context)

        if config:
            self.config = config
        else:
            self.config = {}

        self.image_tags: Dict[str, str] = {}
        self.tag_to_image: Dict[str, str] = {}
        self.image_dir: Optional[Path] = None
        self.mapping_file: Optional[Path] = None
        self.settings_file: Optional[Path] = None

        self._load_config()
        self.settings: Dict[str, Any] = self._load_settings()

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
        context.register_web_api(
            f"/{PLUGIN_NAME}/kbs", self.handle_list_kbs, ["GET"], "列出知识库"
        )
        context.register_web_api(
            f"/{PLUGIN_NAME}/set_kb", self.handle_set_kb, ["POST"], "选择知识库"
        )
        logger.info(f"[{PLUGIN_NAME}] Web API 已注册")

    # ==================== 生命周期 ====================

    async def initialize(self):
        self._scan_images()
        logger.info(
            f"[{PLUGIN_NAME}] 已就绪 | 图片目录: {self.image_dir} | 图片数: {len(self.image_tags)}"
        )
        logger.info(
            f"[{PLUGIN_NAME}] 当前使用的知识库: {self._current_kb_name()!r}"
        )

    def _load_config(self):
        plugin_dir = Path(__file__).parent
        default_dir = str(plugin_dir / "data" / "images")
        image_dir_str = self.config.get("image_dir", default_dir)
        self.image_dir = Path(image_dir_str)
        if not self.image_dir.is_absolute():
            self.image_dir = plugin_dir / image_dir_str
        self.image_dir.mkdir(parents=True, exist_ok=True)

        self.mapping_file = plugin_dir / "data" / "mapping.json"
        self.mapping_file.parent.mkdir(parents=True, exist_ok=True)
        self.settings_file = plugin_dir / "data" / "settings.json"

    # ==================== settings.json ====================

    def _load_settings(self) -> Dict[str, Any]:
        if not self.settings_file or not self.settings_file.exists():
            return {}
        try:
            with open(self.settings_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except Exception as e:
            logger.error(f"读取 settings.json 失败: {e}")
        return {}

    def _save_settings(self):
        if not self.settings_file:
            return
        try:
            with open(self.settings_file, "w", encoding="utf-8") as f:
                json.dump(self.settings, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"保存 settings.json 失败: {e}")

    def _current_kb_name(self) -> str:
        kb_name = self.settings.get("kb_name", "")
        if not kb_name:
            kb_name = self.config.get("kb_name", "")
        return str(kb_name).strip() if kb_name else ""

    # ==================== mapping.json ====================

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

    @staticmethod
    def _extract_text(item: Any) -> str:
        if item is None:
            return ""
        if isinstance(item, str):
            return item
        if isinstance(item, dict):
            for key in ("content", "text", "page_content", "chunk", "document"):
                if key in item and item[key]:
                    return str(item[key])
            return json.dumps(item, ensure_ascii=False)
        for attr in ("content", "text", "page_content", "chunk", "document"):
            val = getattr(item, attr, None)
            if val:
                return str(val)
        return str(item)

    async def _try_retrieve(self, kb_manager, q: str, kb_name: str, top_k: int):
        """尝试多种 retrieve 调用方式，兼容不同 AstrBot 版本。"""
        candidates = [
            ("query + kb_names + top_k",
             lambda: kb_manager.retrieve(query=q, kb_names=[kb_name], top_k=top_k)),
            ("query + kb_names",
             lambda: kb_manager.retrieve(query=q, kb_names=[kb_name])),
            ("kb_names + query",
             lambda: kb_manager.retrieve(kb_names=[kb_name], query=q)),
            ("query only",
             lambda: kb_manager.retrieve(query=q)),
            ("positional q",
             lambda: kb_manager.retrieve(q)),
            ("positional q + kb_names",
             lambda: kb_manager.retrieve(q, [kb_name])),
            ("positional q + kb_names + top_k",
             lambda: kb_manager.retrieve(q, [kb_name], top_k)),
        ]

        last_err = None
        for desc, fn in candidates:
            try:
                result = fn()
                if inspect.isawaitable(result):
                    result = await result
                logger.info(f"[{PLUGIN_NAME}] retrieve 调用成功，方式: {desc}")
                return result, desc
            except TypeError as e:
                last_err = e
                continue
            except Exception as e:
                logger.error(f"[{PLUGIN_NAME}] retrieve 方式 [{desc}] 异常: {e}")
                return None, str(e)

        return None, f"所有 retrieve 调用方式均失败，最后一次错误: {last_err}"

    async def _find_knowledge_docs(self, query: str) -> List[str]:
        """返回检索到的文本片段列表（已按配置过滤和截断）。"""
        q = query.strip()
        if not q:
            return []

        kb_name = self._current_kb_name()
        if not kb_name:
            logger.info(f"[{PLUGIN_NAME}] 未选择知识库，跳过检索")
            return []

        kb_manager = getattr(self.context, "kb_manager", None)
        if kb_manager is None:
            logger.error(f"[{PLUGIN_NAME}] context 没有 kb_manager 属性")
            return []

        # 核心：默认只取 1 条，避免返回一大堆无关内容
        top_k = int(
            self.settings.get("kb_top_k")
            or self.config.get("kb_top_k", 1)
        )
        top_k = max(1, min(top_k, 5))  # 硬性限制 1~5

        results, desc = await self._try_retrieve(kb_manager, q, kb_name, top_k)
        if results is None:
            logger.warning(f"[{PLUGIN_NAME}] 知识库检索失败: {desc}")
            return []

        if not results:
            logger.info(f"[{PLUGIN_NAME}] 知识库「{kb_name}」检索无结果")
            return []

        docs = results
        if isinstance(results, dict) and "results" in results:
            docs = results["results"]
        elif hasattr(results, "results"):
            docs = results.results

        try:
            docs = list(docs)
        except TypeError:
            docs = [docs]

        # 截取前 top_k 条
        docs = docs[:top_k]

        logger.info(
            f"[{PLUGIN_NAME}] 知识库「{kb_name}」检索到 {len(docs)} 条结果"
        )

        # 单条最大长度（字符数），默认 1000
        max_chars = int(
            self.settings.get("kb_max_chars")
            or self.config.get("kb_max_chars", 1000)
        )

        texts = []
        for d in docs:
            t = self._extract_text(d)
            if not t:
                continue
            if max_chars > 0 and len(t) > max_chars:
                t = t[:max_chars] + "…"
            texts.append(t)
        return texts

    # ==================== LLM 摘要 ====================

    async def _summarize_with_llm(
        self, event: AstrMessageEvent, question: str, context_text: str
    ) -> Optional[str]:
        """用 LLM 基于检索到的上下文生成简洁回答。失败返回 None。"""
        try:
            get_provider = getattr(self.context, "get_using_provider", None)
            if not callable(get_provider):
                logger.warning(f"[{PLUGIN_NAME}] context 没有 get_using_provider")
                return None

            # 优先按当前会话取 provider，失败时退化为无参调用
            provider = None
            try:
                provider = get_provider(umo=event.unified_msg_origin)
                if inspect.isawaitable(provider):
                    provider = await provider
            except TypeError:
                provider = get_provider()
                if inspect.isawaitable(provider):
                    provider = await provider
            except Exception:
                provider = None

            if not provider:
                logger.warning(f"[{PLUGIN_NAME}] 未能获取当前 LLM provider")
                return None

            system_prompt = (
                "你是一个知识库助手。请根据下方提供的资料，用简洁、"
                "准确的中文回答用户的问题。只回答与问题直接相关的内容，"
                "不要复述资料全文，不要编造资料里没有的信息。"
                "如果资料与问题无关，请说“资料中未找到相关内容”。"
            )
            user_prompt = (
                f"【用户问题】\n{question}\n\n"
                f"【参考资料】\n{context_text}\n\n"
                f"请基于参考资料回答用户问题："
            )

            resp = await provider.text_chat(
                prompt=user_prompt,
                system_prompt=system_prompt,
                session_id=None,
            )

            # 兼容不同版本的返回结构
            text = None
            if hasattr(resp, "completion_text"):
                text = resp.completion_text
            elif isinstance(resp, dict):
                text = resp.get("completion_text") or resp.get("text")
            elif isinstance(resp, str):
                text = resp

            if text:
                logger.info(f"[{PLUGIN_NAME}] LLM 摘要成功，长度: {len(text)}")
                return str(text).strip()
            logger.warning(f"[{PLUGIN_NAME}] LLM 返回结构无法解析: {type(resp)}")
            return None
        except Exception as e:
            logger.error(f"[{PLUGIN_NAME}] LLM 摘要失败: {e}", exc_info=True)
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

        docs = await self._find_knowledge_docs(text)
        images = self._find_images(text)

        explanation: Optional[str] = None
        if docs:
            context_text = "\n\n---\n\n".join(docs)

            use_llm = self.settings.get(
                "use_llm_summary",
                self.config.get("use_llm_summary", True),
            )
            if use_llm:
                explanation = await self._summarize_with_llm(
                    event, text, context_text
                )
                if not explanation:
                    logger.info(
                        f"[{PLUGIN_NAME}] LLM 摘要失败，退化为只取前 1 条原文"
                    )
                    explanation = docs[0]
            else:
                # 不使用 LLM 时，直接输出最相关的那条
                explanation = docs[0]

        logger.info(
            f"[{PLUGIN_NAME}] 检索 | 消息: {text!r} | "
            f"知识库: {'有' if explanation else '无'} | 图片: {len(images)} 张"
        )

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
        kb_name = self._current_kb_name()
        msg = (
            f"📊 图片标签知识库统计\n"
            f"图片目录：{self.image_dir}\n"
            f"标签总数：{total_tags}\n"
            f"图片文件数：{unique_images}\n"
            f"知识库：{kb_name or '（未选择）'}"
        )
        yield event.plain_result(msg)

    # ==================== Web API ====================

    async def handle_upload(self):
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
        try:
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
        try:
            if not self.image_dir:
                return error_response("插件未初始化")

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

    # ==================== 知识库列表 / 选择 ====================

    async def _list_kb_names(self) -> List[str]:
        kb_manager = getattr(self.context, "kb_manager", None)
        if kb_manager is None:
            return []

        names: List[str] = []

        method = getattr(kb_manager, "get_all_kbs", None)
        if callable(method):
            try:
                result = method()
                if hasattr(result, "__await__"):
                    result = await result
                for item in result or []:
                    n = getattr(item, "kb_name", None) or getattr(item, "name", None)
                    if n:
                        names.append(str(n))
            except Exception as e:
                logger.warning(f"[{PLUGIN_NAME}] get_all_kbs 失败: {e}")

        if not names:
            for method_name in ("list_kbs", "get_kbs", "all_kbs"):
                m = getattr(kb_manager, method_name, None)
                if callable(m):
                    try:
                        result = m()
                        if hasattr(result, "__await__"):
                            result = await result
                        for item in result or []:
                            n = getattr(item, "kb_name", None) or getattr(item, "name", None)
                            if n:
                                names.append(str(n))
                        if names:
                            break
                    except Exception:
                        pass

        if not names:
            for attr in ("kbs", "knowledge_bases", "_kbs", "kb_list", "_kb_list"):
                val = getattr(kb_manager, attr, None)
                if isinstance(val, dict):
                    names = list(val.keys())
                    break
                if isinstance(val, list):
                    for k in val:
                        n = getattr(k, "kb_name", None) or getattr(k, "name", None)
                        if n:
                            names.append(str(n))
                    if names:
                        break

        return sorted(set(n for n in names if n))

    async def handle_list_kbs(self):
        try:
            names = await self._list_kb_names()
            logger.info(f"[{PLUGIN_NAME}] 可用知识库: {names}")
            return json_response({
                "kbs": names,
                "current": self._current_kb_name(),
            })
        except Exception as e:
            logger.error(f"[{PLUGIN_NAME}] handle_list_kbs 失败: {e}")
            return error_response(f"列出知识库失败: {e}")

    async def handle_set_kb(self):
        try:
            payload = await request.json(default={})
            if not isinstance(payload, dict):
                payload = {}
            kb_name = str(payload.get("kb_name", "")).strip()
            self.settings["kb_name"] = kb_name
            self._save_settings()
            logger.info(f"[{PLUGIN_NAME}] 知识库已切换为: {kb_name!r}")
            return json_response({"kb_name": kb_name})
        except Exception as e:
            logger.error(f"[{PLUGIN_NAME}] handle_set_kb 失败: {e}")
            return error_response(f"设置知识库失败: {e}")