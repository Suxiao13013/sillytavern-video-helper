"""
ComfyUI API 客户端
用于与ComfyUI服务器通信，提交工作流、监控进度、获取结果
"""

import json
import time
import uuid
import urllib.request
import urllib.parse
import urllib.error
import ssl
import os
from typing import Optional, Dict, Any, List, Callable


class ComfyUIClient:
    """ComfyUI API客户端"""
    
    def __init__(self, host: str = "127.0.0.1", port: int = 8188, 
                 client_id: Optional[str] = None, use_https: bool = False,
                 username: Optional[str] = None, password: Optional[str] = None):
        self.host = host
        self.port = port
        self.client_id = client_id or str(uuid.uuid4())
        self.use_https = use_https
        self.scheme = "https" if use_https else "http"
        self.base_url = f"{self.scheme}://{host}:{port}"
        self.username = username
        self.password = password
        
        # SSL context for self-signed certs
        self.ssl_ctx = ssl.create_default_context()
        self.ssl_ctx.check_hostname = False
        self.ssl_ctx.verify_mode = ssl.CERT_NONE
    
    def _request(self, method: str, path: str, data: Optional[bytes] = None,
                 headers: Optional[Dict] = None, timeout: int = 30) -> Any:
        """发送HTTP请求"""
        url = f"{self.base_url}{path}"
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Content-Type", "application/json")
        if headers:
            for k, v in headers.items():
                req.add_header(k, v)
        
        # Basic Auth
        if self.username and self.password:
            import base64
            credentials = base64.b64encode(f"{self.username}:{self.password}".encode()).decode()
            req.add_header("Authorization", f"Basic {credentials}")
        
        ctx = self.ssl_ctx if self.use_https else None
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            body = e.read().decode() if e.fp else ""
            raise Exception(f"HTTP {e.code}: {body[:500]}")
        except urllib.error.URLError as e:
            raise Exception(f"连接失败: {e.reason}")
    
    def test_connection(self) -> Dict:
        """测试连接"""
        try:
            result = self._request("GET", "/system_stats")
            return {"success": True, "stats": result}
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def get_system_stats(self) -> Dict:
        """获取系统状态"""
        return self._request("GET", "/system_stats")
    
    def get_extensions(self) -> List:
        """获取已安装的扩展/自定义节点"""
        return self._request("GET", "/extensions")
    
    def get_object_info(self) -> Dict:
        """获取所有可用节点信息"""
        return self._request("GET", "/object_info")
    
    def get_history(self, prompt_id: Optional[str] = None) -> Dict:
        """获取历史记录"""
        if prompt_id:
            return self._request("GET", f"/history/{prompt_id}")
        return self._request("GET", "/history")
    
    def get_queue(self) -> Dict:
        """获取当前队列"""
        return self._request("GET", "/queue")
    
    def interrupt(self) -> Dict:
        """中断当前生成"""
        return self._request("POST", "/interrupt")
    
    def submit_workflow(self, workflow: Dict, prompt_id: Optional[str] = None) -> str:
        """
        提交工作流到ComfyUI
        返回prompt_id用于追踪进度
        """
        if prompt_id is None:
            prompt_id = str(uuid.uuid4())
        
        payload = {
            "prompt": workflow,
            "client_id": self.client_id
        }
        
        result = self._request("POST", "/prompt", 
                              data=json.dumps(payload).encode())
        return result.get("prompt_id", prompt_id)
    
    def wait_for_completion(self, prompt_id: str, 
                           timeout: int = 600,
                           poll_interval: float = 2.0,
                           progress_callback: Optional[Callable] = None) -> Dict:
        """
        等待工作流完成
        返回包含输出信息的字典
        """
        start_time = time.time()
        
        while True:
            elapsed = time.time() - start_time
            if elapsed > timeout:
                raise TimeoutError(f"等待超时 ({timeout}s)")
            
            # 检查队列
            queue = self.get_queue()
            running = queue.get("queue_running", [])
            pending = queue.get("queue_pending", [])
            
            # 检查是否还在队列中
            in_running = any(item[1] == prompt_id for item in running)
            in_pending = any(item[1] == prompt_id for item in pending)
            
            if progress_callback:
                progress_callback({
                    "elapsed": elapsed,
                    "in_queue": in_running or in_pending,
                    "running": in_running,
                    "pending": in_pending,
                    "queue_position": next(
                        (i for i, item in enumerate(pending) if item[1] == prompt_id), 
                        -1
                    )
                })
            
            # 检查历史记录（完成的任务会在这里）
            history = self.get_history(prompt_id)
            if prompt_id in history:
                entry = history[prompt_id]
                status = entry.get("status", {})
                if status.get("completed", False) or status.get("status_str") == "success":
                    return {
                        "success": True,
                        "prompt_id": prompt_id,
                        "outputs": entry.get("outputs", {}),
                        "elapsed": elapsed
                    }
                if status.get("status_str") == "error":
                    return {
                        "success": False,
                        "prompt_id": prompt_id,
                        "error": str(status.get("messages", "Unknown error")),
                        "elapsed": elapsed
                    }
            
            if not in_running and not in_pending:
                # 可能已完成或出错，再查一次
                history = self.get_history(prompt_id)
                if prompt_id in history:
                    return {
                        "success": True,
                        "prompt_id": prompt_id,
                        "outputs": history[prompt_id].get("outputs", {}),
                        "elapsed": elapsed
                    }
                # 真的找不到了
                return {
                    "success": False,
                    "prompt_id": prompt_id,
                    "error": "任务丢失，不在队列也不在历史中",
                    "elapsed": elapsed
                }
            
            time.sleep(poll_interval)
    
    def get_image(self, filename: str, subfolder: str = "", 
                  folder_type: str = "output") -> bytes:
        """从ComfyUI获取图片"""
        params = urllib.parse.urlencode({
            "filename": filename,
            "subfolder": subfolder,
            "type": folder_type
        })
        url = f"{self.base_url}/view?{params}"
        ctx = self.ssl_ctx if self.use_https else None
        req = urllib.request.Request(url)
        if self.username and self.password:
            import base64
            credentials = base64.b64encode(f"{self.username}:{self.password}".encode()).decode()
            req.add_header("Authorization", f"Basic {credentials}")
        with urllib.request.urlopen(req, context=ctx) as resp:
            return resp.read()
    
    def upload_image(self, image_path: str, overwrite: bool = True) -> Dict:
        """上传图片到ComfyUI"""
        import mimetypes
        
        filename = os.path.basename(image_path)
        mime_type = mimetypes.guess_type(image_path)[0] or "image/png"
        
        boundary = f"----WebKitFormBoundary{uuid.uuid4().hex[:16]}"
        
        with open(image_path, "rb") as f:
            image_data = f.read()
        
        body = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="image"; filename="{filename}"\r\n'
            f"Content-Type: {mime_type}\r\n\r\n"
        ).encode() + image_data + (
            f"\r\n--{boundary}\r\n"
            f'Content-Disposition: form-data; name="overwrite"\r\n\r\n'
            f"{str(overwrite).lower()}\r\n"
            f"--{boundary}--\r\n"
        ).encode()
        
        url = f"{self.base_url}/upload/image"
        req = urllib.request.Request(url, data=body, method="POST")
        req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
        if self.username and self.password:
            import base64
            credentials = base64.b64encode(f"{self.username}:{self.password}".encode()).decode()
            req.add_header("Authorization", f"Basic {credentials}")
        
        ctx = self.ssl_ctx if self.use_https else None
        with urllib.request.urlopen(req, timeout=60, context=ctx) as resp:
            return json.loads(resp.read().decode())
    
    def upload_mask(self, image_path: str, mask_path: str, 
                    overwrite: bool = True) -> Dict:
        """上传带遮罩的图片"""
        import mimetypes
        
        boundary = f"----WebKitFormBoundary{uuid.uuid4().hex[:16]}"
        
        with open(image_path, "rb") as f:
            image_data = f.read()
        with open(mask_path, "rb") as f:
            mask_data = f.read()
        
        image_name = os.path.basename(image_path)
        mask_name = os.path.basename(mask_path)
        
        body = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="image"; filename="{image_name}"\r\n'
            f"Content-Type: image/png\r\n\r\n"
        ).encode() + image_data + (
            f"\r\n--{boundary}\r\n"
            f'Content-Disposition: form-data; name="mask"; filename="{mask_name}"\r\n'
            f"Content-Type: image/png\r\n\r\n"
        ).encode() + mask_data + (
            f"\r\n--{boundary}\r\n"
            f'Content-Disposition: form-data; name="overwrite"\r\n\r\n'
            f"{str(overwrite).lower()}\r\n"
            f"--{boundary}--\r\n"
        ).encode()
        
        url = f"{self.base_url}/upload/mask"
        req = urllib.request.Request(url, data=body, method="POST")
        req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
        if self.username and self.password:
            import base64
            credentials = base64.b64encode(f"{self.username}:{self.password}".encode()).decode()
            req.add_header("Authorization", f"Basic {credentials}")
        
        ctx = self.ssl_ctx if self.use_https else None
        with urllib.request.urlopen(req, timeout=60, context=ctx) as resp:
            return json.loads(resp.read().decode())


