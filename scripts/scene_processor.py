"""
场景处理器 - 核心中间件
负责串联整个视频生成流水线：
聊天记录 -> 场景解析 -> 提示词生成 -> ComfyUI视频生成 -> 结果拼接
"""

import json
import os
import sys
import time
import subprocess
from typing import Dict, List, Optional
from dataclasses import dataclass, asdict

# 添加脚本目录到路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from chat_parser import ChatParser, Scene
from scene_analyzer import SceneAnalyzer, VideoPrompt
from comfyui_client import ComfyUIClient, WorkflowBuilder


@dataclass
class PipelineConfig:
    """流水线配置"""
    # ComfyUI连接
    comfyui_host: str = "127.0.0.1"
    comfyui_port: int = 8188
    comfyui_use_https: bool = False
    comfyui_username: str = ""
    comfyui_password: str = ""
    
    # LLM API
    llm_api_base: str = "https://api.openai.com/v1"
    llm_api_key: str = "your-api-key"
    llm_model: str = "gpt-4o-mini"
    
    # 图片生成
    image_model: str = "miaomiaoHarem_v20.safetensors"
    image_lora: str = ""
    image_lora_strength: float = 0.8
    image_width: int = 768
    image_height: int = 1280
    image_steps: int = 28
    image_cfg: float = 4.5
    image_sampler: str = "euler"
    image_scheduler: str = "simple"
    
    # 视频生成 (Wan2.1)
    video_model: str = "wan2.1_i2v_480p_14B_bf16.safetensors"
    video_frames: int = 49  # ~5秒 @ 10fps
    video_fps: int = 10
    video_width: int = 480
    video_height: int = 832
    video_steps: int = 30
    video_cfg: float = 5.0
    
    # 帧插值
    rife_enabled: bool = True
    rife_multiplier: int = 2  # 2x = 10fps -> 20fps
    
    # 超分
    upscale_enabled: bool = True
    upscale_model: str = "RealESRGAN_x4plus_anime_6B.pth"
    upscale_factor: int = 2
    
    # 场景设置
    scene_detection: str = "auto"  # auto, fixed, user_input
    messages_per_scene: int = 5
    max_scenes: int = 20
    shots_per_scene: int = 4
    seconds_per_shot: float = 5.0
    
    # 输出
    output_dir: str = ".\\output"
    temp_dir: str = ".\\temp"
    final_format: str = "mp4"
    final_fps: int = 24
    
    # 工作流文件
    workflow_keyframe: str = ".\\workflows\\keyframe_gen.json"
    workflow_i2v: str = ".\\workflows\\i2v_wan21.json"
    workflow_upscale: str = ".\\workflows\\video_upscale.json"


