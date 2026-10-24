# factorio-agent (fgen 分支)

Factorio 蓝图生成器库：agent 的产出是参数或生成器代码，不是坐标。总体方案见 `docs/design.md`，当前阶段任务书见 `docs/plan-phase0.md`，待实机核对清单见 `docs/VERIFY.md`。

本分支是孤儿分支；`master` 上的旧 agent（Lua 脚本 + RCON 循环）与本分支无关，不要引用。

## 约定

- 只用 `uv`：`uv sync`、`uv run pytest`、`uv run ruff check .`、`uv add`。不用 pip 或裸 python。
- 核心库 `src/fgen` 零第三方运行时依赖。
- 任何实体尺寸、速度、配方数字只能出现在 `src/fgen/data/seed/*.json`，代码里不写死。
- 凭记忆写下、未经实机核对的数值与约定，一律登记到 `docs/VERIFY.md`。
- 坐标与方向约定见 `docs/plan-phase0.md` §3，全库统一。
- 本环境没有 Factorio 二进制、访问不到 factorio.com；需要服务器的部分只留接口。
