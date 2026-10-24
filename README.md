# fgen — Factorio 蓝图生成器库（阶段 0：无服务器底座）

agent 的产出是**生成器参数或生成器代码**，不是坐标。总体方案见 `docs/design.md`，本阶段任务书见 `docs/plan-phase0.md`，所有凭记忆写下的数值与约定见 `docs/VERIFY.md`。

本阶段没有 Factorio 服务器：不含 RCON / 部署 / 实测，`fgen.sim.protocol` 只有接口与 `NotAvailable` 占位。

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

所有子命令都接受 `--data data-raw-dump.json`（放在子命令前），用真实 dump 代替种子数据。

## 布局

| 模块 | 内容 |
| --- | --- |
| `fgen.data` | `schema.py` 镜像 `data.raw` 字段名的 dataclass；`loader.py` 的 `load_seed()` / `load_dump(path)` / `fields_used()`；`seed/vanilla-2.0.json` 手写种子数据（含引擎常数），**唯一允许出现数字的地方** |
| `fgen.geom` | 纯函数几何：`Direction`（16 向）/`Vec2`/`Tile`、占地、爪子取放格、电杆覆盖与拉线、带容量与带道、液体连接点 |
| `fgen.layout` | `Placement`/`Interface`/`Layout`/`Unit`；平移/旋转/镜像；`validate()`（违例列表，不抛异常）；`to_ascii()` |
| `fgen.blueprint` | 字符串编解码、`unit_to_blueprint`、`blueprint_to_layout` |
| `fgen.gen` | `Generator` 协议与 `Infeasible`；`throughput`（解析吞吐模型、`predict`）；`smelting_row`（带供料行 → 冶炼列）；`sweep`（特征化） |
| `fgen.sim` | 服务器相关接口（未实现） |

## 约定（摘要，全文见任务书 §3）

- x 向东、y 向南；瓦片 `(tx, ty)` 是左上角在该点的 1×1 格；实体 `position` 是中心。
- `Direction` 是 2.0 的 16 向整数（N=0, E=4, S=8, W=12）；顺时针 90° 把 `(x, y)` 变为 `(-y, x)`。
- N 向爪子从北边取、往南边放；带道 left/right 相对流向；爪子放带落远道（均待实机核对）。
- 生成器 `generate()` 要么返回通过 `validate()` 的 `Unit`，要么抛 `Infeasible(reason, suggestions)`；`suggestions` 是经过可行性检验的具体参数改法。

## 冶炼列生成器

参数见 `fgen.gen.smelting_row.SmeltingRowParams`。炉数、行长、行偏移（由爪子取放向量推出）、炉步长（由占地推出）、电杆行数与间隔（由 `supply_area_distance` 与 `maximum_wire_distance` 推出：一排电杆的供电区能覆盖上下两排爪子就只放一排，否则两排都放）全部从游戏数据派生。换电杆或熔炉只改参数。5 / 7.5 / 15 板/秒的渲染见 `characterization/smelting_row.md`。

## 测试样本

`tests/fixtures/` 里的 3 条 2.0 蓝图字符串取自 MIT 许可的 factorio-draftsman 仓库（社区蓝图站被代理拦截），来源见 `tests/fixtures/SOURCES.txt`。
