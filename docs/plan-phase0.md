# 阶段 0 实施任务书：无服务器底座

本文是 `docs/design.md`（总体方案）在阶段 0 的落地任务书。执行者按本文实现；本文与 `design.md` 冲突时以本文为准，并在 PR 说明中指出冲突点。

## 1. 边界

**本轮环境没有 Factorio 二进制，也没有网络访问 factorio.com。** 因此：

- 不做：RCON 部署器、headless 往返测试、地形查询、参数扫描实测。这些只留 `Protocol` 接口和 `NotAvailable` 占位实现（见 §4.7）。
- 做：数据层（手写种子数据 + 预留真实 dump 的加载器）、几何库、蓝图字符串编解码、布局模型与变换、静态不变量检查、ASCII 渲染、第一个生成器家族（带供料行 → 冶炼列）及其解析吞吐模型。
- 所有凭记忆写下、未经实机核对的数值和约定，必须登记到 `docs/VERIFY.md`（见 §6）。不得把猜测伪装成事实。

本分支是孤儿分支，与 `master` 上的旧 agent 代码无关，不要去复用或引用 `master`。

## 2. 技术约定

- Python ≥ 3.12，`uv` 管理项目（`uv sync` / `uv run pytest` / `uv add`）。禁止使用 pip 或裸 python。
- 核心库零第三方运行时依赖（标准库即可：`dataclasses`、`zlib`、`base64`、`json`、`math`）。开发依赖：`pytest`、`hypothesis`、`ruff`。
- 包名 `fgen`，`src/fgen/` 布局。所有公开函数带类型标注；`ruff check` 与 `ruff format --check` 必须干净。
- 数据不变量由构造保证：几何库只读 `GameData`，不在代码里写死任何实体尺寸或速度数字。任何数字只能出现在 `src/fgen/data/seed/*.json` 里。
- 每个里程碑一个或多个提交，提交信息写清做了什么；每个里程碑结束时 `uv run pytest` 全绿。

## 3. 坐标与方向约定（全库统一）

- 世界坐标：x 向东增大，y 向南增大。瓦片 `(tx, ty)` 指左上角位于 `(tx, ty)` 的 1×1 格。
- 实体 `position` 是中心点（与蓝图 JSON 一致）。宽度为偶数的实体中心落在整数坐标，奇数落在 `.5`。
- `Direction` 为 2.0 的 16 向枚举：N=0, NE=2, E=4, SE=6, S=8, SW=10, W=12, NW=14。本轮生成器只用四个正方向，但枚举和编解码必须保留 16 向。
- 向量旋转：以 N 为基准，顺时针转 90°（N→E）把 `(x, y)` 映射为 `(-y, x)`。校验：N 的 `(0,-1)` 转 E 得 `(1, 0)`。
- 镜像：`mirror_x` 把 x 取反并交换 E/W；`mirror_y` 把 y 取反并交换 N/S。2.0 蓝图实体支持 `mirror` 字段（用于化工厂等不对称实体），本轮种子集里没有需要它的实体，编解码要原样保留该字段。
- 爪子：prototype 的 `pickup_position` / `insert_position` 是 N 向时的向量，按实体方向旋转后加到中心点，再向下取整得到取/放瓦片。约定"N 向爪子从北边取、往南边放"——这条要登记到 VERIFY.md。
- 带子：方向为流向。带道 `left` / `right` 相对流向定义。爪子放到带上落在远道（相对爪子）。登记到 VERIFY.md。

## 4. 模块与接口

目录：

```
src/fgen/
  data/        schema.py  loader.py  seed/vanilla-2.0.json
  geom/        vec.py  footprint.py  inserter.py  pole.py  belt.py  fluid.py
  layout/      model.py  transform.py  validate.py  render.py
  blueprint/   codec.py  export.py  import_.py
  gen/         base.py  throughput.py  smelting_row.py
  sim/         protocol.py
  cli.py
tests/
docs/         design.md  plan-phase0.md  VERIFY.md
characterization/   smelting_row.jsonl
```

### 4.1 `data`：游戏数据层

`schema.py`：dataclass 镜像 `data.raw` 的字段名（不要改名，便于以后直接吃真实 dump）。至少：

