"""
LLM场景分析器
使用LLM分析聊天场景，生成适合视频/图片生成的提示词
"""

import json
import urllib.request
import urllib.error
import re
from typing import Dict, List, Optional
from dataclasses import dataclass


@dataclass
class VideoPrompt:
    """视频提示词"""
    scene_id: int
    scene_title: str
    
    # 图片生成提示词
    image_positive: str  # 正向提示词（booru风格）
    image_negative: str  # 负向提示词
    
    # 视频描述
    video_description: str  # 视频动作描述
    video_style: str  # 风格描述
    
    # 角色信息
    characters: List[Dict]  # [{name, tags, position}]
    
    # 环境信息
    environment: str
    lighting: str
    camera_angle: str
    
    # 分镜信息
    shots: List[Dict]  # [{duration, description, camera}]


class SceneAnalyzer:
    """场景分析器 - 使用LLM将聊天场景转化为视频提示词"""
    
    def __init__(self, api_base: str, api_key: str, model: str = "gpt-4o-mini"):
        self.api_base = api_base.rstrip("/")
        self.api_key = api_key
        self.model = model
    
    def _call_llm(self, system_prompt: str, user_prompt: str, 
                  temperature: float = 0.7) -> str:
        """调用LLM API"""
        url = f"{self.api_base}/chat/completions"
        
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            "temperature": temperature,
            "max_tokens": 4096
        }
        
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, method="POST")
        req.add_header("Content-Type", "application/json")
        req.add_header("Authorization", f"Bearer {self.api_key}")
        
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                result = json.loads(resp.read().decode())
                # 支持多种API格式
                choices = result.get("choices", [])
                if choices:
                    msg = choices[0].get("message", {})
                    return msg.get("content", "") or msg.get("text", "")
                return result.get("response", "") or result.get("output", "")
        except urllib.error.HTTPError as e:
            body = e.read().decode() if e.fp else ""
            raise Exception(f"LLM API错误 {e.code}: {body[:500]}")
    
    def analyze_scene(self, scene_text: str, scene_title: str,
                     character_profiles: Optional[Dict] = None) -> VideoPrompt:
        """
        分析单个场景，生成视频提示词
        """
        system_prompt = """你是一个专业的动漫视频导演和提示词工程师。
你的任务是将小说/聊天记录的场景描述转化为可用于AI生成动漫视频的精确提示词。

你需要输出一个JSON对象，包含以下字段：

```json
{
    "scene_title": "场景标题",
    "image_positive": "图片生成正向提示词（booru标签风格，英文，逗号分隔）",
    "image_negative": "图片生成负向提示词（英文）",
    "video_description": "视频动作描述（详细描述画面中发生的动作，中文）",
    "video_style": "风格描述（英文，如 anime style, cinematic lighting）",
    "characters": [
        {
            "name": "角色名",
            "tags": "角色外观标签（英文booru风格）",
            "position": "在画面中的位置描述"
        }
    ],
    "environment": "环境描述（英文标签）",
    "lighting": "光照描述（英文标签）",
    "camera_angle": "镜头角度（英文，如 close-up, medium shot, wide shot）",
    "shots": [
        {
            "duration": 5,
            "description": "分镜描述（中文）",
            "camera": "镜头运动（英文，如 static, slow zoom in, pan left）"
        }
    ]
}
```

重要规则：
1. image_positive 必须是英文booru标签风格，用逗号分隔
2. 必须包含质量标签：masterpiece, best quality, highly detailed
3. 风格必须是动漫风格：anime style, cel shading
4. 角色描述要包含外观特征（发色、瞳色、服装等）
5. 分镜建议3-5个，每个3-5秒，总计15-25秒
6. 镜头运动要配合叙事节奏
7. 注意保持角色外貌一致性
8. 负向提示词必须包含：lowres, bad anatomy, bad hands, text, error, missing fingers, extra digit, fewer digits, cropped, worst quality, low quality, normal quality, jpeg artifacts, signature, watermark, username, blurry, bad feet"""

        user_prompt = f"""请分析以下场景，生成动漫视频的提示词：

场景标题：{scene_title}

场景内容：
{scene_text[:3000]}

{self._format_character_profiles(character_profiles) if character_profiles else ""}

请输出JSON格式的结果。"""

        response = self._call_llm(system_prompt, user_prompt)
        
        # 解析JSON响应
        return self._parse_response(response, scene_title)
    
    def analyze_scenes_batch(self, scenes_text: List[Dict], 
                            character_profiles: Optional[Dict] = None) -> List[VideoPrompt]:
        """
        批量分析多个场景
        scenes_text: [{"scene_id": int, "title": str, "text": str}]
        """
        results = []
        
        for scene_info in scenes_text:
            try:
                prompt = self.analyze_scene(
                    scene_text=scene_info["text"],
                    scene_title=scene_info["title"],
                    character_profiles=character_profiles
                )
                prompt.scene_id = scene_info["scene_id"]
                results.append(prompt)
            except Exception as e:
                print(f"场景 {scene_info['scene_id']} 分析失败: {e}")
                # 创建默认提示词
                results.append(VideoPrompt(
                    scene_id=scene_info["scene_id"],
                    scene_title=scene_info["title"],
                    image_positive="masterpiece, best quality, anime style, 1girl",
                    image_negative="lowres, bad anatomy, worst quality, low quality",
                    video_description=scene_info["text"][:200],
                    video_style="anime style, cinematic",
                    characters=[],
                    environment="indoor",
                    lighting="soft lighting",
                    camera_angle="medium shot",
                    shots=[{"duration": 5, "description": scene_info["text"][:100], "camera": "static"}]
                ))
        
        return results
    
    def extract_character_profile(self, scene_text: str, 
                                 character_name: str) -> Dict:
        """从场景文本中提取角色外观特征"""
        system_prompt = """你是一个动漫角色设计师。从文本中提取角色的外观特征，输出booru标签风格的英文描述。

输出格式（JSON）：
```json
{
    "name": "角色名",
    "hair_color": "发色",
    "eye_color": "瞳色",
    "hair_style": "发型",
    "body_type": "体型",
    "outfit": "服装描述",
    "accessories": "配饰",
    "visual_tags": "完整的英文booru标签，逗号分隔",
    "age_appearance": "外观年龄",
    "distinctive_features": "独特特征"
}
```"""
        
        user_prompt = f"""从以下文本中提取角色"{character_name}"的外观特征：

{scene_text[:2000]}

请输出JSON格式。"""
        
        response = self._call_llm(system_prompt, user_prompt, temperature=0.3)
        return self._parse_json_response(response)
    
    def generate_video_prompt_for_segment(self, segment_text: str, 
                                         character_tags: str,
                                         style: str = "anime") -> str:
        """为单个视频片段生成精确的提示词"""
        system_prompt = f"""你是一个视频提示词专家。将场景描述转化为精确的{style}风格视频生成提示词。

规则：
1. 输出英文提示词
2. 使用booru标签格式（逗号分隔）
3. 包含：角色描述、动作、环境、光照、镜头
4. 包含质量标签：masterpiece, best quality
5. 风格标签：{style} style, cel shading, vibrant colors
6. 动作描述要具体、可执行
7. 总长度控制在200词以内
8. 不要输出解释，只输出提示词"""
        
        user_prompt = f"""角色固定标签：{character_tags}

场景描述：
{segment_text[:1000]}

请输出视频生成提示词（英文booru标签格式）："""
        
        return self._call_llm(system_prompt, user_prompt, temperature=0.5).strip()
    
    def _format_character_profiles(self, profiles: Dict) -> str:
        """格式化角色档案"""
        if not profiles:
            return ""
        
        lines = ["已知角色信息："]
        for name, profile in profiles.items():
            lines.append(f"- {name}: {profile.visual_tags or profile.appearance or '无'}")
        
        return "\n".join(lines)
    
    def _parse_response(self, response: str, scene_title: str) -> VideoPrompt:
        """解析LLM响应为VideoPrompt"""
        data = self._parse_json_response(response)
        
        if not data:
            # 解析失败，创建默认值
            return VideoPrompt(
                scene_id=0,
                scene_title=scene_title,
                image_positive="masterpiece, best quality, anime style",
                image_negative="lowres, bad anatomy, worst quality",
                video_description=response[:200] if response else "",
                video_style="anime style",
                characters=[],
                environment="",
                lighting="soft lighting",
                camera_angle="medium shot",
                shots=[]
            )
        
        return VideoPrompt(
            scene_id=0,
            scene_title=data.get("scene_title", scene_title),
            image_positive=data.get("image_positive", "masterpiece, best quality, anime style"),
            image_negative=data.get("image_negative", "lowres, bad anatomy, worst quality"),
            video_description=data.get("video_description", ""),
            video_style=data.get("video_style", "anime style"),
            characters=data.get("characters", []),
            environment=data.get("environment", ""),
            lighting=data.get("lighting", "soft lighting"),
            camera_angle=data.get("camera_angle", "medium shot"),
            shots=data.get("shots", [])
        )
    
    def _parse_json_response(self, response: str) -> Optional[Dict]:
        """从LLM响应中解析JSON"""
        if not response:
            return None
        
        # 尝试直接解析
        try:
            return json.loads(response)
        except json.JSONDecodeError:
            pass
        
        # 尝试提取JSON块
        json_match = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', response, re.DOTALL)
        if json_match:
            try:
                return json.loads(json_match.group(1))
            except json.JSONDecodeError:
                pass
        
        # 尝试找最外层的{}
        brace_match = re.search(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', response, re.DOTALL)
        if brace_match:
            try:
                return json.loads(brace_match.group())
            except json.JSONDecodeError:
                pass
        
        return None


if __name__ == "__main__":
    # 测试
    analyzer = SceneAnalyzer(
        api_base="https://api.openai.com/v1",
        api_key="your-api-key",
        model="gpt-4o-mini"
    )
    
    test_text = """The girl sat by the window, sunlight casting warm shadows across her face.
    She looked up from her book and smiled gently. The room was quiet and peaceful,
    with soft music playing in the background. Cherry blossoms drifted past the window."""
    
    print("正在测试场景分析...")
    result = analyzer.analyze_scene(test_text, "Afternoon Scene")
    print(f"\n场景标题: {result.scene_title}")
    print(f"图片提示词: {result.image_positive[:200]}...")
    print(f"视频描述: {result.video_description[:200]}...")
    print(f"分镜数: {len(result.shots)}")
