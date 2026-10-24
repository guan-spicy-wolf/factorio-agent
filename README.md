# fgen — Factorio 蓝图生成器库（阶段 1：headless 测试场与实测）

agent 的产出是**生成器参数或生成器代码**，不是坐标。总体方案见 `docs/design.md`，任务书见 `docs/plan-phase0.md`（无服务器底座）与 `docs/plan-phase1.md`（headless 测试场），数值与约定的核对状态见 `docs/VERIFY.md`。

阶段 1 起可以连 Factorio headless（当前 2.1.23，默认开启 space-age/quality/elevated-rails/recycler）：`fgen.sim` 有 RCON 客户端、服务器管理、蓝图部署与读回、测试夹具（harness）、实测与 VERIFY 检查。没有二进制时 `fgen.sim.protocol` 的 `NotAvailable*` 仍是默认，所有 headless 测试自动 skip。

## 快速开始

```sh
uv sync
uv run pytest
uv run ruff check . && uv run ruff format --check .

uv run fgen gen smelting --rate 7.5                       # ASCII + 接口契约 + predict + 蓝图字符串
uv run fgen gen smelting --rate 15 --belt fast-transport-belt
uv run fgen gen smelting --rate 5 --furnace electric-furnace --pole medium-electric-pole --sides 2
uv run fgen sweep smelting                                # 写 characterization/smelting_row.{jsonl,md}
uv run fgen decode <蓝图字符串或文件>                      # 美化 JSON
uv run fgen encode <json 文件>
uv run fgen render <蓝图字符串或文件> [--rulers]
uv run fgen validate <蓝图字符串或文件>
```

## headless 测试与实测

二进制位置：`$FACTORIO_DIR/bin/x64/factorio`（默认 `~/factorio`；`bin/headless.sh install` 下载，`bin/headless.sh dump` 按 `FGEN_MODS` 指定的 mod 组合导出，默认全开）。

```sh
uv run pytest -m "not headless"     # 不启动服务器，约 10 秒
uv run pytest -m headless           # 35 个，需要二进制；约 30 秒（4 核容器实测）
uv run fgen verify                  # VERIFY.md 的 9 项实机检查，JSON 一行一项，约 15 秒
uv run fgen sweep smelting --measure   # 18 组冶炼列实测写入 characterization/，约 2 分钟
```

- 服务器：`HeadlessServer`（`fgen.sim.server`）在临时目录里建平地存档（`config/test-*.json`，mod 组合取 `DEFAULT_MODS`），`--start-server` 并等 RCON 就绪；退出时 `/quit`，超时再 terminate/kill，并用 `PR_SET_PDEATHSIG` 保证 Python 进程死掉时服务器也被杀。一次只起一个实例：pytest 每个测试模块一个服务器（`tests/conftest.py` 的 `server` fixture），按模块顺序运行。
- 时间一律按游戏 tick：测量时把 `game.speed` 调到 20–64 倍，用游戏内 `script.on_nth_tick` 计数与快照，Python 只轮询 `game.tick`。
- `fgen verify --attach PORT --password PW`、`fgen sweep smelting --measure --attach PORT` 可以复用一个已在运行的服务器。

| 模块 | 内容 |
| --- | --- |
| `fgen.sim.rcon` | 标准库 Source RCON；Factorio 每条命令恰好回一个包（任意长度） |
| `fgen.sim.server` | `HeadlessServer`：建存档、启动、等待就绪、停止、`set_speed`/`tick`/`wait_ticks` |
| `fgen.sim.lua` | `/sc` 片段模板（清场、读实体、导入并建造蓝图、导出蓝图、地形报告） |
| `fgen.sim.deployer` | `RconDeployer`：蓝图导入 + 复活 ghost 放置（`place_direct` 为 create_entity 对照），`read_back`/`clear` |
| `fgen.sim.harness` | 接口接无限源（infinity chest → loader，按带道侧向汇入）、计数汇、无限电 |
| `fgen.sim.measure` | `RconCharacterizer.measure(unit, warmup_s, sample_s, samples, speed)` |
| `fgen.sim.verify` | VERIFY.md 条目的实机检查，`fgen verify` |
| `fgen.layout.compare` | 忽略顺序与浮点误差的布局比较（往返测试用） |

所有子命令都接受 `--data data-raw-dump.json`（放在子命令前），用真实 dump 代替种子数据。

## 布局

| 模块 | 内容 |
| --- | --- |
| `fgen.data` | `schema.py` 镜像 `data.raw` 字段名的 dataclass；`loader.py` 的 `load_seed()` / `load_dump(path)` / `fields_used()`；`seed/vanilla-2.0.json` 手写种子数据（含引擎常数），**唯一允许出现数字的地方** |
| `fgen.geom` | 纯函数几何：`Direction`（16 向）/`Vec2`/`Tile`、占地、爪子取放格、电杆覆盖与拉线、带容量与带道、液体连接点 |
| `fgen.layout` | `Placement`/`Interface`/`Layout`/`Unit`；平移/旋转/镜像；`validate()`（违例列表，不抛异常）；`to_ascii()` |
| `fgen.blueprint` | 字符串编解码、`unit_to_blueprint`、`blueprint_to_layout` |
| `fgen.gen` | `Generator` 协议与 `Infeasible`；`throughput`（解析吞吐模型、`predict`）；`smelting_row`（带供料行 → 冶炼列）；`sweep`（特征化） |
| `fgen.sim` | 服务器相关：协议与 `NotAvailable` 默认值、RCON、部署、实测（见上节） |

## 约定（摘要，全文见任务书 §3）

- x 向东、y 向南；瓦片 `(tx, ty)` 是左上角在该点的 1×1 格；实体 `position` 是中心。
- `Direction` 是 2.0 的 16 向整数（N=0, E=4, S=8, W=12）；顺时针 90° 把 `(x, y)` 变为 `(-y, x)`。
- N 向爪子从北边取、往南边放；带道 left/right 相对流向（东向带 left 在北）；爪子放带落远道（均已在 2.0.76 与 2.1.23 实机核对）。
- 导出的蓝图写出电杆铜线 `wires`（2.0.76 / 2.1.23 都不会给蓝图里的电杆自动连线）。
- 生成器 `generate()` 要么返回通过 `validate()` 的 `Unit`，要么抛 `Infeasible(reason, suggestions)`；`suggestions` 是经过可行性检验的具体参数改法。

## 冶炼列生成器

参数见 `fgen.gen.smelting_row.SmeltingRowParams`。炉数、行长、行偏移（由爪子取放向量推出）、炉步长（由占地推出）、电杆行数与间隔（由 `supply_area_distance` 与 `maximum_wire_distance` 推出：一排电杆的供电区能覆盖上下两排爪子就只放一排，否则两排都放）全部从游戏数据派生。换电杆或熔炉只改参数。5 / 7.5 / 15 板/秒的渲染见 `characterization/smelting_row.md`。

## 测试样本

`tests/fixtures/` 里 3 条 2.0 蓝图字符串取自 MIT 许可的 factorio-draftsman 仓库，另有 3 条由 headless 2.0.76 游戏自己导出，来源见 `tests/fixtures/SOURCES.txt`。