- `EntityProto`: `name, type, collision_box((x1,y1),(x2,y2)), tile_width, tile_height, flags`; 按类型可选字段：
  - inserter: `pickup_position, insert_position, rotation_speed, extension_speed, energy_per_movement`（吞吐估算用 `rotation_speed`/`extension_speed` 推摆动周期即可，先粗略）
  - electric-pole: `supply_area_distance, maximum_wire_distance`
  - transport-belt / underground-belt / splitter: `speed`（tiles/tick），underground 另有 `max_distance`
  - furnace / assembling-machine: `crafting_speed, crafting_categories, energy_usage, energy_source.type`（electric / burner）
  - mining-drill: `mining_speed, resource_searching_radius, vector_to_place_result`
  - pipe / pipe-to-ground / offshore-pump / pumpjack / storage-tank: `fluid_box.pipe_connections[]`（2.0 格式：每个连接有 `position` 与 `direction`），pipe-to-ground 另有 `max_underground_distance`
  - container: `inventory_size`
- `RecipeProto`: `name, category, energy_required, ingredients[(name, amount)], results[(name, amount)]`
- `GameData`: `entities: dict[str, EntityProto]`, `recipes: dict[str, RecipeProto]`, `version: str`, `source: "seed" | "dump"`

`seed/vanilla-2.0.json`：手写种子数据，覆盖以下实体与配方（vanilla 2.0 base，不含太空时代）：

- 带：`transport-belt, fast-transport-belt, express-transport-belt` 及对应 `underground-belt`、`splitter`
- 爪：`burner-inserter, inserter, fast-inserter, long-handed-inserter, bulk-inserter`
- 电杆：`small-electric-pole, medium-electric-pole, big-electric-pole, substation`
- 炉与机器：`stone-furnace, steel-furnace, electric-furnace, assembling-machine-1/2/3`
- 采矿：`burner-mining-drill, electric-mining-drill`
- 液体：`pipe, pipe-to-ground, offshore-pump, pumpjack, storage-tank`
- 箱：`wooden-chest, iron-chest, steel-chest`
- 配方：`iron-plate, copper-plate, stone-brick, steel-plate, copper-cable, electronic-circuit, iron-gear-wheel`

每个数值都是"凭记忆"，JSON 顶层要有 `"_provenance": "hand-written from memory; verify against --dump-data"`，并把整份种子文件列入 VERIFY.md。

`loader.py`：
- `load_seed() -> GameData`
- `load_dump(path) -> GameData`：读取真实 `factorio --dump-data` 产出的 `data-raw-dump.json`，只抽取 schema 需要的字段。没有真实文件无法测试，但要写好并用一个"手工构造的 data.raw 片段"做单元测试，保证字段路径正确。
- `fields_used() -> dict[type, list[str]]`：列出几何库实际读取的字段，供拿到真实 dump 时逐项核对。

### 4.2 `geom`：几何库

全部是纯函数，输入 `GameData` + 实体名 + 位置 + 方向。

- `vec.py`: `Vec2`（不可变、支持加减、`rotate(Direction)`、`mirror_x/mirror_y`）、`Direction`（含 `rotate_cw(n)`、`opposite`、`mirror_x/mirror_y`、与蓝图整数互转）、`Tile`。
- `footprint.py`: `tiles_of(gd, name, pos, dir) -> frozenset[Tile]`；`aabb_of(...)`；`center_for_tile_anchor(gd, name, top_left_tile, dir) -> Vec2`（给左上角瓦片算出合法中心，生成器用它避免手算 .5）。方向为 E/W 时宽高互换。
- `inserter.py`: `pickup_tile(gd, name, pos, dir) -> Tile`，`drop_tile(...)`。
- `pole.py`: `supply_tiles(gd, name, pos) -> frozenset[Tile]`；`wire_reaches(gd, a_name, a_pos, b_name, b_pos) -> bool`（取两者 `maximum_wire_distance` 的较小值，中心距比较）。
- `belt.py`: `next_tile(tile, dir)`；`items_per_second(gd, name) -> float`（`speed * 60 * 8`，每道一半；把 8 这个常数登记到 VERIFY.md）；`underground_partner_range(gd, name) -> int`。
- `fluid.py`: `connection_points(gd, name, pos, dir) -> list[(Tile, Direction)]`。只做这一项，液体细节留阶段 5。

### 4.3 `layout`：布局模型、变换、校验、渲染

`model.py`：
- `Placement(name, position: Vec2, direction: Direction, extra: dict)`，`extra` 透传蓝图字段（`recipe`, `items`, `mirror`, `type`(underground 的 input/output), `filters` 等）。
- `Interface(kind: "item"|"fluid"|"power", material: str|None, tile: Tile, direction: Direction, lane: "left"|"right"|"both"|None, rate: float|None, role: "input"|"output", assumptions: list[str])`。
- `Layout(placements: list[Placement])`，提供 `bounds()`、`tiles()`。
- `Unit(layout: Layout, interfaces: list[Interface], meta: dict)`。

