# Factorio Agent

一个能在 Factorio 中自主完成任务并持续改进自身工具集的 AI agent。

## 快速开始

```bash
# 1. 安装依赖
uv sync

# 2. 启动 Factorio 服务器
./bin/factorio-agent.sh start

# 3. 运行 agent（需要 ANTHROPIC_API_KEY）
ANTHROPIC_API_KEY=your_key uv run python -m agent.run "在地图上放置一个储物箱"

# 4. 停止服务器
./bin/factorio-agent.sh stop
```

## 项目结构

```
factorio-agent/
├── agent/                    # Python agent 代码
│   ├── loop.py              # 主循环
│   ├── bridge.py            # Lua 脚本调用接口
│   ├── api_docs.py          # API 文档索引
│   └── run.py               # 入口
│
├── mod/                      # Factorio mod
│   ├── control.lua          # RCON 命令注册
│   └── scripts/             # Lua 脚本
│       ├── atomic/          # 原子操作（单个 API）
│       ├── actions/         # 动作脚本（封装流程）
│       ├── examples/        # 示例脚本
│       └── lib/             # 工具库
│
├── bin/                      # 管理脚本
│   ├── factorio-agent.sh    # 服务器管理
│   └── install-service.sh   # 安装 systemd 服务
│
└── files/                    # API 文档数据
    ├── prototype-api.json
    └── runtime-api.json
```

## 脚本层次

### 原子操作 (`atomic/`)

最底层的 API 调用，单个操作：

| 脚本 | 功能 |
|------|------|
| `teleport` | 瞬移到位置 |
| `inventory_get` | 获取 inventory |
| `inventory_add/remove` | 添加/移除物品 |
| `cursor_set/clear/get` | 操作手持物品 |
| `build_from_cursor` | 从 cursor 放置实体 |
| `mine_entity` | 采矿实体 |
| `can_reach/can_place` | 检查可达性/可放置 |

### 动作脚本 (`actions/`)

封装常用流程：

| 脚本 | 流程 |
|------|------|
| `spawn` | 创建人物 + 初始物品 |
| `move` | 移动到位置 |
| `place` | 检查 inventory → 移动 → 放置 |
| `remove` | 移动 → 采矿 → 物品返还 |
| `inspect` | 查询区域实体/资源 |

### 示例脚本 (`examples/`)

完整的任务示例，供 agent 学习：

| 脚本 | 功能 |
|------|------|
| `build_belt_line` | 建造传送带线 |
| `setup_mining` | 设置采矿站（钻机 + 箱子） |

## 使用方式

### 通过 Python 调用

```python
from agent.bridge import FactorioBridge
from agent.rcon import RCONClient

rcon = RCONClient(port=27015, password="changeme")
rcon.connect()
bridge = FactorioBridge(rcon)

# Spawn character with items
bridge.spawn({"iron-chest": 20, "electric-mining-drill": 5})

# Place entity (real flow: inventory → cursor → build)
bridge.place("iron-chest", 10, 10)

# Remove entity (real flow: mine → inventory)
bridge.remove(10, 10)

rcon.close()
```

### 直接调用 Lua 脚本

```
/agent spawn {"items": {"iron-chest": 20}}
/agent place {"name": "iron-chest", "x": 10, "y": 10}
/agent atomic.mine_entity {"x": 10, "y": 10}
```

## 真实玩家流程

Factorio 中，**Character** 实体继承 **LuaControl**，拥有真实玩家的操作能力：

| 操作 | API |
|------|-----|
| 采矿 | `character.mine_entity(entity)` ✓ |
| 放置 | `character.cursor_stack` + `surface.create_entity()` |
| 移动 | `character.teleport(position)` |
| Inventory | `character.get_inventory()` |

Headless 服务器没有连接的 Player，但 Character 可以独立存在并操作。

## 服务器管理

```bash
# 查看状态
./bin/factorio-agent.sh status

# 启动/停止/重启
./bin/factorio-agent.sh start
./bin/factorio-agent.sh stop
./bin/factorio-agent.sh restart

# 创建新地图
./bin/factorio-agent.sh fresh

# 查看日志
./bin/factorio-agent.sh logs

# 安装为系统服务
./bin/factorio-agent.sh install --user  # 用户服务
./bin/factorio-agent.sh install         # 系统服务（需 sudo）
```

### Systemd 服务

安装后：

```bash
# 用户服务
systemctl --user start factorio-agent
systemctl --user enable factorio-agent  # 开机自启

# 系统服务
sudo systemctl start factorio-agent
sudo systemctl enable factorio-agent
```

## 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `FACTORIO_DIR` | `~/factorio` | Factorio 安装目录 |
| `RCON_PORT` | `27015` | RCON 端口 |
| `RCON_PASSWORD` | `changeme` | RCON 密码 |
| `SAVE_NAME` | `agent` | 存档名称 |
| `ANTHROPIC_API_KEY` | - | LLM API 密钥 |

## 测试

```bash
# 单元测试
uv run pytest tests/ -v

# 端到端测试（需要服务器运行）
uv run python scripts/e2e_test.py
```

## 演化愿景

Agent 的目标是演化自己的工具集：

1. **MVP**: 使用种子脚本完成任务
2. **演化**: 通过 API 文档学习，编写新脚本
3. **提交**: 新脚本通过 PR 审核
4. **复用**: 审核通过后，新脚本加入工具库

```
任务: "建造自动化采矿系统"
      ↓
Agent 查阅 API → 编写 scripts/auto_mining.lua → 提交 PR
      ↓
下次任务直接使用新脚本，效率更高
```

## License

MIT