"""
视频助手 API 中间件服务器
连接SillyTavern脚本和ComfyUI视频生成流水线

启动方式: python middleware_server.py [--port 5000] [--config config.json]
"""

import json
import os
import sys
import uuid
import time
import threading
import argparse
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from typing import Dict, Optional
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from scripts.scene_processor import SceneProcessor, PipelineConfig
from scripts.chat_parser import ChatParser


# 全局任务管理
class TaskManager:
    """任务管理器"""
    
    def __init__(self):
        self.tasks: Dict[str, Dict] = {}
        self.lock = threading.Lock()
    
    def create_task(self) -> str:
        task_id = str(uuid.uuid4())[:8]
        with self.lock:
            self.tasks[task_id] = {
                "id": task_id,
                "status": "pending",
                "percent": 0,
                "message": "等待开始",
                "current": 0,
                "total": 0,
                "result": None,
                "error": None,
                "created_at": datetime.now().isoformat(),
                "updated_at": datetime.now().isoformat()
            }
        return task_id
    
    def update_task(self, task_id: str, **kwargs):
        with self.lock:
            if task_id in self.tasks:
                self.tasks[task_id].update(kwargs)
                self.tasks[task_id]["updated_at"] = datetime.now().isoformat()
    
    def get_task(self, task_id: str) -> Optional[Dict]:
        with self.lock:
            return self.tasks.get(task_id)


# 全局实例
task_manager = TaskManager()
config = PipelineConfig()
processor: Optional[SceneProcessor] = None


def load_config(config_path: str):
    """加载配置"""
    global config
    if os.path.exists(config_path):
        with open(config_path, "r") as f:
            data = json.load(f)
            
            # ComfyUI
            if "comfyui" in data:
                config.comfyui_host = data["comfyui"].get("host", config.comfyui_host)
                config.comfyui_port = data["comfyui"].get("port", config.comfyui_port)
                config.comfyui_use_https = data["comfyui"].get("use_https", config.comfyui_use_https)
                config.comfyui_username = data["comfyui"].get("username", config.comfyui_username)
                config.comfyui_password = data["comfyui"].get("password", config.comfyui_password)
            
            # LLM
            if "llm" in data:
                config.llm_api_base = data["llm"].get("api_base", config.llm_api_base)
                config.llm_api_key = data["llm"].get("api_key", config.llm_api_key)
                config.llm_model = data["llm"].get("model", config.llm_model)
            
            # Image
            if "image" in data:
                for k, v in data["image"].items():
                    attr = f"image_{k}"
                    if hasattr(config, attr):
                        setattr(config, attr, v)
            
            # Video
            if "video" in data:
                for k, v in data["video"].items():
                    attr = f"video_{k}"
                    if hasattr(config, attr):
                        setattr(config, attr, v)
            
            # Scene
            if "scene" in data:
                config.scene_detection = data["scene"].get("detection", config.scene_detection)
                config.messages_per_scene = data["scene"].get("messages_per_scene", config.messages_per_scene)
                config.max_scenes = data["scene"].get("max_scenes", config.max_scenes)
                config.shots_per_scene = data["scene"].get("shots_per_scene", config.shots_per_scene)
                config.seconds_per_shot = data["scene"].get("seconds_per_shot", config.seconds_per_shot)
            
            # Output
            if "output" in data:
                config.output_dir = data["output"].get("dir", config.output_dir)
                config.temp_dir = data["output"].get("temp_dir", config.temp_dir)


