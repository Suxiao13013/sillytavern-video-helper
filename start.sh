#!/bin/bash
# 视频助手启动脚本

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo "=============================================="
echo "  🎬 SillyTavern 视频助手"
echo "=============================================="

# 检查Python
if ! command -v python3 &> /dev/null; then
    echo -e "${RED}错误: 未找到python3${NC}"
    exit 1
fi

# 检查FFmpeg
if ! command -v ffmpeg &> /dev/null; then
    echo -e "${YELLOW}警告: 未找到ffmpeg，视频后处理功能将不可用${NC}"
fi

# 检查配置文件
if [ ! -f "config.json" ]; then
    echo -e "${YELLOW}警告: 未找到config.json，使用默认配置${NC}"
fi

# 默认参数
PORT=${1:-5000}
HOST="0.0.0.0"
CONFIG="config.json"

# 解析参数
while [[ $# -gt 0 ]]; do
    case $1 in
        --port)
            PORT="$2"
            shift 2
            ;;
        --host)
            HOST="$2"
            shift 2
            ;;
        --config)
            CONFIG="$2"
            shift 2
            ;;
        *)
            shift
            ;;
    esac
done

echo ""
echo "  端口: $PORT"
echo "  地址: $HOST"
echo "  配置: $CONFIG"
echo ""
echo "  API端点:"
echo "    GET  http://localhost:$PORT/health"
echo "    POST http://localhost:$PORT/api/analyze"
echo "    POST http://localhost:$PORT/api/generate"
echo "    GET  http://localhost:$PORT/api/progress/:id"
echo ""
echo "=============================================="
echo ""

# 启动服务器
python3 middleware_server.py --port "$PORT" --host "$HOST" --config "$CONFIG"
