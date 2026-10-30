# factorio-agent (fgen 分支)

Factorio 蓝图生成器库：agent 的产出是参数或生成器代码，不是坐标。总体方案见 `docs/design.md`（第二版，按六轮实验结论修订；第一版存为 `docs/design-v1-2026-10-24.md`），任务书见 `docs/plan-phase0.md`、`docs/plan-phase1.md`、`docs/plan-phase2.md`、`docs/plan-phase3a.md`、`docs/plan-phase4a.md`、`docs/plan-phase4b.md`、`docs/plan-phase5a.md`、`docs/plan-phase7.md`、`docs/plan-phase8.md`（实验，记录在 `docs/experiments/`），待实机核对清单见 `docs/VERIFY.md`，用法见 `README.md`。

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
- `characterization/mining_patch.{jsonl,md}` 由 `uv run fgen sweep mining [--measure] [--real-map]` 生成，同样不要手改；`--real-map` 起正常地图服务器（`config/ore-map-gen-settings.json`），其余保留旧行。
- `characterization/site.{jsonl,md}` 由 `uv run --extra solver fgen sweep site [--measure] [--real-map] [--only flat,real-map,red-science] [--keep-save LABEL]` 生成，不要手改；按场景（`flat`、`real-map`、`red-science-0.5`、`red-science-1`）合并，没测的场景保留旧行（规划渲染未变时保留旧实测）。站点里的单元由 `fgen.site.build` 从链路放置（场景函数里不写坐标、不写锚点）。
- `characterization/direct_chain.{jsonl,md}` 由 `uv run fgen sweep direct-chain [--measure]` 生成，不要手改；不带 `--measure` 时保留蓝图未变的旧实测。
- `characterization/solver_time.{jsonl,md}` 由 `uv run --extra solver fgen sweep solver-time [--only CASE,...] [--procs 1,4]` 生成（无服务器），不要手改；没选的用例保留旧行。
- `characterization/fluid_chain.{jsonl,md}` 由 `uv run --extra solver fgen sweep fluid [--measure]` 生成（塑料、硫酸，CP-SAT），不要手改；不带 `--measure` 时保留蓝图未变的旧实测。与 `direct_chain` 分开，重新生成时不会重解绿瓶。
- OR-Tools 是可选依赖：`uv run` 会按锁文件精确同步、把它卸掉，用到 CP-SAT 的命令与测试一律 `uv run --extra solver ...`。
- 流体：口几何（先镜像后旋转、配方按类合并盒子、闲置口）只在 `geom/fluid.py`；求解器的管网词汇在 `solve/cpsat_fluid.py`；穷尽搜索后端拒绝流体配方。
- 周期求解器（`src/fgen/solve`）核心零第三方依赖；周期的边界条件写在 `solve/period.py` docstring，`validate` 是最终裁判。直插链用到的配方实测前要解锁（`RconCharacterizer` 自动做）。
- 路由（`src/fgen/route`）是纯函数：输入 `Site`（`Terrain` 快照 + `PlacedUnit` + `Link`），只读接口契约与端口下的实体；单元测试用手写 `Terrain`。真实地图流程固定为清障 → 快照 → 生成/路由 → 部署。
- 存档不进主分支：`FGEN_SAVE_DIR` / `--keep-save` 保留的 zip 用 `bin/publish-save.sh` 推到孤儿分支 `saves`。
- 同一测试模块里只能用一个服务器 fixture（`server` 或 `ore_server`），两个都要用就拆模块。
- 种子要加原型时改 `scripts/extract_seed.py` 的 `ADD` 表（及 `loader.py` 字段表）后重新生成。