class SceneProcessor:
    """场景处理器 - 主控制器"""
    
    def __init__(self, config: Optional[PipelineConfig] = None):
        self.config = config or PipelineConfig()
        
        # 初始化组件
        self.client = ComfyUIClient(
            host=self.config.comfyui_host,
            port=self.config.comfyui_port,
            use_https=self.config.comfyui_use_https,
            username=self.config.comfyui_username,
            password=self.config.comfyui_password
        )
        
        self.analyzer = SceneAnalyzer(
            api_base=self.config.llm_api_base,
            api_key=self.config.llm_api_key,
            model=self.config.llm_model
        )
        
        self.parser: Optional[ChatParser] = None
        self.scenes: List[Scene] = []
        self.video_prompts: List[VideoPrompt] = []
        
        # 确保目录存在
        os.makedirs(self.config.output_dir, exist_ok=True)
        os.makedirs(self.config.temp_dir, exist_ok=True)
    
    def load_chat(self, jsonl_path: str) -> int:
        """加载聊天记录"""
        print(f"加载聊天记录: {jsonl_path}")
        
        self.parser = ChatParser(jsonl_path)
        metadata, messages = self.parser.parse()
        
        # 设置聊天标题
        chat_title = os.path.splitext(os.path.basename(jsonl_path))[0]
        self.parser.chat_title = chat_title
        
        print(f"  消息数: {len(messages)}")
        print(f"  角色: {list(self.parser.characters.keys())}")
        
        # 检测场景
        self.scenes = self.parser.detect_scenes(
            method=self.config.scene_detection,
            messages_per_scene=self.config.messages_per_scene
        )
        
        # 限制场景数
        if len(self.scenes) > self.config.max_scenes:
            print(f"  场景数超过限制({self.config.max_scenes})，截断")
            self.scenes = self.scenes[:self.config.max_scenes]
        
        print(f"  检测到场景数: {len(self.scenes)}")
        
        return len(self.scenes)
    
    def analyze_scenes(self, progress_callback=None) -> List[VideoPrompt]:
        """分析所有场景，生成视频提示词"""
        print("\n开始分析场景...")
        
        self.video_prompts = []
        
        # 先提取角色信息
        character_profiles = {}
        for name, profile in self.parser.characters.items():
            # 从第一个包含该角色的场景中提取特征
            for scene in self.scenes:
                if name in scene.characters:
                    scene_text = self.parser.get_scene_text_for_llm(scene)
                    try:
                        char_info = self.analyzer.extract_character_profile(
                            scene_text, name
                        )
                        profile.visual_tags = char_info.get("visual_tags", "")
                        profile.appearance = char_info.get("distinctive_features", "")
                        character_profiles[name] = profile
                        print(f"  角色 {name}: {profile.visual_tags[:80]}...")
                        break
                    except Exception as e:
                        print(f"  角色 {name} 提取失败: {e}")
        
        # 分析每个场景
        scenes_data = []
        for i, scene in enumerate(self.scenes):
            scenes_data.append({
                "scene_id": i + 1,
                "title": scene.title,
                "text": self.parser.get_scene_text_for_llm(scene)
            })
        
        # 批量分析
        self.video_prompts = self.analyzer.analyze_scenes_batch(
            scenes_data, character_profiles
        )
        
        for i, prompt in enumerate(self.video_prompts):
            if progress_callback:
                progress_callback(i + 1, len(self.video_prompts), prompt.scene_title)
            print(f"  场景 {prompt.scene_id}: {prompt.scene_title}")
            print(f"    图片提示词: {prompt.image_positive[:80]}...")
        
        return self.video_prompts
    
    def generate_keyframe(self, prompt: VideoPrompt, seed: int = -1) -> Optional[str]:
        """
        生成关键帧图片
        返回图片路径
        """
        print(f"  生成关键帧: 场景 {prompt.scene_id} - {prompt.scene_title}")
        
        # 加载工作流
        if not os.path.exists(self.config.workflow_keyframe):
            print(f"  工作流文件不存在: {self.config.workflow_keyframe}")
            return self._generate_keyframe_api(prompt, seed)
        
        workflow = WorkflowBuilder.load_workflow(self.config.workflow_keyframe)
        
        # 更新工作流参数
        # 查找并更新正向提示词
        text_nodes = WorkflowBuilder.find_nodes_by_class(workflow, "CLIPTextEncode")
        if len(text_nodes) >= 1:
            workflow = WorkflowBuilder.set_text_input(
                workflow, text_nodes[0], prompt.image_positive
            )
        if len(text_nodes) >= 2:
            workflow = WorkflowBuilder.set_text_input(
                workflow, text_nodes[1], prompt.image_negative
            )
        
        # 更新采样参数
        sampler_nodes = WorkflowBuilder.find_nodes_by_class(workflow, "KSampler")
        if sampler_nodes:
            if seed > 0:
                workflow = WorkflowBuilder.set_seed(workflow, sampler_nodes[0], seed)
        
        # 更新尺寸
        empty_nodes = WorkflowBuilder.find_nodes_by_class(workflow, "EmptyLatentImage")
        if empty_nodes:
            workflow = WorkflowBuilder.set_dimensions(
                workflow, empty_nodes[0], 
                self.config.image_width, self.config.image_height
            )
        
        # 提交工作流
        try:
            prompt_id = self.client.submit_workflow(workflow)
            result = self.client.wait_for_completion(prompt_id, timeout=300)
            
            if result["success"]:
                # 获取输出图片
                outputs = result["outputs"]
                for node_id, node_output in outputs.items():
                    if "images" in node_output:
                        for img in node_output["images"]:
                            img_data = self.client.get_image(
                                img["filename"],
                                img.get("subfolder", ""),
                                img.get("type", "output")
                            )
                            # 保存图片
                            output_path = os.path.join(
                                self.config.temp_dir,
                                f"keyframe_{prompt.scene_id}.png"
                            )
                            with open(output_path, "wb") as f:
                                f.write(img_data)
                            print(f"    关键帧已保存: {output_path}")
                            return output_path
            
            print(f"    生成失败: {result.get('error', '未知错误')}")
            return None
            
        except Exception as e:
            print(f"    生成异常: {e}")
            return None
    
    def _generate_keyframe_api(self, prompt: VideoPrompt, seed: int = -1) -> Optional[str]:
        """通过API直接构建工作流生成关键帧（无需预设工作流文件）"""
        import random
        
        if seed <= 0:
            seed = random.randint(1, 2**32)
        
        # 构建基础文生图工作流
        workflow = {
            "1": {
                "class_type": "CheckpointLoaderSimple",
                "inputs": {"ckpt_name": self.config.image_model}
            },
            "2": {
                "class_type": "CLIPTextEncode",
                "inputs": {
                    "text": prompt.image_positive,
                    "clip": ["1", 1]
                }
            },
            "3": {
                "class_type": "CLIPTextEncode",
                "inputs": {
                    "text": prompt.image_negative,
                    "clip": ["1", 1]
                }
            },
            "4": {
                "class_type": "EmptyLatentImage",
                "inputs": {
                    "width": self.config.image_width,
                    "height": self.config.image_height,
                    "batch_size": 1
                }
            },
            "5": {
                "class_type": "KSampler",
                "inputs": {
                    "seed": seed,
                    "steps": self.config.image_steps,
                    "cfg": self.config.image_cfg,
                    "sampler_name": self.config.image_sampler,
                    "scheduler": self.config.image_scheduler,
                    "denoise": 1.0,
                    "model": ["1", 0],
                    "positive": ["2", 0],
                    "negative": ["3", 0],
                    "latent_image": ["4", 0]
                }
            },
            "6": {
                "class_type": "VAEDecode",
                "inputs": {
                    "samples": ["5", 0],
                    "vae": ["1", 2]
                }
            },
            "7": {
                "class_type": "SaveImage",
                "inputs": {
                    "filename_prefix": f"keyframe_{prompt.scene_id}",
                    "images": ["6", 0]
                }
            }
        }
        
        # 如果有LoRA，添加LoRA节点
        if self.config.image_lora:
            workflow["8"] = {
                "class_type": "LoraLoader",
                "inputs": {
                    "lora_name": self.config.image_lora,
                    "strength_model": self.config.image_lora_strength,
                    "strength_clip": self.config.image_lora_strength,
                    "model": ["1", 0],
                    "clip": ["1", 1]
                }
            }
            # 更新后续节点的输入
            workflow["2"]["inputs"]["clip"] = ["8", 1]
            workflow["3"]["inputs"]["clip"] = ["8", 1]
            workflow["5"]["inputs"]["model"] = ["8", 0]
        
        try:
            prompt_id = self.client.submit_workflow(workflow)
            result = self.client.wait_for_completion(prompt_id, timeout=300)
            
            if result["success"]:
                outputs = result["outputs"]
                for node_id, node_output in outputs.items():
                    if "images" in node_output:
                        for img in node_output["images"]:
                            img_data = self.client.get_image(
                                img["filename"],
                                img.get("subfolder", ""),
                                img.get("type", "output")
                            )
                            output_path = os.path.join(
                                self.config.temp_dir,
                                f"keyframe_{prompt.scene_id}.png"
                            )
                            with open(output_path, "wb") as f:
                                f.write(img_data)
                            return output_path
            
            return None
            
        except Exception as e:
            print(f"    API生成异常: {e}")
            return None
    
    def generate_video_from_image(self, image_path: str, 
                                  prompt: VideoPrompt,
                                  shot_index: int = 0) -> Optional[str]:
        """
        从提示词直接生成视频（统一工作流：文生图+图生视频）
        image_path 参数保留兼容性但不再使用（图片在ComfyUI内部生成）
        """
        print(f"  生成视频: 场景{prompt.scene_id}_镜头{shot_index}")
        
        # 构建统一工作流
        workflow = self._build_i2v_workflow(image_path, prompt, shot_index)
        
        if workflow is None:
            print(f"    工作流构建失败")
            return None
        
        try:
            prompt_id = self.client.submit_workflow(workflow)
            
            def progress_cb(info):
                if info.get("running"):
                    print(f"    生成中... ({info['elapsed']:.0f}s)", end="\r")
            
            result = self.client.wait_for_completion(
                prompt_id, timeout=900, progress_callback=progress_cb
            )
            
            if result["success"]:
                outputs = result["outputs"]
                for node_id, node_output in outputs.items():
                    # VHS_VideoCombine 输出格式
                    if "gifs" in node_output:
                        for gif in node_output["gifs"]:
                            video_data = self.client.get_image(
                                gif["filename"],
                                gif.get("subfolder", ""),
                                gif.get("type", "output")
                            )
                            output_path = os.path.join(
                                self.config.temp_dir,
                                f"video_{prompt.scene_id}_shot{shot_index}.mp4"
                            )
                            with open(output_path, "wb") as f:
                                f.write(video_data)
                            print(f"    视频已保存: {output_path}")
                            return output_path
                    
                    # SaveAnimatedWEBP 输出格式
                    if "images" in node_output:
                        for img in node_output["images"]:
                            if img.get("filename", "").endswith((".webp", ".gif", ".mp4")):
                                video_data = self.client.get_image(
                                    img["filename"],
                                    img.get("subfolder", ""),
                                    img.get("type", "output")
                                )
                                output_path = os.path.join(
                                    self.config.temp_dir,
                                    f"video_{prompt.scene_id}_shot{shot_index}.webp"
                                )
                                with open(output_path, "wb") as f:
                                    f.write(video_data)
                                print(f"    视频已保存: {output_path}")
                                return output_path
            
            print(f"    视频生成失败: {result.get('error', '未知错误')}")
            return None
            
        except Exception as e:
            print(f"    视频生成异常: {e}")
            return None
    
    def _build_i2v_workflow(self, image_path: str, 
                           prompt: VideoPrompt, shot_index: int) -> Optional[Dict]:
        """构建统一的文生图+图生视频工作流"""
        if os.path.exists(self.config.workflow_i2v):
            workflow = WorkflowBuilder.load_workflow(self.config.workflow_i2v)
            
            # 构建提示词
            shot_desc = ""
            if prompt.shots and shot_index < len(prompt.shots):
                shot_desc = prompt.shots[shot_index].get("description", "")
            video_prompt_text = f"{prompt.image_positive}, {prompt.video_style}, {shot_desc}"
            
            # 更新 SD 正向提示词 (节点2: CLIPTextEncode)
            sd_text_nodes = WorkflowBuilder.find_nodes_by_class(workflow, "CLIPTextEncode")
            # 节点2是SD提示词，节点13是Wan提示词
            # 按节点ID排序，前面的是SD，后面的是Wan
            sd_nodes = [n for n in sd_text_nodes if int(n) < 10]
            wan_nodes = [n for n in sd_text_nodes if int(n) >= 10]
            
            if sd_nodes:
                workflow = WorkflowBuilder.set_text_input(
                    workflow, sd_nodes[0], prompt.image_positive
                )
            if len(sd_nodes) >= 2:
                workflow = WorkflowBuilder.set_text_input(
                    workflow, sd_nodes[1], prompt.image_negative
                )
            
            # 更新 Wan 视频提示词 (节点13: CLIPTextEncode for Wan)
            if wan_nodes:
                workflow = WorkflowBuilder.set_text_input(
                    workflow, wan_nodes[0], video_prompt_text
                )
            
            # 更新尺寸 (节点4: EmptyLatentImage)
            empty_nodes = WorkflowBuilder.find_nodes_by_class(workflow, "EmptyLatentImage")
            if empty_nodes:
                workflow = WorkflowBuilder.set_dimensions(
                    workflow, empty_nodes[0],
                    self.config.video_width, self.config.video_height
                )
            
            return workflow
        
        print(f"    需要预设工作流文件")
        return None
    
    def upscale_video(self, video_path: str) -> Optional[str]:
        """视频超分辨率"""
        if not self.config.upscale_enabled:
            return video_path
        
        print(f"  超分辨率处理: {os.path.basename(video_path)}")
        
        # 方案1: 使用FFmpeg + RealESRGAN
        # 先提取帧 -> 超分 -> 重新合成
        output_path = video_path.replace(".mp4", "_upscaled.mp4")
        
        try:
            # 使用ffmpeg提取帧
            frames_dir = os.path.join(self.config.temp_dir, "upscale_frames")
            os.makedirs(frames_dir, exist_ok=True)
            
            subprocess.run([
                "ffmpeg", "-i", video_path,
                "-qscale:v", "2",
                os.path.join(frames_dir, "frame_%04d.png")
            ], capture_output=True, check=True)
            
            # 这里应该调用超分模型
            # 暂时使用ffmpeg的scale滤镜作为占位
            subprocess.run([
                "ffmpeg", "-i", video_path,
                "-vf", f"scale=iw*{self.config.upscale_factor}:ih*{self.config.upscale_factor}:flags=lanczos",
                "-c:v", "libx264", "-crf", "18",
                "-y", output_path
            ], capture_output=True, check=True)
            
            print(f"    超分完成: {output_path}")
            return output_path
            
        except subprocess.CalledProcessError as e:
            print(f"    超分失败: {e}")
            return video_path
    
    def interpolate_frames(self, video_path: str) -> Optional[str]:
        """帧插值"""
        if not self.config.rife_enabled:
            return video_path
        
        print(f"  帧插值处理: {os.path.basename(video_path)}")
        
        output_path = video_path.replace(".mp4", "_interpolated.mp4")
        
        try:
            # 使用ffmpeg的minterpolate滤镜
            subprocess.run([
                "ffmpeg", "-i", video_path,
                "-vf", f"minterpolate=fps={self.config.video_fps * self.config.rife_multiplier}:mi_mode=mci:mc_mode=aobmc:me_mode=bidir:vsbmc=1",
                "-c:v", "libx264", "-crf", "18",
                "-y", output_path
            ], capture_output=True, check=True)
            
            print(f"    插值完成: {output_path}")
            return output_path
            
        except subprocess.CalledProcessError as e:
            print(f"    插值失败: {e}")
            return video_path
    
    def concat_videos(self, video_paths: List[str], output_name: str) -> Optional[str]:
        """拼接多个视频"""
        if not video_paths:
            return None
        
        if len(video_paths) == 1:
            return video_paths[0]
        
        output_path = os.path.join(self.config.output_dir, output_name)
        
        # 创建FFmpeg concat列表
        list_path = os.path.join(self.config.temp_dir, "concat_list.txt")
        with open(list_path, "w") as f:
            for vp in video_paths:
                f.write(f"file '{os.path.abspath(vp)}'\n")
        
        try:
            subprocess.run([
                "ffmpeg", "-f", "concat", "-safe", "0",
                "-i", list_path,
                "-c:v", "libx264", "-crf", "18",
                "-y", output_path
            ], capture_output=True, check=True)
            
            print(f"  拼接完成: {output_path}")
            return output_path
            
        except subprocess.CalledProcessError as e:
            print(f"  拼接失败: {e}")
            return None
    
    def run_pipeline(self, jsonl_path: str, 
                    scenes_only: bool = False,
                    analyze_only: bool = False) -> Dict:
        """
        运行完整流水线
        
        Args:
            jsonl_path: JSONL聊天记录路径
            scenes_only: 仅解析场景（不生成）
            analyze_only: 仅分析（生成提示词但不生图/视频）
        
        Returns:
            包含结果信息的字典
        """
        result = {
            "success": False,
            "chat_file": jsonl_path,
            "scenes": [],
            "videos": [],
            "final_video": None,
            "errors": []
        }
        
        # 1. 加载聊天记录
        try:
            scene_count = self.load_chat(jsonl_path)
            result["scene_count"] = scene_count
        except Exception as e:
            result["errors"].append(f"加载失败: {e}")
            return result
        
        # 2. 检测场景
        scenes_info = []
        for i, scene in enumerate(self.scenes):
            scenes_info.append({
                "scene_id": i + 1,
                "title": scene.title,
                "messages": len(scene.messages),
                "characters": scene.characters,
                "location": scene.location,
                "start_time": scene.start_time
            })
        result["scenes"] = scenes_info
        
        if scenes_only:
            result["success"] = True
            return result
        
        # 3. 测试ComfyUI连接
        conn_test = self.client.test_connection()
        if not conn_test["success"]:
            result["errors"].append(f"ComfyUI连接失败: {conn_test['error']}")
            print(f"\nComfyUI连接失败: {conn_test['error']}")
            print("将仅进行场景分析和提示词生成")
            analyze_only = True
        
        # 4. 分析场景
        try:
            self.analyze_scenes()
        except Exception as e:
            result["errors"].append(f"场景分析失败: {e}")
            return result
        
        # 保存提示词到文件
        prompts_path = os.path.join(self.config.output_dir, "video_prompts.json")
        prompts_data = []
        for p in self.video_prompts:
            prompts_data.append(asdict(p))
        with open(prompts_path, "w", encoding="utf-8") as f:
            json.dump(prompts_data, f, indent=2, ensure_ascii=False)
        print(f"\n提示词已保存: {prompts_path}")
        
        if analyze_only:
            result["success"] = True
            result["prompts_file"] = prompts_path
            return result
        
        # 5. 为每个场景生成视频
        import random
        
        for prompt in self.video_prompts:
            scene_videos = []
            seed = random.randint(1, 2**32)
            
            print(f"\n处理场景 {prompt.scene_id}: {prompt.scene_title}")
            
            # 5a. 生成关键帧
            keyframe_path = self.generate_keyframe(prompt, seed)
            
            if keyframe_path is None:
                result["errors"].append(f"场景{prompt.scene_id}关键帧生成失败")
                continue
            
            # 5b. 为每个分镜生成视频
            shots = prompt.shots if prompt.shots else [
                {"duration": 5, "description": prompt.video_description, "camera": "static"}
            ]
            
            for shot_idx in range(min(len(shots), self.config.shots_per_scene)):
                video_path = self.generate_video_from_image(
                    keyframe_path, prompt, shot_idx
                )
                
                if video_path:
                    # 5c. 帧插值
                    video_path = self.interpolate_frames(video_path)
                    
                    # 5d. 超分辨率
                    video_path = self.upscale_video(video_path)
                    
                    scene_videos.append(video_path)
            
            # 5e. 拼接场景内视频
            if scene_videos:
                scene_video = self.concat_videos(
                    scene_videos, 
                    f"scene_{prompt.scene_id:03d}_{prompt.scene_title[:20]}.mp4"
                )
                if scene_video:
                    result["videos"].append({
                        "scene_id": prompt.scene_id,
                        "title": prompt.scene_title,
                        "path": scene_video,
                        "shots": len(scene_videos)
                    })
        
        # 6. 拼接所有场景视频
        all_scene_videos = [v["path"] for v in result["videos"]]
        if all_scene_videos:
            final_name = f"{os.path.splitext(os.path.basename(jsonl_path))[0]}_final.mp4"
            final_path = self.concat_videos(all_scene_videos, final_name)
            result["final_video"] = final_path
        
        result["success"] = len(result["videos"]) > 0
        return result


