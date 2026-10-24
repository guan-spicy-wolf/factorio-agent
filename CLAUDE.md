# factorio-agent (fgen 分支)

Factorio 蓝图生成器库：agent 的产出是参数或生成器代码，不是坐标。总体方案见 `docs/design.md`，任务书见 `docs/plan-phase0.md`、`docs/plan-phase1.md`，待实机核对清单见 `docs/VERIFY.md`，用法见 `README.md`。

本分支是孤儿分支；`master` 上的旧 agent（Lua 脚本 + RCON 循环）与本分支无关，不要引用。

## 约定

- 只用 `uv`：`uv sync`、`uv run pytest`、`uv run ruff check . && uv run ruff format --check .`、`uv add`。不用 pip 或裸 python。
- 核心库 `src/fgen` 零第三方运行时依赖。
- 任何实体尺寸、速度、配方数字（以及 tick 率、每格物品数等引擎常数）只能出现在 `src/fgen/data/seed/*.json`，代码里不写死；测试的期望值也从种子数据推导。
- 凭记忆写下、未经实机核对的数值与约定，一律登记到 `docs/VERIFY.md`。
- 坐标与方向约定见 `docs/plan-phase0.md` §3，全库统一。
- Factorio headless 2.1.23 由 SessionStart 钩子自动下载到 `$FACTORIO_DIR`（默认 `~/factorio`），`$FACTORIO_DATA_DUMP` 指向 `--dump-data` 输出（mod 组合由 `FGEN_MODS` 决定，默认 base+space-age+quality+elevated-rails+recycler，与 `fgen.sim.server.DEFAULT_MODS` 一致）；手动用 `bin/headless.sh install|dump`。种子数据用 `scripts/extract_seed.py` 从 dump 重新生成。
- 需要服务器的测试打 `@pytest.mark.headless`（无二进制自动 skip）；`uv run pytest -m "not headless"` 不启动服务器。一次只起一个服务器实例，测试/脚本退出时必须停掉它。实测只进 `characterization/*.jsonl` 的 `measured`，游戏内计时一律按 tick。
- `characterization/smelting_row.{jsonl,md}` 由 `uv run fgen sweep smelting [--measure]` 生成，不要手改（不带 `--measure` 时保留蓝图未变的旧实测）。
