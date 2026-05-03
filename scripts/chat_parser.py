"""
SillyTavern JSONL 聊天记录解析器
解析聊天记录文件，提取场景、角色、环境信息
"""

import json
import re
import os
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class ChatMessage:
    """单条聊天消息"""
    index: int
    name: str
    is_user: bool
    is_system: bool
    send_date: str
    mes: str
    cleaned_text: str = ""
    word_count: int = 0
    
    # 解析后的结构化内容
    maintext: str = ""
    recap: str = ""
    thinking: str = ""
    img_gen_blocks: List[str] = field(default_factory=list)


@dataclass
class Scene:
    """场景/章节"""
    scene_id: int
    title: str
    messages: List[ChatMessage]
    start_time: str
    end_time: str
    summary: str = ""
    location: str = ""
    characters: List[str] = field(default_factory=list)
    
    # 生成的视频信息
    video_prompt: str = ""
    image_prompt: str = ""
    video_path: str = ""
    
    @property
    def text(self) -> str:
        """获取场景的完整文本"""
        return "\n\n".join(m.cleaned_text or m.mes for m in self.messages)


@dataclass 
class CharacterProfile:
    """角色档案"""
    name: str
    visual_tags: str = ""
    appearance: str = ""
    default_outfit: str = ""
    personality: str = ""


