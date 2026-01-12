#!/bin/bash

# ==================== xbFileBot 状态查看脚本 ====================

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVICE_NAME="xbfilebot"
LOG_FILE="$BASE_DIR/xbfilebot.log"

echo -e "${BLUE}📊 xbFileBot 状态查看${NC}"
echo -e "${BLUE}📂 项目目录: $BASE_DIR${NC}"

if systemctl is-active --quiet $SERVICE_NAME; then
    echo -e "${GREEN}✅ 机器人正在运行${NC}"
    echo
    echo -e "${YELLOW}📋 服务状态详情：${NC}"
    systemctl status $SERVICE_NAME --no-pager -l | head -20
    echo
    echo -e "${YELLOW}📄 最近日志（最后10行）：${NC}"
    if [ -f "$LOG_FILE" ]; then
        tail -10 "$LOG_FILE" | sed 's/^/   /'
    else
        echo "   无日志文件"
    fi
else
    echo -e "${RED}❌ 机器人未运行${NC}"
    echo -e "${YELLOW}可能原因：未启动 / 启动失败 / 被手动停止${NC}"
    echo
    echo -e "${YELLOW}📄 最近错误日志（如果存在）：${NC}"
    if [ -f "$LOG_FILE" ]; then
        echo "   $(tail -10 "$LOG_FILE" | grep -i error || echo '无错误记录')"
    fi
fi

echo -e "${BLUE}🎉 状态查看完成${NC}"