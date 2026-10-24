# 阶段 2 实施任务书：锚定单元——采矿

接续 `docs/plan-phase1.md`。阶段 1 已在 headless 上打通部署、读回、实测与核对。本阶段做第一个**锚定单元**：矿区形状由地图决定，钻机布局由算法在真实地图上生成，输出接口契约，并实测矿产率与契约一致。这是上一版方案真正卡住的地方，所以放在自由单元之前做。本文与 `design.md` 冲突时以本文为准。

## 1. 环境事实

- Factorio headless **2.1.23**，mod 组合 base + space-age + quality + elevated-rails + recycler（`fgen.sim.server.DEFAULT_MODS`，与种子数据一致）。2.1 的 API 变动已记在 `docs/VERIFY.md` 的迁移记录里；再遇到 2.0 文档与实机不一致时，以实机为准并补记。
- 平地测试场没有矿。矿要么用脚本"画"出来（`surface.create_entity{name="iron-ore", position=..., amount=...}`，一格一个资源实体），要么用带资源的正常地图生成（见 §3.5）。两种都要用：脚本画矿给形状可控的单元测试，正常地图给端到端验证。
- 资源原型：`iron-ore/copper-ore/coal/stone` 的 `minable.mining_time = 1`，`category` 缺省为 `basic-solid`；`crude-oil` 是 `basic-fluid`、`infinite`，**本阶段不做**。电钻 `mining_speed 0.5`、`resource_searching_radius 2.49`（即 5×5 搜索区）、`vector_to_place_result (0, -1.85)`（出料落在正前方一格）、`resource_categories ["basic-solid"]`。大型钻机（space-age）有 `drops_full_belt_stacks`，本阶段不用它，但数据层要能装下它。
- 没有科技：采矿产能加成为 0。单钻产率 = `mining_speed / mining_time`。

## 2. 技术约定

沿用阶段 0/1（`uv`、零运行时依赖、数字只在种子、headless 标记、按 tick 计时、一次一个服务器、实测只进 `characterization`）。另外：

- 种子数据要补 `resource` 类型（`minable.mining_time`、`minable.result/results`、`category`、`infinite`）和 `big-mining-drill`。改 `loader.py` 的字段表后用 `scripts/extract_seed.py` 重新生成，不手改 JSON。
- 锚定单元的输出仍是 `Unit`（`Layout` + `Interface` 列表 + `meta`），但多一个来源：`meta["anchor"]` 记录它锚定的地图区域、矿种、总矿量。
- 生成器以**地形快照**为输入，不直接依赖 RCON：先定义 `Terrain` 数据类（可建格集合、资源格 → (矿种, 数量)、水域格、已有实体占格），`RconTerrain` 实现 `protocol.TerrainQuery` 并能导出一个 `Terrain` 快照。生成器的单元测试用手写的 `Terrain`，不起服务器。

## 3. 模块

```
src/fgen/
  terrain/
    model.py       Terrain 快照 + 从 (tile -> (name, amount)) 构造；连通分块（矿区分割）
    paint.py       脚本画矿的形状生成（矩形、L 形、随机 blob，给测试和夹具用）
  gen/
    mining_patch.py   家族：钻机铺矿（锚定单元）
  sim/
    terrain.py     RconTerrain（实现 TerrainQuery，含 snapshot(area) -> Terrain）
    harness.py     补：给采矿单元的输出接汇（已有逻辑复用），给电接口接电
    measure.py     补：采矿单元的实测（无输入源，只有汇；逐钻状态诊断）
```

### 3.1 `terrain/model.py`

- `Terrain(buildable: frozenset[Tile], resources: dict[Tile, tuple[str, int]], water: frozenset[Tile], occupied: frozenset[Tile])`。
- `patches(terrain, resource: str) -> list[Patch]`：按 4 邻接把同种矿格分成连通块。`Patch` 有 `tiles`、`bounds`、`total_amount`。
- 这些全是纯函数，有 hypothesis 测试（随机画几块矿，连通块数与并集正确）。

### 3.2 `gen/mining_patch.py`

参数：

```python
@dataclass(frozen=True)
class MiningPatchParams:
    resource: str = "iron-ore"
    drill: str = "electric-mining-drill"
    belt: str = "transport-belt"
    pole: str = "small-electric-pole"
    axis: Literal["x", "y"] = "x"      # 带子走向
    max_belts: int | None = None      # 不设则按矿区能放多少放多少
    min_ore_tiles_per_drill: int = 1  # 搜索区内至少几格矿才放这台钻
```

输入：`Patch` + `Terrain`（钻机和带子都只能放在可建且未占用的格上，矿格本身可建）。输出：`Unit`。

经典排法：沿 `axis` 铺带子行，带子两侧各一排钻机面向带子（钻机 3×3，出料落在正前方一格，正好是带子）；相邻带子行之间间隔 = 两排钻机的厚度（6 格），即带子周期 7 格。每排钻机沿带子紧密排列，步长 3。电杆：从 `supply_area_distance` 与 `maximum_wire_distance` 推出，放在**钻机行里**（用一个 3 格钻位换成电杆 + 空位），能同时覆盖带子两侧两排钻机时就只在一侧放；覆盖判定与阶段 0 一样用 `pole.supply_tiles`，不能写 if 实体名。