class ChatParser:
    """SillyTavern JSONL聊天记录解析器"""
    
    def __init__(self, file_path: str):
        self.file_path = file_path
        self.metadata: Dict = {}
        self.messages: List[ChatMessage] = []
        self.characters: Dict[str, CharacterProfile] = {}
        self.chat_title: str = ""
        
    def parse(self) -> Tuple[Dict, List[ChatMessage]]:
        """解析JSONL文件"""
        with open(self.file_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        
        # 第一行是元数据
        if lines:
            self._parse_metadata(lines[0])
        
        # 解析每条消息
        for i, line in enumerate(lines[1:], start=2):
            line = line.strip()
            if not line:
                continue
            try:
                msg = self._parse_message(line, i)
                if msg:
                    self.messages.append(msg)
            except json.JSONDecodeError:
                continue
        
        # 提取角色信息
        self._extract_characters()
        
        return self.metadata, self.messages
    
    def _parse_metadata(self, line: str):
        """解析元数据行"""
        try:
            # 跳过行号前缀
            json_match = re.search(r'\{.*\}', line)
            if json_match:
                self.metadata = json.loads(json_match.group())
        except json.JSONDecodeError:
            pass
    
    def _parse_message(self, line: str, line_num: int) -> Optional[ChatMessage]:
        """解析单条消息"""
        # 跳过行号前缀 (如 "     2|")
        json_match = re.search(r'\{.*\}', line)
        if not json_match:
            return None
        
        data = json.loads(json_match.group())
        
        msg = ChatMessage(
            index=line_num,
            name=data.get("name", ""),
            is_user=data.get("is_user", False),
            is_system=data.get("is_system", False),
            send_date=data.get("send_date", ""),
            mes=data.get("mes", "")
        )
        
        # 清理文本
        msg.cleaned_text = self._clean_message(msg.mes)
        msg.word_count = len(msg.cleaned_text)
        
        # 解析结构化标签
        self._parse_structured_content(msg)
        
        return msg
    
    def _clean_message(self, text: str) -> str:
        """清理消息文本，移除HTML/标签等"""
        # 移除<thinking>标签内容
        text = re.sub(r'<thinking>.*?</thinking>', '', text, flags=re.DOTALL)
        
        # 移除<content>标签包装
        text = re.sub(r'</?content>', '', text)
        
        # 移除<maintext>标签包装但保留内容
        text = re.sub(r'</?maintext>', '', text)
        
        # 移除[IMG_GEN]块
        text = re.sub(r'\[IMG_GEN\].*?\[/IMG_GEN\]', '', text, flags=re.DOTALL)
        
        # 移除HTML标签
        text = re.sub(r'<br\s*/?>', '\n', text)
        text = re.sub(r'<div[^>]*>.*?</div>', '', text, flags=re.DOTALL)
        text = re.sub(r'<font[^>]*>(.*?)</font>', r'\1', text, flags=re.DOTALL)
        text = re.sub(r'<[^>]+>', '', text)
        
        # 移除状态栏
        text = re.sub(r'<details>.*?</details>', '', text, flags=re.DOTALL)
        
        # 移除[no_gen] [scheduled]标记
        text = re.sub(r'\[no_gen\]|\[scheduled\]', '', text)
        
        # 清理多余空白
        text = re.sub(r'\n{3,}', '\n\n', text)
        text = text.strip()
        
        return text
    
    def _parse_structured_content(self, msg: ChatMessage):
        """解析结构化标签内容"""
        # 提取maintext
        mt = re.search(r'<maintext>\s*(.*?)\s*</maintext>', msg.mes, re.DOTALL)
        if mt:
            msg.maintext = self._clean_message(mt.group(1))
        
        # 提取recap（小总结）
        recap = re.search(r'<recap>(.*?)</recap>', msg.mes, re.DOTALL)
        if recap:
            # 提取小总结中的关键信息
            details = re.search(r'<details>(.*?)</details>', recap.group(1), re.DOTALL)
            if details:
                msg.recap = details.group(1).strip()
            else:
                msg.recap = recap.group(1).strip()
        
        # 提取thinking
        thinking = re.search(r'<thinking>(.*?)</thinking>', msg.mes, re.DOTALL)
        if thinking:
            msg.thinking = thinking.group(1).strip()
        
        # 提取IMG_GEN块
        img_blocks = re.findall(r'\[IMG_GEN\](.*?)\[/IMG_GEN\]', msg.mes, re.DOTALL)
        msg.img_gen_blocks = [b.strip() for b in img_blocks]
    
    def _extract_characters(self):
        """从消息中提取角色信息"""
        for msg in self.messages:
            if msg.name and msg.name not in self.characters:
                self.characters[msg.name] = CharacterProfile(
                    name=msg.name,
                    visual_tags="",
                    appearance=""
                )
    
    def detect_scenes(self, method: str = "auto", 
                     messages_per_scene: int = 5) -> List[Scene]:
        """
        检测场景边界
        
        method:
        - "auto": 自动检测（基于时间间隔和内容）
        - "fixed": 固定每N条消息一个场景
        - "user_input": 基于用户输入分割（每次用户发言=新场景）
        """
        scenes = []
        
        if method == "fixed":
            scenes = self._detect_scenes_fixed(messages_per_scene)
        elif method == "user_input":
            scenes = self._detect_scenes_by_user_input()
        else:  # auto
            scenes = self._detect_scenes_auto()
        
        # 为每个场景生成标题
        for i, scene in enumerate(scenes):
            scene.scene_id = i + 1
            if not scene.title:
                scene.title = f"第{i+1}章"
        
        return scenes
    
    def _detect_scenes_auto(self) -> List[Scene]:
        """自动场景检测"""
        scenes = []
        current_group = []
        last_date = None
        
        for msg in self.messages:
            if msg.is_system:
                continue
            
            # 解析时间
            current_date = self._extract_date(msg.send_date)
            
            # 判断是否是新场景
            is_new_scene = False
            
            if last_date and current_date:
                # 日期变化 = 新场景
                if current_date != last_date:
                    is_new_scene = True
            
            # 用户输入 = 新场景的候选
            if msg.is_user and current_group:
                # 如果当前组已经有足够内容，开始新场景
                total_words = sum(m.word_count for m in current_group)
                if total_words > 300:
                    is_new_scene = True
            
            if is_new_scene and current_group:
                scenes.append(self._create_scene(current_group))
                current_group = []
            
            current_group.append(msg)
            if current_date:
                last_date = current_date
        
        # 最后一组
        if current_group:
            scenes.append(self._create_scene(current_group))
        
        return scenes
    
    def _detect_scenes_fixed(self, n: int) -> List[Scene]:
        """固定消息数分场景"""
        scenes = []
        non_system_msgs = [m for m in self.messages if not m.is_system]
        
        for i in range(0, len(non_system_msgs), n):
            group = non_system_msgs[i:i+n]
            scenes.append(self._create_scene(group))
        
        return scenes
    
    def _detect_scenes_by_user_input(self) -> List[Scene]:
        """基于用户输入分割"""
        scenes = []
        current_group = []
        
        for msg in self.messages:
            if msg.is_system:
                continue
            
            if msg.is_user and current_group:
                scenes.append(self._create_scene(current_group))
                current_group = []
            
            current_group.append(msg)
        
        if current_group:
            scenes.append(self._create_scene(current_group))
        
        return scenes
    
    def _create_scene(self, messages: List[ChatMessage]) -> Scene:
        """从消息组创建场景"""
        # 提取时间范围
        start_time = messages[0].send_date if messages else ""
        end_time = messages[-1].send_date if messages else ""
        
        # 提取角色
        characters = list(set(m.name for m in messages if m.name))
        
        # 生成场景摘要（从recap中提取）
        summaries = [m.recap for m in messages if m.recap]
        summary = "\n".join(summaries) if summaries else ""
        
        # 尝试从summary中提取地点
        location = ""
        for s in summaries:
            loc_match = re.search(r'地点[：:]\s*(.+?)(?:\n|$)', s)
            if loc_match:
                location = loc_match.group(1).strip()
                break
        
        # 生成场景标题
        title = self._generate_scene_title(messages)
        
        return Scene(
            scene_id=0,
            title=title,
            messages=messages,
            start_time=start_time,
            end_time=end_time,
            summary=summary,
            location=location,
            characters=characters
        )
    
    def _generate_scene_title(self, messages: List[ChatMessage]) -> str:
        """从消息中生成场景标题"""
        # 从recap中提取时间和事件
        for msg in messages:
            if msg.recap:
                # 尝试提取"发生的事"
                event_match = re.search(r'发生的事[：:]\s*(.+?)(?:\n|$)', msg.recap)
                if event_match:
                    return event_match.group(1).strip()[:50]
                
                # 尝试提取时间
                time_match = re.search(r'时间[：:]\s*(.+?)(?:\n|$)', msg.recap)
                if time_match:
                    return time_match.group(1).strip()[:30]
        
        # 从maintext中提取前50字
        for msg in messages:
            text = msg.maintext or msg.cleaned_text
            if text and len(text) > 20:
                return text[:50].replace("\n", " ") + "..."
        
        return ""
    
    def _extract_date(self, date_str: str) -> Optional[str]:
        """提取日期"""
        if not date_str:
            return None
        try:
            dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
            return dt.strftime("%Y-%m-%d")
        except (ValueError, AttributeError):
            return None
    
    def get_scene_text_for_llm(self, scene: Scene, max_length: int = 3000) -> str:
        """获取适合发送给LLM的场景文本"""
        parts = []
        
        # 添加场景元信息
        if scene.location:
            parts.append(f"[地点: {scene.location}]")
        if scene.start_time:
            parts.append(f"[时间: {scene.start_time}]")
        if scene.characters:
            parts.append(f"[角色: {', '.join(scene.characters)}]")
        parts.append("")
        
        # 添加正文
        for msg in scene.messages:
            text = msg.maintext or msg.cleaned_text
            if text:
                prefix = f"[{msg.name}]" if msg.name else ""
                parts.append(f"{prefix}\n{text}")
        
        result = "\n\n".join(parts)
        
        # 截断到指定长度
        if len(result) > max_length:
            result = result[:max_length] + "\n...(截断)"
        
        return result
    
    def export_scenes_summary(self, scenes: List[Scene]) -> str:
        """导出场景摘要"""
        lines = [f"# {self.chat_title or '聊天记录'} - 场景摘要\n"]
        
        for scene in scenes:
            lines.append(f"## {scene.title}")
            lines.append(f"- 时间: {scene.start_time} ~ {scene.end_time}")
            lines.append(f"- 地点: {scene.location or '未标注'}")
            lines.append(f"- 角色: {', '.join(scene.characters)}")
            if scene.summary:
                lines.append(f"- 摘要: {scene.summary[:200]}")
            lines.append(f"- 消息数: {len(scene.messages)}")
            lines.append("")
        
        return "\n".join(lines)


if __name__ == "__main__":
    import sys
    
    if len(sys.argv) < 2:
        print("用法: python chat_parser.py <jsonl文件路径>")
        sys.exit(1)
    
    parser = ChatParser(sys.argv[1])
    metadata, messages = parser.parse()
    
    print(f"解析完成:")
    print(f"  消息总数: {len(messages)}")
    print(f"  角色数: {len(parser.characters)}")
    print(f"  角色列表: {list(parser.characters.keys())}")
    
    # 检测场景
    scenes = parser.detect_scenes(method="auto")
    print(f"\n自动检测场景数: {len(scenes)}")
    
    for scene in scenes:
        print(f"\n  {scene.title}")
        print(f"    消息: {len(scene.messages)}条")
        print(f"    角色: {scene.characters}")
        print(f"    时间: {scene.start_time}")
