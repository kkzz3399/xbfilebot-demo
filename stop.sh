#!/bin/bash

# ==================== xbFileBot 停止脚本 ====================

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVICE_NAME="xbfilebot"

echo -e "${BLUE}🛑 xbFileBot 停止脚本${NC}"
echo -e "${BLUE}📂 项目目录: $BASE_DIR${NC}"

echo -e "${YELLOW}🛑 正在停止机器人服务...${NC}"

sudo systemctl stop $SERVICE_NAME

sleep 2

if systemctl is-active --quiet $SERVICE_NAME; then
    echo -e "${RED}❌ 停止失败！服务仍在运行${NC}"
    echo -e "${YELLOW}尝试强制终止进程...${NC}"
    sudo systemctl kill $SERVICE_NAME
    sleep 2
    if systemctl is-active --quiet $SERVICE_NAME; then
        echo -e "${RED}❌ 强制停止也失败，请手动检查${NC}"
    else
        echo -e "${GREEN}✅ 通过强制终止成功停止${NC}"
    fi
else
    echo -e "${GREEN}✅ 机器人已成功停止${NC}"
fi

echo -e "${BLUE}🎉 停止操作完成${NC}"