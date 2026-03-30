# Factorio Agent

一个能在 Factorio 中自主完成任务并持续改进自身工具集的 AI agent。

## 愿景

Agent 通过标准 git 工作流演化自己的工具集：阅读 API 文档、编写 Lua 脚本、提交 PR、审核通过后使用新工具完成更复杂的任务。每一轮任务的产出不仅是游戏内的工厂，也是更强的工具库。

## 核心设计

### 架构

```
Agent (Python, LLM loop)
    │
    ├─ call_script(name, args)  ── RCON ──→  Factorio Mod ──→ scripts/{name}.lua
    ├─ git clone / branch / commit / PR      ──→  Scripts Repo
    └─ api_search / api_detail               ──→  API 文档索引
```

### 三个要素

**1. Factorio Mod 作为执行环境**

一个 Factorio mod，内部包含所有已审核的 Lua 脚本。脚本之间可以 `require`。
Mod 通过 RCON 暴露唯一入口 `/agent <script_name> <args>`。

```
factorio-agent-mod/
├── control.lua           # RCON 命令注册 + 脚本调度
└── scripts/              # 已审核脚本，从 git repo 同步
    ├── lib/              # 公共库（区域计算、序列化等）
    ├── inspect.lua       # 查看区域实体
    ├── place.lua         # 放置实体
    ├── remove.lua        # 拆除实体
    └── ...               # agent 后续产出的工具
```

**2. Git Repo 作为演化载体**

脚本源码存放在 git repo 中。Agent 通过标准 git 操作演化工具集：
- 读取现有脚本了解能力边界
- 查阅 API 文档学习新接口
- 编写新脚本或修改现有脚本
- 提交 PR，审核通过后合并
- 合并后同步到 mod 目录，agent 即可调用

审核门控 = PR merge。未合并的代码不会进入 mod，agent 无法执行。

**3. API 文档索引**

将 Factorio Lua API 官方文档预处理为结构化索引，提供两个查询接口：
- `api_search(keyword)` → 返回匹配的类/方法/属性列表
- `api_detail(name)` → 返回完整签名、参数、返回值、描述

Agent 通过查阅文档来学习如何编写新脚本。

### Agent 可用工具

| 工具 | 用途 |
|---|---|
| `call_script(name, args)` | 通过 RCON 执行 mod 中的已审核脚本 |
| `api_search(query)` | 搜索 Factorio API 文档 |
| `api_detail(name)` | 获取 API 对象/方法的详细信息 |
| git 操作 | 读取 repo、创建分支、提交代码、发起 PR |

没有 `execute_lua`。所有 Lua 执行都通过 `call_script` 走 mod 内的已审核脚本。

### 行动预算

不设硬限制。行动次数作为 task 级优化指标：

```yaml
task_result:
  goal: "自动化红瓶生产"
  production: {red_science_per_min: 45}
  efficiency: {total_script_calls: 87, ticks_elapsed: 36000}
  cost: {token_cost_usd: 0.15}
  scripts_contributed: ["scan_iron_ore.lua", "belt_layout.lua"]
```

跨版本对比这些指标，就是可量化的演化证据。

### 审核机制

初期人工审核。后续可引入自动化：
- 在快照存档上试运行脚本
- 检查执行前后游戏状态差异
- 纯读脚本自动通过，含写操作的脚本分类标记

## MVP

**目标**：Agent 完成两次相同任务，第二次通过复用第一次产出的脚本表现更好。

**MVP 场景**：在空地图上建立自动采铁系统（采矿机 → 传送带 → 熔炉 → 箱子）。

**MVP 包含**：
- Factorio headless server 运行
- 最小 mod（control.lua + 种子脚本）
- RCON 客户端（Python）
- Agent 主循环（LLM → tool call → 执行 → 循环）
- 种子脚本：inspect、place、remove、advance_time
- API 文档索引 + 搜索
- Git repo 存放脚本，手动审核 PR

**MVP 不包含**：
- 自动化审核
- 多 agent 并行
- 治理层 / scorecard
- 容器化部署

## TODO

### 第一步：Factorio 基础连通

- [ ] Factorio headless server 安装和启动脚本
- [ ] 最小 mod 骨架：control.lua 注册 `/agent` RCON 命令
- [ ] Python RCON 客户端：连接、发送命令、解析返回
- [ ] 验证：通过 Python → RCON → mod → Lua 链路执行一个查询并拿到结果

### 第二步：种子脚本

- [ ] `scripts/inspect.lua` — 查询指定区域的实体和资源
- [ ] `scripts/place.lua` — 在指定位置放置实体
- [ ] `scripts/remove.lua` — 移除实体
- [ ] `scripts/advance.lua` — 推进游戏时间
- [ ] `scripts/lib/serialize.lua` — 游戏对象到 JSON 的序列化
- [ ] 验证：通过 call_script 放置一个采矿机并查询确认

### 第三步：API 文档索引

- [ ] 下载 Factorio Lua API 文档
- [ ] 解析为结构化 JSON（类 → 属性/方法 → 签名/描述）
- [ ] 实现关键词搜索和详情查询
- [ ] 验证：搜索 "fluid" 能返回相关 API

### 第四步：Agent 主循环

- [ ] 最朴素的 agent loop：system prompt → LLM → tool call → 执行 → 循环
- [ ] 工具注册：call_script、api_search、api_detail、git 操作
- [ ] 任务输入格式：目标描述 + 成功指标
- [ ] 指标收集：操作次数、tick 数、token 消耗
- [ ] 验证：agent 自主完成"放置一个采矿机对准铁矿"

### 第五步：演化闭环

- [ ] Agent 能读取 scripts repo 了解现有工具
- [ ] Agent 能创建分支、写新脚本、提 PR
- [ ] 人工审核 PR，合并后同步到 mod 目录
- [ ] 第二次运行同一任务，agent 使用新工具
- [ ] 对比两次运行的指标

### 待确认技术问题

- [ ] Factorio mod 热更新：修改文件后 `require` 缓存如何清除，是否需要重载
- [ ] RCON 返回值大小限制：大范围 inspect 的结果是否会被截断
- [ ] mod 内 `require` 的路径解析规则确认