def main():
    """命令行入口"""
    import argparse
    
    parser = argparse.ArgumentParser(description="SillyTavern聊天记录视频生成器")
    parser.add_argument("input", help="JSONL聊天记录文件路径")
    parser.add_argument("--scenes-only", action="store_true", help="仅解析场景")
    parser.add_argument("--analyze-only", action="store_true", help="仅分析生成提示词")
    parser.add_argument("--config", help="配置文件路径")
    parser.add_argument("--output", help="输出目录")
    
    args = parser.parse_args()
    
    # 加载配置
    config = PipelineConfig()
    if args.config and os.path.exists(args.config):
        with open(args.config, "r") as f:
            config_data = json.load(f)
            for k, v in config_data.items():
                if hasattr(config, k):
                    setattr(config, k, v)
    
    if args.output:
        config.output_dir = args.output
    
    # 运行流水线
    processor = SceneProcessor(config)
    result = processor.run_pipeline(
        args.input,
        scenes_only=args.scenes_only,
        analyze_only=args.analyze_only
    )
    
    # 输出结果
    print("\n" + "=" * 60)
    print("处理结果:")
    print(f"  成功: {result['success']}")
    print(f"  场景数: {result.get('scene_count', 0)}")
    print(f"  生成视频数: {len(result['videos'])}")
    if result['final_video']:
        print(f"  最终视频: {result['final_video']}")
    if result['errors']:
        print(f"  错误:")
        for err in result['errors']:
            print(f"    - {err}")
    
    # 保存结果报告
    report_path = os.path.join(config.output_dir, "pipeline_result.json")
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    print(f"\n结果报告: {report_path}")


if __name__ == "__main__":
    main()