由算法派生：

- 哪些钻位放钻：搜索区（半径 `resource_searching_radius`）内矿格数 ≥ `min_ore_tiles_per_drill` 且占地全部可建、不与已放实体重叠。
- 每条带子的产率 = 该带两侧钻机数 × 单钻产率；超过带容量就在超出处截断该行（截断处的钻机不放，并在 `meta["dropped_drills"]` 记录原因），或者按 `axis` 换向、改带等级作为 `Infeasible.suggestions`。单钻产率、带容量都从种子算。
- 带子方向：统一指向矿区一侧的边缘；每条带子的末端即一个输出接口（`kind="item"`，`lane="both"`，`rate` = 该带稳态产率，`assumptions` 写明"产能加成 0、所有钻下有矿"）。多条带子就是多个输出接口，**不做合流**（合流是阶段 3 连接算法的事）。
- 电接口：第一根电杆。
- `meta["anchor"] = {resource, patch_bounds, total_amount, drills, est_depletion_s}`，其中 `est_depletion_s` = 总矿量 / 总产率（粗略，边缘钻机先停，实际曲线不是常数）。

不变量：沿用 `layout.validate`，另加 `drill.no_ore`（钻机搜索区内无矿）、`drill.output_blocked`（出料格不是带子/箱子）。

### 3.3 `sim/terrain.py`

`RconTerrain`：

- `resources(area)`：`find_entities_filtered{area, type="resource"}` → `{Tile: (name, amount)}`。
- `buildable(tile)` / `water(area)`：`find_tiles_filtered` 按 `collision_mask` 判断；注意 2.1 的 tile collision mask 字段格式，查实机。
- `occupied(area)`：非资源实体的占格（用阶段 1 的 `read_back` 加 footprint）。
- `snapshot(area) -> Terrain`。
- `paint(patch_spec)`：用脚本在平地上画矿，供测试；`terrain/paint.py` 只生成格集合与数量，`RconTerrain.paint` 负责落地。

### 3.4 实测（`measure.py` 补充）

- 采矿单元无输入；每个输出接口接一个汇（沿用阶段 1 的 loader + 钢箱计数）。电接 `electric-energy-interface`。
- 预热短（钻机几秒就出矿），采样 6 段。返回每个接口的实测产率，与契约 `rate` 对比；逐钻 `status`（`defines.entity_status.no_minable_resources` 等）与 `mining_target` 诊断。
- 把结果写 `characterization/mining_patch.jsonl/.md`：参数、矿区形状描述、契约产率、实测产率、偏差、被截断的钻机数。

### 3.5 真实地图验证

- `config/ore-map-gen-settings.json`：正常地图生成（资源默认、无敌人、`peaceful_mode`），固定 `seed`。`HeadlessServer` 支持选择 map-gen 配置。
- 启动后生成出生点周围区块（`force.chart` / `request_to_generate_chunks` + `force_generate_chunk_requests`），`RconTerrain.snapshot` 取 ±150 格，找出最近的 `iron-ore` 连通块。
- 对它跑生成器 → 部署 → 实测。这是本阶段的完成判据来源。

## 4. 里程碑与验收

| # | 内容 | 验收 |
| --- | --- | --- |
| M1 | 种子补 `resource` 与大钻；`terrain/model.py`、`paint.py` | 不起服务器的测试：连通分块、形状生成 |
| M2 | `gen/mining_patch.py` + 新不变量 + ASCII 渲染（钻 `D`、矿格在渲染里用背景字符区分，如 `:`） | 手写矩形、L 形、带洞的矿区：生成通过 validate；换电杆/带/钻只改参数；带容量截断有用例；`Infeasible` 有用例 |
| M3 | `sim/terrain.py` + 画矿 | headless：画一块矩形矿，`snapshot` 读回与画的一致；`buildable/water` 在平地与有水地图上正确 |
| M4 | 实测 | headless：矩形矿（单带、双带）与 L 形矿，每个输出接口实测产率与契约偏差 ≤ 5%（钻机数不多时离散效应明显，偏差超了要解释） |
| M5 | 真实地图端到端 | 固定 seed 的正常地图，找最近铁矿，生成、部署、实测，产率与契约一致；结果写进 `characterization/mining_patch.md` 单独一节 |
| M6 | 收尾 | README、VERIFY.md（新增条目：出料落格与带道、`no_minable_resources` 状态名、tile collision mask 格式、钻机搜索区边界含不含 2.49 的半格） |

M1–M4 必须完成；M5 尽力；M6 必做。

## 5. 不要做的事

- 不做抽油机、海水泵（阶段 5）；不做多条带的合流与平衡（阶段 3）。
- 不做选址 agent；矿区由测试或 §3.5 的"最近铁矿"规则给出。
- 不追求钻机覆盖率最优；只要求可用、可解释、可部署。
- 不改阶段 1 的冶炼列生成器。
