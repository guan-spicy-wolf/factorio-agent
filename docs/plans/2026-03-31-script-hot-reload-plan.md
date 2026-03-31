# 脚本热更新实现计划

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 让 Agent 写完脚本后立即可用，无需重启 Factorio 服务器。

**Architecture:** Mod 端改造为缓存 + 动态加载，Python 端添加审核管理器（占位实现），`script_write` 完成后自动触发重载。

**Tech Stack:** Lua (Factorio mod), Python 3.13, RCON

---

## Task 1: 创建审核管理器（占位实现）

**Files:**
- Create: `agent/review.py`
- Test: `tests/test_review.py`

**Step 1: Write the failing test**

```python
# tests/test_review.py
"""Tests for script review manager."""

import pytest
from agent.review import ReviewManager, get_review_manager


class TestReviewManager:
    def test_submit_returns_approved(self):
        """当前实现：submit 直接返回 approved。"""
        manager = ReviewManager()
        result = manager.submit("atomic.test", "return function() end")
        assert result["status"] == "approved"
        assert result["name"] == "atomic.test"
        assert result["reviewer"] == "auto-pass"

    def test_check_status_returns_approved(self):
        """当前实现：check_status 始终返回 approved。"""
        manager = ReviewManager()
        result = manager.check_status("atomic.test")
        assert result["status"] == "approved"

    def test_get_review_manager_returns_singleton(self):
        """get_review_manager 返回全局实例。"""
        m1 = get_review_manager()
        m2 = get_review_manager()
        assert m1 is m2
```

**Step 2: Run test to verify it fails**

Run: `cd /home/holo/factorio-agent && uv run pytest tests/test_review.py -v`
Expected: FAIL with "No module named 'agent.review'"

**Step 3: Write minimal implementation**

```python
# agent/review.py
"""Script review manager.

Current implementation: auto-approve all scripts.
Future: integrate with GitHub PR + LLM review.
"""

from typing import Optional


class ReviewManager:
    """Manages script review workflow.
    
    Current: auto-approve everything.
    Future: create PR, trigger LLM review, merge on approval.
    """

    def submit(self, name: str, code: str, author: str = "agent") -> dict:
        """Submit a script for review.
        
        Args:
            name: Script name (e.g. "atomic.my_action")
            code: Lua source code
            author: Who submitted (default: "agent")
        
        Returns:
            Current: {"status": "approved", ...}
            Future: {"status": "pending_review", "pr_url": ...}
        """
        # TODO: Future implementation
        # - Create git branch
        # - Write file
        # - Create PR
        # - Trigger LLM review
        return {
            "status": "approved",
            "name": name,
            "reviewer": "auto-pass",
            "author": author,
        }

    def check_status(self, name: str) -> dict:
        """Check review status for a script.
        
        Returns:
            Current: always approved
            Future: query PR status
        """
        # TODO: Query PR status
        return {
            "status": "approved",
            "name": name,
        }


# Global instance
_review_manager: Optional[ReviewManager] = None


def get_review_manager() -> ReviewManager:
    """Get the global review manager instance."""
    global _review_manager
    if _review_manager is None:
        _review_manager = ReviewManager()
    return _review_manager
```

**Step 4: Run test to verify it passes**

Run: `cd /home/holo/factorio-agent && uv run pytest tests/test_review.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add agent/review.py tests/test_review.py
git commit -m "feat: add ReviewManager placeholder for script approval"
```

---

## Task 2: Mod 端添加脚本缓存和重载命令

**Files:**
- Modify: `mod/control.lua`

**Step 1: Write reload command handler**

在 `control.lua` 中添加：
- 脚本缓存表
- `load_script(name)` 函数：缓存未命中时 `loadfile()` 加载
- `/agent reload <name>` 命令
- `/agent reload_all` 命令