class APIHandler(BaseHTTPRequestHandler):
    """API请求处理器"""
    
    def log_message(self, format, *args):
        """自定义日志格式"""
        timestamp = datetime.now().strftime("%H:%M:%S")
        print(f"[{timestamp}] {args[0]}")
    
    def send_json(self, data: dict, status: int = 200):
        """发送JSON响应"""
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode("utf-8"))
    
    def read_json(self) -> dict:
        """读取JSON请求体"""
        length = int(self.headers.get("Content-Length", 0))
        if length == 0:
            return {}
        body = self.rfile.read(length)
        return json.loads(body.decode("utf-8"))
    
    def do_OPTIONS(self):
        """处理CORS预检请求"""
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
    
    def do_GET(self):
        """处理GET请求"""
        parsed = urlparse(self.path)
        path = parsed.path
        
        if path == "/health":
            self.handle_health()
        elif path == "/api/progress":
            self.handle_progress(parse_qs(parsed.query))
        elif path.startswith("/api/progress/"):
            task_id = path.split("/")[-1]
            self.handle_progress_by_id(task_id)
        elif path == "/api/tasks":
            self.handle_list_tasks()
        elif path == "/api/config":
            self.handle_get_config()
        else:
            self.send_json({"error": "Not Found"}, 404)
    
    def do_POST(self):
        """处理POST请求"""
        parsed = urlparse(self.path)
        path = parsed.path
        
        if path == "/api/analyze":
            self.handle_analyze()
        elif path == "/api/generate":
            self.handle_generate()
        elif path == "/api/export":
            self.handle_export()
        elif path == "/api/parse":
            self.handle_parse()
        else:
            self.send_json({"error": "Not Found"}, 404)
    
    # ---- 处理函数 ----
    
    def handle_health(self):
        """健康检查"""
        # 测试ComfyUI连接
        comfyui_ok = False
        try:
            global processor
            if processor:
                result = processor.client.test_connection()
                comfyui_ok = result.get("success", False)
        except:
            pass
        
        self.send_json({
            "status": "ok",
            "service": "video-helper-middleware",
            "version": "1.0.0",
            "comfyui_connected": comfyui_ok,
            "timestamp": datetime.now().isoformat()
        })
    
    def handle_analyze(self):
        """分析聊天场景"""
        data = self.read_json()
        messages = data.get("messages", [])
        scene_settings = data.get("settings", {})
        
        if not messages:
            self.send_json({"error": "没有消息数据"}, 400)
            return
        
        try:
            # 创建临时JSONL文件
            temp_path = os.path.join(config.temp_dir, "temp_chat.jsonl")
            os.makedirs(config.temp_dir, exist_ok=True)
            
            with open(temp_path, "w", encoding="utf-8") as f:
                # 元数据行
                f.write(json.dumps({"chat_metadata": {}, "user_name": "User", "character_name": "Character"}, ensure_ascii=False) + "\n")
                # 消息行
                for msg in messages:
                    f.write(json.dumps(msg, ensure_ascii=False) + "\n")
            
            # 解析场景
            parser = ChatParser(temp_path)
            parser.parse()
            
            detection = scene_settings.get("scene_detection", config.scene_detection)
            msgs_per = scene_settings.get("messages_per_scene", config.messages_per_scene)
            
            scenes = parser.detect_scenes(method=detection, messages_per_scene=msgs_per)
            
            # 限制场景数
            max_scenes = scene_settings.get("max_scenes", config.max_scenes)
            if len(scenes) > max_scenes:
                scenes = scenes[:max_scenes]
            
            # 构建响应
            scenes_data = []
            for i, scene in enumerate(scenes):
                scenes_data.append({
                    "scene_id": i + 1,
                    "title": scene.title,
                    "messages": len(scene.messages),
                    "characters": scene.characters,
                    "location": scene.location,
                    "start_time": scene.start_time,
                    "end_time": scene.end_time,
                    "summary": scene.summary[:200] if scene.summary else ""
                })
            
            self.send_json({
                "success": True,
                "scenes": scenes_data,
                "total_scenes": len(scenes_data),
                "total_messages": len(messages)
            })
            
        except Exception as e:
            self.send_json({"error": str(e)}, 500)
    
    def handle_generate(self):
        """启动视频生成任务"""
        data = self.read_json()
        messages = data.get("messages", [])
        character_info = data.get("character_info", {})
        gen_settings = data.get("settings", {})
        
        if not messages:
            self.send_json({"error": "没有消息数据"}, 400)
            return
        
        # 创建任务
        task_id = task_manager.create_task()
        
        # 更新设置
        if gen_settings:
            config.shots_per_scene = gen_settings.get("shots_per_scene", config.shots_per_scene)
            config.seconds_per_shot = gen_settings.get("seconds_per_shot", config.seconds_per_shot)
        
        # 在后台线程执行生成
        thread = threading.Thread(
            target=_run_generation,
            args=(task_id, messages, character_info),
            daemon=True
        )
        thread.start()
        
        self.send_json({
            "success": True,
            "task_id": task_id,
            "message": "视频生成任务已创建"
        })
    
    def handle_progress_by_id(self, task_id: str):
        """查询任务进度"""
        task = task_manager.get_task(task_id)
        if task:
            self.send_json(task)
        else:
            self.send_json({"error": "任务不存在"}, 404)
    
    def handle_progress(self, params: dict):
        """查询任务进度（通过查询参数）"""
        task_ids = params.get("task_id", [])
        if task_ids:
            task = task_manager.get_task(task_ids[0])
            if task:
                self.send_json(task)
                return
        self.send_json({"error": "缺少task_id"}, 400)
    
    def handle_list_tasks(self):
        """列出所有任务"""
        with task_manager.lock:
            tasks = list(task_manager.tasks.values())
        self.send_json({"tasks": tasks})
    
    def handle_get_config(self):
        """获取当前配置"""
        self.send_json({
            "comfyui": {
                "host": config.comfyui_host,
                "port": config.comfyui_port
            },
            "scene": {
                "detection": config.scene_detection,
                "messages_per_scene": config.messages_per_scene,
                "max_scenes": config.max_scenes,
                "shots_per_scene": config.shots_per_scene,
                "seconds_per_shot": config.seconds_per_shot
            }
        })
    
    def handle_export(self):
        """导出JSONL文件"""
        data = self.read_json()
        messages = data.get("messages", [])
        
        if not messages:
            self.send_json({"error": "没有消息数据"}, 400)
            return
        
        # 保存JSONL
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"chat_export_{timestamp}.jsonl"
        filepath = os.path.join(config.output_dir, filename)
        os.makedirs(config.output_dir, exist_ok=True)
        
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(json.dumps({"export_time": datetime.now().isoformat()}, ensure_ascii=False) + "\n")
            for msg in messages:
                f.write(json.dumps(msg, ensure_ascii=False) + "\n")
        
        self.send_json({
            "success": True,
            "path": filepath,
            "filename": filename,
            "messages": len(messages)
        })
    
    def handle_parse(self):
        """解析JSONL文件"""
        data = self.read_json()
        filepath = data.get("path", "")
        
        if not filepath or not os.path.exists(filepath):
            self.send_json({"error": "文件不存在"}, 400)
            return
        
        try:
            parser = ChatParser(filepath)
            metadata, messages = parser.parse()
            scenes = parser.detect_scenes()
            
            self.send_json({
                "success": True,
                "messages": len(messages),
                "characters": list(parser.characters.keys()),
                "scenes": len(scenes)
            })
        except Exception as e:
            self.send_json({"error": str(e)}, 500)


