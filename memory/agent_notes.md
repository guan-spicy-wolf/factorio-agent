# Agent Memory

## 2026-03-30: 演进闭环实现

### 脚本管理工具
已实现以下工具，允许 Agent 扩展自身能力：

| 工具 | 功能 |
|------|------|
| `script_list()` | 列出所有可用脚本 |
| `script_read(name)` | 读取脚本源码 |
| `script_write(name, code)` | 写入新脚本 |
| `script_template(category, name)` | 获取脚本模板 |

### 演进流程
```
1. script_list()        → 了解当前能力
2. script_read("atomic.teleport") → 学习脚本模式
3. script_template()    → 获取模板
4. script_write()       → 创建新能力
5. 重启服务器            → 新脚本可用
```

### 限制
- Factorio 的 require 是静态的，新脚本需要服务器重启
- 暂无自动审核机制，依赖人工 review
- 暂无 git commit/PR 自动化

---

## 2026-03-30: 初始测试发现

### 地图感知问题
- 当前 `inspect()` 是局部搜索，缺乏完整地图意识
- Agent 每次只能看到当前位置周围的实体和资源
- 改进方向：实现地图扫描脚本，记录资源分布；或持久化已探索区域

### 移动机制
- 当前 `move()` 是瞬移（teleport），可能嵌入物品内部
- 目前不阻塞使用，但后续需要改进
- 改进方向：基于寻路的 walk 移动；或 teleport 前检查目标位置碰撞

### 物品获取
- **当前方案**: 使用 `give_item(name, count)` 直接添加物品到背包
- **原因**: Agent 的 character 没有 player 关联，无法使用 crafting
- **后期改进**: 可以添加合成限制（需要原料、解锁科技等）

## 已知限制

### Character vs Player
- Character 是物理实体（有位置、物品栏）
- Player 是游戏参与者（有 crafting queue、研发、地图视野）
- Agent 创建的 character 是"孤儿"（player: nil），无法 craft
- Crafting 是 Player 的能力，不是 Character 的能力