class WorkflowBuilder:
    """工作流构建器 - 帮助动态构建ComfyUI工作流"""
    
    @staticmethod
    def load_workflow(path: str) -> Dict:
        """从JSON文件加载工作流"""
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    
    @staticmethod
    def save_workflow(workflow: Dict, path: str):
        """保存工作流到JSON文件"""
        with open(path, "w", encoding="utf-8") as f:
            json.dump(workflow, f, indent=2, ensure_ascii=False)
    
    @staticmethod
    def update_node_input(workflow: Dict, node_id: str, 
                         input_name: str, value: Any) -> Dict:
        """更新工作流中某个节点的输入值"""
        if node_id in workflow:
            if "inputs" not in workflow[node_id]:
                workflow[node_id]["inputs"] = {}
            workflow[node_id]["inputs"][input_name] = value
        return workflow
    
    @staticmethod
    def find_nodes_by_class(workflow: Dict, class_type: str) -> List[str]:
        """按节点类型查找节点ID"""
        return [
            node_id for node_id, node in workflow.items()
            if node.get("class_type") == class_type
        ]
    
    @staticmethod
    def set_text_input(workflow: Dict, node_id: str, text: str, 
                      input_name: str = "text") -> Dict:
        """设置文本输入"""
        return WorkflowBuilder.update_node_input(
            workflow, node_id, input_name, text
        )
    
    @staticmethod
    def set_model(workflow: Dict, node_id: str, model_name: str) -> Dict:
        """设置模型名称"""
        return WorkflowBuilder.update_node_input(
            workflow, node_id, "ckpt_name", model_name
        )
    
    @staticmethod
    def set_seed(workflow: Dict, node_id: str, seed: int) -> Dict:
        """设置随机种子"""
        return WorkflowBuilder.update_node_input(
            workflow, node_id, "seed", seed
        )
    
    @staticmethod
    def set_dimensions(workflow: Dict, node_id: str, 
                      width: int, height: int) -> Dict:
        """设置尺寸"""
        WorkflowBuilder.update_node_input(workflow, node_id, "width", width)
        WorkflowBuilder.update_node_input(workflow, node_id, "height", height)
        return workflow


if __name__ == "__main__":
    # 测试连接
    client = ComfyUIClient()
    result = client.test_connection()
    if result["success"]:
        print("ComfyUI连接成功!")
        stats = result["stats"]
        print(f"设备: {stats.get('devices', [])}")
    else:
        print(f"连接失败: {result['error']}")