def _run_generation(task_id: str, messages: list, character_info: dict):
    """后台执行视频生成"""
    global processor
    
    try:
        task_manager.update_task(task_id, status="running", message="初始化处理器", percent=0)
        
        # 初始化处理器
        processor = SceneProcessor(config)
        
        # 创建临时JSONL文件
        temp_path = os.path.join(config.temp_dir, f"task_{task_id}.jsonl")
        os.makedirs(config.temp_dir, exist_ok=True)
        
        with open(temp_path, "w", encoding="utf-8") as f:
            f.write(json.dumps({"task_id": task_id}, ensure_ascii=False) + "\n")
            for msg in messages:
                f.write(json.dumps(msg, ensure_ascii=False) + "\n")
        
        # 1. 加载聊天记录
        task_manager.update_task(task_id, message="加载聊天记录", percent=5)
        scene_count = processor.load_chat(temp_path)
        
        # 2. 分析场景
        task_manager.update_task(task_id, 
            message="分析场景中", 
            percent=10,
            total=scene_count
        )
        
        def analysis_progress(current, total, title):
            task_manager.update_task(task_id,
                message=f"分析场景: {title}",
                percent=10 + int(40 * current / total),
                current=current,
                total=total
            )
        
        processor.analyze_scenes(progress_callback=analysis_progress)
        
        # 3. 生成视频
        task_manager.update_task(task_id, message="开始生成视频", percent=50)
        
        result = processor.run_pipeline(temp_path, analyze_only=False)
        
        # 4. 完成
        task_manager.update_task(task_id,
            status="completed",
            message="生成完成",
            percent=100,
            result=result
        )
        
    except Exception as e:
        task_manager.update_task(task_id,
            status="error",
            message=f"生成失败: {str(e)}",
            error=str(e)
        )


def main():
    parser = argparse.ArgumentParser(description="视频助手API中间件")
    parser.add_argument("--port", type=int, default=5000, help="监听端口")
    parser.add_argument("--host", default="0.0.0.0", help="监听地址")
    parser.add_argument("--config", default="config.json", help="配置文件路径")
    parser.add_argument("--debug", action="store_true", help="调试模式")
    
    args = parser.parse_args()
    
    # 加载配置
    config_path = args.config
    if not os.path.isabs(config_path):
        config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), config_path)
    
    load_config(config_path)
    
    # 确保目录存在
    os.makedirs(config.output_dir, exist_ok=True)
    os.makedirs(config.temp_dir, exist_ok=True)
    
    print("=" * 60)
    print("  视频助手 API 中间件")
    print("=" * 60)
    print(f"  监听地址: {args.host}:{args.port}")
    print(f"  ComfyUI:  {config.comfyui_host}:{config.comfyui_port}")
    print(f"  LLM API:  {config.llm_api_base}")
    print(f"  输出目录:  {config.output_dir}")
    print("=" * 60)
    
    # 启动服务器
    server = HTTPServer((args.host, args.port), APIHandler)
    
    try:
        print(f"\n服务器已启动: http://{args.host}:{args.port}")
        print("API端点:")
        print(f"  GET  /health           - 健康检查")
        print(f"  POST /api/analyze      - 分析场景")
        print(f"  POST /api/generate     - 生成视频")
        print(f"  GET  /api/progress/:id - 查询进度")
        print(f"  GET  /api/tasks        - 列出任务")
        print(f"  POST /api/export       - 导出JSONL")
        print("\n等待请求...\n")
        
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n服务器已停止")
        server.server_close()


if __name__ == "__main__":
    main()