```lua
-- mod/control.lua 改动点

-- 在文件顶部添加脚本缓存（约第10行后）
-- Script cache for dynamic loading
local script_cache = {}

-- Helper: load script with caching
local function load_script(name)
    -- Check cache first
    if script_cache[name] then
        return script_cache[name]
    end
    
    -- Parse name: atomic.teleport -> scripts/atomic/teleport.lua
    local category, script_name = name:match("^(%w+)%.(%w+)$")
    if not category or not script_name then
        return nil, "invalid script name format: " .. name
    end
    
    local path = "scripts." .. category .. "." .. script_name
    local ok, mod = pcall(require, path)
    if ok and mod then
        script_cache[name] = mod
        return mod
    end
    
    -- Try loadfile for newly written scripts
    local file_path = "scripts/" .. category .. "/" .. script_name .. ".lua"
    ok, mod = pcall(loadfile, file_path)
    if ok and mod then
        local result = mod()
        script_cache[name] = result
        return result
    end
    
    return nil, "script not found: " .. name
end

-- Helper: clear script cache
local function reload_script(name)
    if name then
        script_cache[name] = nil
        -- Also clear package.loaded for require
        local path = "scripts." .. name:gsub("%.", "/")
        package.loaded[path] = nil
    else
        -- Clear all
        script_cache = {}
        for k, _ in pairs(package.loaded) do
            if k:match("^scripts%.") then
                package.loaded[k] = nil
            end
        end
    end
    return {ok = true, reloaded = name or "all"}
end
```

**Step 2: Modify command handler to support reload**

在 `commands.add_command` 的处理逻辑中添加 reload 支持：

```lua
-- 在 commands.add_command 处理函数中，param 解析后添加：

-- Handle reload commands
if script_name == "reload" then
    local target = args_str:match("^%s*(%S+)")
    local result = reload_script(target)
    rcon.print(serialize(result))
    return
end

if script_name == "reload_all" then
    local result = reload_script(nil)
    rcon.print(serialize(result))
    return
end

-- 在执行脚本前，改用 load_script：
local script_fn = load_script(script_name)
if not script_fn then
    -- 尝试从 scripts 表获取（兼容旧代码）
    script_fn = scripts[script_name]
end
```

**Step 3: Verify syntax**

Run: `lua -e "dofile('mod/control.lua')"` or manual check
（Factorio mod 需要在游戏内测试，这里只检查语法）

**Step 4: Commit**

```bash
git add mod/control.lua
git commit -m "feat(mod): add script cache and reload commands"
```

---

## Task 3: Python 端添加 reload_script 方法

**Files:**
- Modify: `agent/bridge.py`
- Test: `tests/test_bridge.py`

**Step 1: Write the failing test**

```python
# tests/test_bridge.py 添加

class TestReloadScript:
    def test_reload_script_calls_rcon(self, mock_rcon):
        """reload_script 发送正确的 RCON 命令。"""
        mock_rcon.send_command.return_value = '{"ok": true, "reloaded": "atomic.test"}'
        
        bridge = FactorioBridge(mock_rcon)
        result = bridge.reload_script("atomic.test")
        
        mock_rcon.send_command.assert_called_once_with("/agent reload atomic.test")
        assert result["ok"] is True
        assert result["reloaded"] == "atomic.test"

    def test_reload_all_calls_rcon(self, mock_rcon):
        """reload_all 发送 reload_all 命令。"""
        mock_rcon.send_command.return_value = '{"ok": true, "reloaded": "all"}'
        
        bridge = FactorioBridge(mock_rcon)
        result = bridge.reload_all()
        
        mock_rcon.send_command.assert_called_once_with("/agent reload_all")
        assert result["reloaded"] == "all"
```

**Step 2: Run test to verify it fails**

Run: `cd /home/holo/factorio-agent && uv run pytest tests/test_bridge.py::TestReloadScript -v`
Expected: FAIL with "AttributeError: 'FactorioBridge' object has no attribute 'reload_script'"

**Step 3: Write minimal implementation**

```python
# agent/bridge.py 在 FactorioBridge 类中添加

    def reload_script(self, name: str) -> dict:
        """Reload a specific script to pick up changes.
        
        Args:
            name: Script name (e.g. "atomic.my_action")
        
        Returns:
            {"ok": true, "reloaded": name}
        """
        return self.call_script("reload", name)

    def reload_all(self) -> dict:
        """Reload all scripts.
        
        Clears the script cache, next call will load fresh.
        
        Returns:
            {"ok": true, "reloaded": "all"}
        """
        return self.call_script("reload_all")
```

注意：`call_script("reload", name)` 会发送 `/agent reload atomic.test`，正好是我们需要的格式。

**Step 4: Run test to verify it passes**

Run: `cd /home/holo/factorio-agent && uv run pytest tests/test_bridge.py::TestReloadScript -v`
Expected: PASS

**Step 5: Commit**