`transform.py`：`translate / rotate / mirror_x / mirror_y` 作用于整个 `Layout` 和 `Unit`（接口位置、方向、带道一起变；镜像时 `left`/`right` 互换）。性质测试：旋转四次 = 恒等；镜像两次 = 恒等；变换后 `tiles_of` 的并集等于对原瓦片集做同一变换。

`validate.py`：返回 `list[Violation(code, message, tiles)]`，不抛异常。至少这些不变量：

| code | 含义 |
| --- | --- |
| `overlap` | 任意两个实体瓦片相交 |
| `inserter.pickup_empty` / `inserter.drop_empty` | 爪子取/放瓦片上没有可持有物品的实体（带、炉、机器、箱）且该瓦片不是声明的接口口 |
| `power.uncovered` | 用电实体（electric 能源的爪、炉、机器、钻机）没有任何一格落在任一电杆的供电区 |
| `power.disconnected` | 电杆按 `wire_reaches` 建图后不连通 |
| `belt.discontinuous` | 带子瓦片的下一格既不是带子/地下带入口，也不是声明的输出接口 |
| `underground.unpaired` | 地下带没有在 `max_distance` 内找到同方向的配对出口 |
| `fluid.unintended_adjacency` | 两个液体连接点相对且物料不同（本轮可只做框架） |

`render.py`：`to_ascii(layout) -> str`。每格一个字符：带按方向 `^>v<`，爪子 `i`，炉 `F`，电杆 `P`，机器 `A`，钻机 `D`，管 `=`，箱 `c`，空 `.`。多格实体用同一字母填满。这是没有服务器时最主要的人工复核手段，务必做好。

### 4.4 `blueprint`：编解码

- `codec.py`: `decode(s: str) -> dict`（去掉前导 `"0"`，base64 → zlib → JSON）；`encode(obj: dict) -> str`。保证 `decode(encode(x)) == x`。版本号 64 位整数，默认值从 `GameData.version` 打包（`major<<48 | minor<<32 | patch<<16`），登记到 VERIFY.md。
- `export.py`: `unit_to_blueprint(unit, gd, label=None) -> dict`，实体编号从 1 起，`direction` 写蓝图整数；N 向（0）是否可省略——保守起见总是写出。
- `import_.py`: `blueprint_to_layout(bp: dict) -> Layout`。
- 往返测试：`Layout → bp → Layout` 相等；对任意手写布局成立（用 hypothesis 生成随机合法实体列表）。
- 真实蓝图样本：尝试从网络（非 factorio.com 的站点，如社区蓝图站）获取 2–3 条 2.0 蓝图字符串放进 `tests/fixtures/`，测试能解码且重编码后再解码相等。若网络不通，跳过该测试并在 VERIFY.md 记一条。

### 4.5 `gen`：生成器家族 1 —— 带供料行（冶炼列）

`base.py`：
```python
class Generator(Protocol[P]):
    family: str
    def generate(self, params: P, gd: GameData) -> Unit: ...
    def invariants(self, unit: Unit, gd: GameData) -> list[Violation]: ...
```
`generate` 必须要么返回通过 `validate` 的 `Unit`，要么抛 `Infeasible(reason, suggestions)`，`suggestions` 是具体的参数改法（如"改 `sides=2`"或"改 `input_lanes=2`"）。不允许返回不合法布局。

`throughput.py`（纯解析，不依赖仿真）：
- `machines_needed(gd, recipe, machine, target_rate) -> int`
- `belt_lane_capacity(gd, belt) -> float`
- `inserter_throughput_estimate(gd, inserter) -> float`（粗略：按 `rotation_speed` 算半周往返；登记到 VERIFY.md）
- `predict(unit) -> dict`：面积、机器数、理论吞吐、瓶颈（带道 / 爪子 / 机器）

`smelting_row.py`：参数
```python
@dataclass(frozen=True)
class SmeltingRowParams:
    recipe: str = "iron-plate"
    furnace: str = "stone-furnace"
    target_rate: float = 7.5          # 产物/秒
    belt: str = "transport-belt"
    inserter: str = "inserter"
    pole: str = "small-electric-pole"
    sides: int = 1                    # 1: 单侧；2: 共用矿带两侧对称
    fuel_lane: bool = True            # 燃料炉：矿一道、煤一道
```
经典排法（单侧）：
```
y=0  矿带 →→→→
y=1  i . i . i .      进料爪 + 电杆
y=2  F F F F F F
y=3  F F F F F F      炉（2×2 步长 2；3×3 步长 3）
y=4  i . i . i .      出料爪 + 电杆
y=5  板带 →→→→
```
由算法派生（不是常量）：炉数、行长、电杆间隔（取 `supply_area_distance` 能覆盖两排爪子且中心距 ≤ `maximum_wire_distance`；小杆需两排都放，中杆可只放一排——这要从数据算出来，不是写死）、燃料炉时的带道假设。接口契约：输入口在行首矿带瓦片（含带道与物料假设），输出口在行尾板带瓦片；电接口为第一根电杆。

