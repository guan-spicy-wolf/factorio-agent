# 命令与脚本地图

`fgen` 的子命令、它们写哪张表、要不要服务器、大致时长，以及阶段 9 之后每张表的去向。时长按 4 核容器、机器安静时；与别的求解或实测同时跑会翻倍（墙钟），游戏内按 tick 的实测不受影响。入口都在 `src/fgen/cli.py`；表生成器在 `src/fgen/gen/*_sweep.py` 与 `src/fgen/compare/`。

## 生成与检查（无服务器）

| 命令 | 做什么 | 模块 | 时长 |
| --- | --- | --- | --- |
| `fgen gen smelting --rate R [...]` | 冶炼列：ASCII + 契约 + predict + 蓝图 | `gen/smelting_row.py` | 毫秒 |
| `fgen gen mining --case NAME [...]` | 画好的矿区上铺钻机（锚定单元） | `gen/mining_patch.py`, `terrain/paint.py` | 毫秒 |
| `fgen gen direct-chain --recipe R --rate X [...]` | 直插链：周期求解 × k；`--prove` 证明最小；`--simulate` 顺带实测 | `gen/direct_chain.py`, `solve/` | 秒到分（绿瓶 60 s 盒） |
| `fgen compare FILE` | **阶段 9**：变体文件的每个成员并排生成，一张硬指标表 → `compare/<name>.{jsonl,md}` | `compare/` | 成员最慢者 × 成员数 ÷ 进程数 |
| `fgen decode / encode / render / validate` | 蓝图字符串工具 | `blueprint/`, `layout/` | 毫秒 |
| `fgen ask site --product P --rate X --out q.json` | 站点判断题（选矿候选 + 事实、放置偏好）写成问题文件 | `site/decide.py` | 起一次服务器拍快照 + 一次规划，约 2 分钟 |

## 特征化表（`characterization/`，回归用；`--measure` 需服务器）

| 命令 | 表 | 不带 `--measure` | 带 `--measure` | 去向（阶段 9） |
| --- | --- | --- | --- | --- |
| `fgen sweep smelting` | `smelting_row` | 秒 | 约 2 分钟 | 保留（回归） |
| `fgen sweep mining [--real-map]` | `mining_patch` | 秒 | 30 s（画矿）/ 15 s（真实地图） | 保留 |
| `fgen sweep direct-chain` | `direct_chain` | 约 30 分钟（绿瓶证明） | +实测 | 保留；prove 行是"周期不变"的裁判 |
| `fgen sweep fluid` | `fluid_chain` | 约 7 分钟 | +实测 | 保留 |
| `fgen sweep palette` | `palette` | 约 40 分钟（两行证明） | 约 40 分钟 | 保留；随时可用行按 §7 新口径对照 |
| `fgen sweep solver-time [--only] [--procs 1,4]` | `solver_time` | 10–40 分钟 | 不实测 | 保留；任务 A、C 的时间口径表 |
| `fgen sweep site [--real-map] [--only] [--decisions] [--rates] [--keep-save]` | `site` | 秒（flat） | 约 25 分钟（全场景） | 规划部分并入 `compare` 的站点家族（任务 B）；实测行保留 |

特征化表的键是 `params`，行有 `measured` 就是回归数据：**只由 sweep 生成，不手改**。`compare/` 的表是对照用的、按变体名；两者不混。

## 实机检查与实测

| 命令 | 做什么 | 时长 |
| --- | --- | --- |
| `fgen verify [--ids]` | `docs/VERIFY.md` 的实机检查，JSON 一行一项 | 约 15 s |
| `fgen gen direct-chain ... --simulate [--keep-save LABEL]` | 单元实测；`--keep-save` 把存档留下 | 分钟 |
| `fgen sweep ... --measure` | 见上表 | 见上表 |
| `uv run --extra solver pytest -m "not headless"` | 不起服务器的全量测试 | 约 12–15 分钟 |
| `uv run --extra solver pytest -m headless` | 需要二进制 | 约 3–5 分钟 |

## 脚本（`bin/`、`scripts/`）

| 脚本 | 做什么 |
| --- | --- |
| `bin/headless.sh install \| dump \| path` | 下载 headless（钉版本）、按 `FGEN_MODS` 导出 `data-raw-dump.json` |
| `bin/publish-save.sh SAVE.zip [msg]` | 把存档推到孤儿分支 `saves`（主分支不进存档） |
| `scripts/extract_seed.py DUMP VERSION [mods]` | 从 dump 重新生成种子数据（加原型改 `ADD` 表） |
| `.claude/hooks/session-start.sh` | 云端会话启动：`uv sync`、装 headless、导出 dump |

## 目录

| 目录 | 内容 | 进仓库？ |
| --- | --- | --- |
| `characterization/` | 回归表（`params` 为键，含实测） | 是，只由 sweep 生成 |
| `compare/` | 对照表（变体名为键，硬指标） | 支撑了某个已提交选择的表进；草稿表不进 |
| `config/decisions/` | 站点判断题的答案文件（`@name`） | 是 |
| `config/variants/` | 变体文件（`fgen compare` 的输入） | 是 |
| `docs/experiments/` | 每个任务的实验记录（迭代、失败、统计） | 是 |
| `docs/plan-phase*.md` | 任务书 | 是 |
| `saves/` | 存档 | 否（`saves` 分支） |
