# Factorio Agent 配置

## 服务器配置

| 文件 | 说明 |
|------|------|
| `config/server-settings.json` | Factorio 服务器设置 |
| `config/map-gen-settings.json` | 地图生成设置 |

### 关键配置项

**server-settings.json**:
```json
{
  "auto_pause": false,  // 必须 false，否则 RCON 无响应
  "visibility": {"public": false, "lan": true}
}
```

**map-gen-settings.json**:
```json
{
  "peaceful_mode": true,  // 无敌人
  "autoplace_controls": {
    "enemy-base": {"frequency": 0, "size": 0, "richness": 0}
  }
}
```

## 环境变量

创建 `.env` 文件：

```bash
# Factorio 服务器
FACTORIO_DIR=~/factorio          # Factorio 安装目录
RCON_HOST=127.0.0.1
RCON_PORT=27015
RCON_PASSWORD=changeme

# Agent
ANTHROPIC_API_KEY=sk-ant-...     # 必需
SAVE_NAME=agent                   # 存档名称
```

## 快速启动

```bash
# 1. 启动 Factorio 服务器
./bin/factorio-agent.sh start

# 2. 运行 agent
source .env
uv run python -m agent.run "在地图上放置一个储物箱"

# 3. 停止服务器
./bin/factorio-agent.sh stop
```