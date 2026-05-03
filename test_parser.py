"""
聊天解析器测试脚本
用法: python test_parser.py <jsonl文件路径>
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from scripts.chat_parser import ChatParser

def test_parse(jsonl_path):
    """测试解析JSONL文件"""
    parser = ChatParser()
    count = parser.load_jsonl(jsonl_path)
    print(f"加载了 {count} 条消息")
    
    scenes = parser.detect_scenes(mode="fixed", messages_per_scene=5)
    print(f"检测到 {len(scenes)} 个场景")
    
    for i, scene in enumerate(scenes):
        print(f"\n场景 {i+1}: {scene.title}")
        print(f"  消息数: {len(scene.messages)}")
        print(f"  角色: {scene.characters}")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python test_parser.py <jsonl文件路径>")
        print("示例: python test_parser.py ./chats/my_chat.jsonl")
        sys.exit(1)
    test_parse(sys.argv[1])
