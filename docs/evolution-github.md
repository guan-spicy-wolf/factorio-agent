# 演进闭环：GitHub 集成方案

## 问题

当前本地实现的局限：
- 脚本写入本地文件，无版本控制
- 无审核机制，脚本直接生效
- 无通知，用户不知道 agent 做了什么
- 无法多机器同步

## 解决方案

使用 GitHub 作为中转和审核平台：

```
┌──────────────────────────────────────────────────────────────────┐
│                      Agent 演进闭环 (GitHub)                      │
├──────────────────────────────────────────────────────────────────┤
│                                                                  │
│  1. Agent 发现需要新能力                                         │
│        ↓                                                         │
│  2. script_create_pr(name, code, reason)                         │
│        → 创建分支、commit、push、创建 PR                          │
│        ↓                                                         │
│  3. GitHub 通知用户 (email/web)                                  │
│        ↓                                                         │
│  4. 用户 Review PR                                               │
│        → Approve → Merge                                         │
│        → Request Changes → Agent 修改                            │
│        ↓                                                         │
│  5. Merge 触发 webhook / 定时 pull                               │
│        → 同步到 mod/scripts/                                     │
│        → 重启服务器                                              │
│        ↓                                                         │
│  6. 新脚本可用！                                                 │
│                                                                  │
└──────────────────────────────────────────────────────────────────┘
```

## 实现步骤

### 第一阶段：GitHub 基础

1. **创建 GitHub Repo**
   - 存放 `scripts/` 目录
   - `scripts/atomic/*.lua`
   - `scripts/actions/*.lua`
   - `scripts/examples/*.lua`

2. **GitHub Token 配置**
   ```
   GITHUB_TOKEN=ghp_xxx  # Personal Access Token
   GITHUB_REPO=owner/factorio-agent-scripts
   ```

3. **PR 创建工具**
   ```python
   def script_create_pr(name: str, code: str, reason: str) -> dict:
       # 1. Create branch: agent/atomic-say-hello
       # 2. Write file: scripts/atomic/say_hello.lua
       # 3. Commit: "feat: add atomic.say_hello - <reason>"
       # 4. Push branch
       # 5. Create PR with description
   ```

### 第二阶段：审核与合并

4. **用户在 GitHub Review**
   - 查看 diff
   - Approve / Request Changes
   - Merge

5. **同步机制**
   - 方案 A：GitHub webhook → 本地服务接收 → 拉取更新
   - 方案 B：定时 pull（每分钟检查）
   - 方案 C：手动 `git pull` + 重启

### 第三阶段：自动化

6. **自动测试**（可选）
   - PR 创建时运行语法检查
   - 简单的 Lua lint

7. **自动重启**
   - 检测到新 commit 后自动重启服务器

## API 设计

```python
# agent/github_scripts.py

def script_list() -> dict:
    """列出所有脚本（从本地 mod/scripts 或 GitHub API）"""

def script_read(name: str) -> dict:
    """读取脚本源码"""

def script_create_pr(name: str, code: str, reason: str) -> dict:
    """创建 PR 添加新脚本

    Returns:
        {
            "pr_url": "https://github.com/owner/repo/pull/42",
            "branch": "agent/atomic-say-hello",
            "status": "pending_review"
        }
    """

def script_list_prs() -> list:
    """列出待处理的 PR"""

def script_check_merged() -> list:
    """检查已合并的 PR，返回需要同步的脚本"""
```

## 配置示例

```bash
# .env
GITHUB_TOKEN=ghp_xxxxxxxxxxxx
GITHUB_REPO=guan-spicy-wolf/factorio-agent-scripts
GITHUB_BRANCH_MAIN=main
```

## 目录结构

```
factorio-agent/
├── mod/
│   └── scripts/        # 符号链接或同步自 git repo
│       ├── atomic/
│       ├── actions/
│       └── examples/
├── agent/
│   └── github_scripts.py
└── .env
```

或者：

```
# 独立 repo
factorio-agent-scripts/
├── atomic/
│   ├── teleport.lua
│   └── say_hello.lua
├── actions/
└── examples/

# 主项目
factorio-agent/
├── mod/scripts -> ../factorio-agent-scripts/  # symlink
└── ...
```

## 待确认

1. **Repo 组织方式**
   - A) 脚本在主 repo 的 `scripts/` 子目录
   - B) 独立的 scripts repo，mod 通过 symlink 链接

2. **同步方式**
   - A) Webhook（需要公网 IP 或代理）
   - B) 定时 pull（简单，延迟 1 分钟）
   - C) 手动同步

3. **PR 粒度**
   - 一个脚本一个 PR
   - 一批相关脚本一个 PR

---

**建议**：先用方案 B（定时 pull）+ 独立 scripts repo，简单可行。