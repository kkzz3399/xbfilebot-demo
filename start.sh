#!/bin/bash

# ==================== xbFileBot 一键启动脚本 ====================

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVICE_NAME="xbfilebot"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
LOG_FILE="$BASE_DIR/xbfilebot.log"
PYTHON_BIN=$(which python3 || which python || echo "")

if [ -z "$PYTHON_BIN" ]; then
    echo -e "${RED}❌ 未找到 Python3，请先安装 Python3${NC}"
    exit 1
fi

echo -e "${BLUE}🔧 xbFileBot 一键启动脚本${NC}"
echo -e "${BLUE}📂 项目目录: $BASE_DIR${NC}"

# 统一给三个脚本加执行权限
chmod +x "$BASE_DIR/start.sh" "$BASE_DIR/stop.sh" "$BASE_DIR/status.sh" 2>/dev/null || true

# 确保日志文件存在
touch "$LOG_FILE"
chmod 644 "$LOG_FILE"

echo -e "${YELLOW}📦 创建/更新 systemd 服务...${NC}"

sudo tee "$SERVICE_FILE" > /dev/null <<EOF
[Unit]
Description=XBFileBot - 私人云盘机器人
After=network.target

[Service]
Type=simple
WorkingDirectory=$BASE_DIR
ExecStart=$PYTHON_BIN $BASE_DIR/main.py
Restart=always
RestartSec=5
StandardOutput=append:$LOG_FILE
StandardError=append:$LOG_FILE
User=root
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload

echo -e "${GREEN}✅ 服务配置已更新${NC}"

# 启用开机自启
sudo systemctl enable $SERVICE_NAME 2>/dev/null || echo -e "${YELLOW}⚠️  开机自启已启用${NC}"

# 重启服务
echo -e "${YELLOW}🚀 启动/重启机器人...${NC}"
sudo systemctl restart $SERVICE_NAME

sleep 3

if systemctl is-active --quiet $SERVICE_NAME; then
    echo -e "${GREEN}✅ 机器人启动成功！${NC}"
    echo -e "${GREEN}📄 日志文件: $LOG_FILE${NC}"
    echo -e "${GREEN}🛠️  管理命令:${NC}"
    echo "   查看状态: bash $BASE_DIR/status.sh"
    echo "   停止服务: bash $BASE_DIR/stop.sh"
    echo "   查看日志: tail -f $LOG_FILE"
else
    echo -e "${RED}❌ 启动失败！查看日志排查${NC}"
    echo -e "${RED}📄 日志: $LOG_FILE${NC}"
    tail -20 "$LOG_FILE" | sed 's/^/   /'
    exit 1
fi

echo -e "${BLUE}🎉 全部完成！机器人正在运行${NC}"