可行性检查：炉数 × 单炉矿耗 > 输入带道容量时抛 `Infeasible`，建议 `sides=2` / 升级带 / `fuel_lane=False`（电炉）。目标产率超过输出带容量时也抛。

验收（替代阶段 1 的实机判据，实机留到以后）：`target_rate` 取 5、7.5、15 时，`generate` 产出通过 `validate` 的 Unit；`predict` 的理论吞吐 ≥ 目标；换 `pole` 为中杆、换 `furnace` 为 steel/electric 时**不改生成器代码**即可生成。把 5/7.5/15 的 ASCII 渲染写进 `characterization/smelting_row.md` 供人复核。

### 4.6 `characterization`

`characterization/smelting_row.jsonl`：由 `fgen sweep smelting` 生成，每行 `{params, predicted: {...}, measured: null}`。`measured` 等有服务器后再填。参数网格小一点：炉三种 × 杆两种 × 产率三档。

### 4.7 `sim`：只留接口

`protocol.py`：`Deployer`（`place(unit, origin) -> None`、`read_back(area) -> Layout`）、`TerrainQuery`（`buildable(tile)`, `resources(area)`, `water(area)`）、`Characterizer`（`measure(unit) -> dict`）。各给一个 `NotAvailable` 实现，调用即抛 `RuntimeError("requires Factorio headless; see docs/VERIFY.md")`。不要写 RCON 代码。

### 4.8 `cli.py`

`uv run fgen <cmd>`：
- `decode <bp-string-or-file>` → 美化 JSON
- `encode <json-file>` → 字符串
- `render <bp-string-or-file>` → ASCII
- `gen smelting --rate 7.5 --furnace stone-furnace --pole small-electric-pole [--sides 2]` → 打印 ASCII、接口契约、predict、蓝图字符串
- `sweep smelting` → 写 `characterization/smelting_row.jsonl` 与 `.md`
- `validate <bp-string-or-file>` → 违例列表

## 5. 里程碑与验收

按顺序完成，每个里程碑 `uv run pytest` 与 `ruff` 全绿后提交。

| # | 内容 | 验收 |
| --- | --- | --- |
| M0 | 项目骨架、`pyproject`、`CLAUDE.md`、`data` 层与种子数据 | `load_seed()` 能加载；`load_dump` 对手工 data.raw 片段测试通过 |
| M1 | `geom` 全部 | 旋转/镜像性质测试；爪子取放格、电杆覆盖、带容量的单元测试（期望值也来自种子数据而非常量） |
| M2 | `blueprint` 编解码 + `layout.model/transform/render` | codec 往返；Layout 往返（hypothesis）；真实样本（若可得） |
| M3 | `layout.validate` | 对手工构造的每一类违例各有一个失败用例和一个通过用例 |
| M4 | `gen` 冶炼列 + `throughput` + `characterization` + `cli` | §4.5 验收全部满足；`Infeasible` 的用例（如 30/s 黄带单侧石炉） |
| M5 | `docs/VERIFY.md`、`README.md` 收尾 | 见 §6 |

M0–M3 必须完成。M4 尽力完成；若时间不够，至少完成 `throughput.py` 和 `Infeasible` 逻辑，并在总结里说明未完成部分。

## 6. `docs/VERIFY.md`

一张表，列出所有凭记忆写下、需要实机核对的内容，每行：条目、当前假设值/约定、所在文件、核对方法（例如"在 headless 用 `--dump-data` 对比 `fields_used()` 的字段"或"放一个 N 向爪子观察取放方向"）。至少包含：种子数据全部数值、爪子方向约定、带道与远道约定、带速 ×8 常数、蓝图版本号打包方式、N 向 direction 是否可省略、地下带 `type` 字段取值、2.0 `fluid_box.pipe_connections` 的字段格式。

## 7. 不要做的事

- 不写任何 RCON、socket、子进程调用 Factorio 的代码。
- 不引入 LLM 调用；本阶段没有 agent 参与。
- 不做 CP-SAT、组件树容器、直插链（阶段 4）。
- 不在几何库或生成器里写死实体尺寸、速度等数字。
- 不为凑测试而放宽不变量。
