# 脚本热更新与审核机制设计

## 目标

让 Agent 写完脚本后立即可用，并为后续审核流程预留接口。

## 背景

当前 `script_write` 只是写入文件，新脚本需要重启 Factorio 服务器才能生效。这导致：
- 演化闭环断裂
- Agent 无法在长任务中即时利用新能力
- Token 消耗增加（需要等待重启、重新建立上下文）

## 方案

### 架构改造

```
script_write() → ReviewManager.submit() → 写入文件 → reload_script() → 立即可用
                       ↑
                 当前：直接通过
                 后续：GitHub PR + LLM 审核
```

### 三部分改动

#### 1. Mod 端：动态加载架构

改造 `control.lua`，从预加载改为缓存 + 动态加载：

```lua
local script_cache = {}

function load_script(name)
    if script_cache[name] then
        return script_cache[name]
    end
    
    local path = "scripts/" .. name:gsub("%.", "/") .. ".lua"
    local ok, func = pcall(loadfile, path)
    if ok and func then
        script_cache[name] = func()
        return script_cache[name]
    end
    return nil, "script not found: " .. name
end

function clear_script_cache(name)
    if name then
        script_cache[name] = nil
    else
        script_cache = {}
    end
end
```

新增 RCON 命令：
- `/agent reload <name>` - 重载指定脚本
- `/agent reload_all` - 清空全部缓存

#### 2. Python 端：审核管理器

新增 `agent/review.py`：

```python
class ReviewManager:
    """管理脚本审核流程。"""
    
    def submit(self, name: str, code: str, author: str = "agent") -> dict:
        """提交脚本变更。当前直接通过，后续可替换为 GitHub PR + LLM 审核。"""
        return {"status": "approved", "name": name, "reviewer": "auto-pass"}
    
    def check_status(self, name: str) -> dict:
        """检查审核状态。"""
        return {"status": "approved", "name": name}
```

#### 3. 工具层：script_write 改造

```python
def script_write(name: str, code: str, description: str = "") -> dict:
    review_manager = get_review_manager()
    result = review_manager.submit(name, code)
    
    if result["status"] == "approved":
        write_and_reload(name, code)
        return {"status": "written_and_active", "name": name}
    else:
        return {"status": "pending_review", "name": name, "pr_url": result.get("pr_url")}
```

新增可选工具：
- `script_reload(name=None)` - 显式重载脚本

### 后续扩展

审核流程可替换为：

```
本地修改 → push PR → LLM 审核 → 自动合并 → 通知 agent
```

扩展点：
- `GitHubReviewManager` - 创建 PR、查询状态
- LLM 审核独立 agent
- 规则引擎（禁止危险 API 等）

## 文件改动清单

| 文件 | 改动 |
|------|------|
| `mod/control.lua` | 添加脚本缓存、动态加载、reload 命令 |
| `agent/review.py` | 新增审核管理器（占位实现） |
| `agent/scripts.py` | 添加 `reload_script` 函数 |
| `agent/bridge.py` | 添加 `reload_script` 方法调用 RCON |
| `agent/run.py` | 修改 `script_write`、添加 `script_reload` 工具 |

## 验收标准

1. Agent 调用 `script_write("atomic.my_action", code)` 后，立即可以 `call_script("atomic.my_action", args)` 调用
2. 现有脚本不受影响
3. `script_reload` 工具可用
4. 审核接口预留，后续可无缝替换

## 不在范围

- `inspect` 优化
- LLM 审核实现
- GitHub 集成