```bash
git add agent/bridge.py tests/test_bridge.py
git commit -m "feat(bridge): add reload_script and reload_all methods"
```

---

## Task 4: 修改 script_write 自动重载

**Files:**
- Modify: `agent/scripts.py`
- Test: `tests/test_scripts.py` (新建)

**Step 1: Write the failing test**

```python
# tests/test_scripts.py
"""Tests for script management."""

import pytest
from pathlib import Path
from agent.scripts import write_script, list_scripts, read_script


class TestWriteScriptWithReload:
    def test_write_script_returns_activation_immediate(self, tmp_path, monkeypatch):
        """write_script 应该返回 activation: immediate 而不是 restart_required。"""
        # 模拟 MOD_SCRIPTS_DIR
        monkeypatch.setattr("agent.scripts.MOD_SCRIPTS_DIR", tmp_path)
        
        code = "return function() return 'test' end"
        result = write_script("atomic.test_reload", code)
        
        assert result.get("activation") != "restart_required"
        # 新实现应该返回 "immediate" 或其他表示立即可用的状态
```

**Step 2: Run test to verify it fails**

Run: `cd /home/holo/factorio-agent && uv run pytest tests/test_scripts.py -v`
Expected: FAIL (activation 仍然是 "restart_required")

**Step 3: Modify write_script signature to accept optional reload callback**

```python
# agent/scripts.py 修改 write_script 函数

from typing import Callable, Optional

# 添加 reload_callback 参数
def write_script(
    name: str, 
    code: str, 
    description: str = "",
    reload_callback: Optional[Callable[[str], dict]] = None,
) -> dict:
    """Write a new Lua script or update an existing one.

    Args:
        name: Script name (e.g. "atomic.new_action", "actions.my_workflow")
        code: Lua source code
        description: Optional description for commit message
        reload_callback: Optional callback to reload script after write.
                         Called as reload_callback(name) -> dict.
                         If provided, script becomes immediately usable.

    Returns:
        {"created": true, "path": "...", "activation": "immediate"} 
        or {"activation": "restart_required"} if no reload_callback.
    """
    # Parse category.script format
    parts = name.split(".")
    if len(parts) != 2:
        return {"error": f"invalid script name format: {name}. Use 'category.script' like 'atomic.teleport'"}

    category, script_name = parts

    # Validate category
    valid_categories = ["atomic", "actions", "examples", "lib"]
    if category not in valid_categories:
        return {"error": f"invalid category: {category}. Must be one of: {valid_categories}"}

    # Validate script name (alphanumeric and underscore only)
    if not script_name.replace("_", "").isalnum():
        return {"error": f"invalid script name: {script_name}. Use only letters, numbers, and underscores"}

    # Create directory if needed
    script_dir = MOD_SCRIPTS_DIR / category
    script_dir.mkdir(parents=True, exist_ok=True)

    script_path = script_dir / f"{script_name}.lua"

    # Check if this is a new script or update
    is_new = not script_path.exists()

    try:
        script_path.write_text(code, encoding="utf-8")
    except Exception as e:
        return {"error": f"failed to write script: {e}"}

    # Determine activation status
    activation = "restart_required"
    if reload_callback:
        try:
            reload_result = reload_callback(name)
            if reload_result.get("ok") or reload_result.get("reloaded"):
                activation = "immediate"
        except Exception as e:
            activation = "reload_failed"

    return {
        "created": is_new,
        "updated": not is_new,
        "name": name,
        "path": str(script_path.relative_to(PROJECT_ROOT)),
        "description": description,
        "lines": len(code.splitlines()),
        "activation": activation,
    }
```

**Step 4: Run test to verify it passes (需要更新测试)**

更新测试以传入 reload_callback：

```python
# tests/test_scripts.py 更新
class TestWriteScriptWithReload:
    def test_write_script_with_reload_callback(self, tmp_path, monkeypatch):
        """write_script 有 reload_callback 时返回 activation: immediate。"""
        monkeypatch.setattr("agent.scripts.MOD_SCRIPTS_DIR", tmp_path)
        
        code = "return function() return 'test' end"
        
        # 模拟成功的 reload
        def mock_reload(name):
            return {"ok": True, "reloaded": name}
        
        result = write_script("atomic.test_reload", code, reload_callback=mock_reload)
        
        assert result["activation"] == "immediate"
        assert result["created"] is True

    def test_write_script_without_reload_returns_restart_required(self, tmp_path, monkeypatch):
        """write_script 没有 reload_callback 时返回 restart_required。"""
        monkeypatch.setattr("agent.scripts.MOD_SCRIPTS_DIR", tmp_path)
        
        code = "return function() return 'test' end"
        result = write_script("atomic.test_no_reload", code)
        
        assert result["activation"] == "restart_required"
```

**Step 5: Commit**

```bash
git add agent/scripts.py tests/test_scripts.py
git commit -m "feat(scripts): add reload_callback to write_script"
```

---

## Task 5: 集成 ReviewManager 到 run.py

**Files:**
- Modify: `agent/run.py`

**Step 1: Import ReviewManager and wire up script_write**

```python
# agent/run.py 添加 import
from agent.review import get_review_manager

# 修改 build_tools 函数中的 script_write 定义
# 找到现有的 script_write 工具定义，替换为：

    # Script management tools (for evolution)
    review_manager = get_review_manager()

    @tool
    def script_write(name: str, code: str, description: str = "") -> dict:
        """Write a new Lua script or update an existing one.

        The script will be saved and immediately activated (no restart needed).
        Use script_read to learn existing patterns and script_template for starters.

        Args:
            name: Script name in format 'category.name' (e.g. 'atomic.my_action', 'actions.my_workflow')
            code: Lua source code
            description: Optional description for the script

        Returns:
            {"status": "written_and_active", ...} if successful
        """
        # Submit for review (current: auto-approve)
        review_result = review_manager.submit(name, code)
        
        if review_result["status"] != "approved":
            return {
                "status": "pending_review",
                "name": name,
                "pr_url": review_result.get("pr_url"),
            }
        
        # Write with reload
        def reload_callback(script_name: str) -> dict:
            if bridge is None:
                return {"ok": False, "error": "no server connection"}
            return bridge.reload_script(script_name)
        
        from agent.scripts import write_script as _write_script
        return _write_script(name, code, description, reload_callback=reload_callback)
```

**Step 2: Add script_reload tool**

```python
# agent/run.py 在 build_tools 中添加

    @tool
    def script_reload(name: str = None) -> dict:
        """Reload a script or all scripts.
        
        Usually called automatically after script_write.
        Use this if you suspect the script cache is stale.
        
        Args:
            name: Script name to reload, or None to reload all scripts.
        
        Returns:
            {"ok": true, "reloaded": name_or_all}
        """
        if bridge is None:
            return {"error": "no server connection"}
        
        if name:
            return bridge.reload_script(name)
        else:
            return bridge.reload_all()
```

**Step 3: Register the new tool**

在 `registry.register(script_reload)` 添加注册。

**Step 4: Commit**

```bash
git add agent/run.py
git commit -m "feat(run): integrate ReviewManager and add script_reload tool"
```

---

## Task 6: 端到端测试

**Files:**
- Test: 手动测试

**Step 1: 启动 Factorio 服务器**

Run: `cd /home/holo/factorio-agent && ./bin/start-server.sh`

**Step 2: 运行 agent 测试脚本写入和立即调用**

Run: `cd /home/holo/factorio-agent && uv run python -m agent.run "写一个脚本 atomic.hello 打印 Hello World，然后调用它验证" --verbose`

**Step 3: 验证新脚本立即可用**

在 agent session 中观察：
1. `script_write("atomic.hello", ...)` 返回 `activation: immediate`
2. 紧接着 `call_script("atomic.hello")` 成功执行

**Step 4: 手动验证 reload 命令**

通过 RCON 直接测试：
```
/agent reload atomic.hello
/agent atomic.hello
```

---

## Task 7: 更新文档和清理

**Files:**
- Modify: `README.md`
- Modify: `TODO.md`
- Modify: `memory/agent_notes.md`

**Step 1: Update README**

添加热更新说明到 README。

**Step 2: Update TODO.md**

标记相关 TODO 为完成。

**Step 3: Update agent_notes.md**

记录热更新机制的使用方法。

**Step 4: Final commit**

```bash
git add README.md TODO.md memory/agent_notes.md
git commit -m "docs: update documentation for script hot-reload feature"
```

---

## 验收清单

- [ ] `script_write()` 后立即可以 `call_script()` 调用新脚本
- [ ] `script_reload()` 工具可用于显式重载
- [ ] 审核流程接口预留（ReviewManager）
- [ ] 现有脚本不受影响
- [ ] 测试通过