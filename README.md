# fgen — Factorio 蓝图生成器库（阶段 4c：随时可用求解、目标预设、组件调色板）

agent 的产出是**生成器参数或生成器代码**，不是坐标。总体方案见 `docs/design.md`（第二版；第一版 `docs/design-v1-2026-10-24.md`），任务书见 `docs/plan-phase0.md`（无服务器底座）、`docs/plan-phase1.md`（headless 测试场）、`docs/plan-phase2.md`（采矿锚定单元）、`docs/plan-phase3a.md`（清障、夹具、连接路由；末尾“结果”一节是接口契约够不够用的结论）与 `docs/plan-phase4a.md`（周期求解器，实验记录在 `docs/experiments/`），数值与约定的核对状态见 `docs/VERIFY.md`。

阶段 1 起可以连 Factorio headless（当前 2.1.23，默认开启 space-age/quality/elevated-rails/recycler）：`fgen.sim` 有 RCON 客户端、服务器管理、蓝图部署与读回、测试夹具（harness）、实测与 VERIFY 检查。没有二进制时 `fgen.sim.protocol` 的 `NotAvailable*` 仍是默认，所有 headless 测试自动 skip。

## 快速开始

```sh
uv sync                      # core: no third-party runtime dependency
uv sync --extra solver       # optional: OR-Tools for the CP-SAT period backend (`--backend cpsat`)
                             # plain `uv run` re-syncs exactly and removes it: use `uv run --extra solver ...`
uv run --extra solver pytest
uv run ruff check . && uv run ruff format --check .

uv run fgen gen smelting --rate 7.5                       # ASCII + 接口契约 + predict + 蓝图字符串
uv run fgen gen smelting --rate 15 --belt fast-transport-belt
uv run fgen gen smelting --rate 5 --furnace electric-furnace --pole medium-electric-pole --sides 2
uv run fgen sweep smelting                                # 写 characterization/smelting_row.{jsonl,md}
uv run fgen gen mining --case l-shape [--pole medium-electric-pole --axis y ...]   # 画好的矿区上铺钻机
uv run fgen sweep mining                                  # 写 characterization/mining_patch.{jsonl,md}
uv run fgen sweep site                                    # 平地整站（规划+路由）写 characterization/site.{jsonl,md}
uv run --extra solver fgen sweep site --measure --real-map [--only red-science] [--keep-save red-1ps]   # 真实地图整站 + 红瓶站（阶段 7）
uv run --extra solver fgen sweep site --measure --real-map --only green-science [--keep-save green-science-1ps]   # 绿瓶站：矿 → 板 → 绿板 → 绿瓶，铁板经分离器分给两条链（阶段 8）
uv run --extra solver fgen ask site --product logistic-science-pack --rate 1 --out q.json   # 站点的判断题（选矿候选 + 事实、放置偏好）给 agent 回答
uv run --extra solver fgen sweep site --measure --real-map --only green-science --decisions config/decisions/haiku.json   # 按答案文件规划实测，行名 green-science-1@haiku
uv run fgen gen direct-chain --recipe electronic-circuit --rate 2   # 直插链：求解器周期 × k
uv run fgen gen direct-chain --rate 2 --layout baseline             # 手写经典 3:2 排法
uv run fgen gen direct-chain --rate 2 --extra-inserters long-handed-inserter   # 允许混长臂爪
uv run fgen gen direct-chain --rate 2 --backend cpsat                # CP-SAT 周期后端（需 --extra solver）
uv run fgen gen direct-chain --recipe automation-science-pack --rate 1 --backend cpsat   # 红瓶（齿轮走带的一条道）
uv run fgen sweep direct-chain                            # 写 characterization/direct_chain.{jsonl,md}
uv run --extra solver fgen gen direct-chain --recipe plastic-bar --machine chemical-plant --rate 2 --extra-inserters long-handed-inserter [--mirror]   # 塑料（管网由 CP-SAT 排）
uv run --extra solver fgen gen direct-chain --recipe sulfuric-acid --machine chemical-plant --rate 50   # 硫酸（水进、酸出管）
uv run --extra solver fgen sweep fluid                    # 写 characterization/fluid_chain.{jsonl,md}
uv run --extra solver fgen gen direct-chain --rate 2                      # 默认：全调色板、compact、随时可用（60 s 时间盒）
uv run --extra solver fgen gen direct-chain --rate 2 --preset cheap       # 预设：compact / cheap / simple
uv run --extra solver fgen gen direct-chain --rate 2 --prove              # 证明最小（慢，特征化用）
uv run --extra solver fgen gen direct-chain --rate 2 --palette given      # 只用 --inserter/--extra-inserters/--pole/--belt
uv run --extra solver fgen gen direct-chain --rate 2 --exclude substation,big-electric-pole   # 收窄调色板
uv run --extra solver fgen gen direct-chain --rate 2 --simulate           # 生成后在 headless 里实测（默认不起仿真）
uv run --extra solver fgen sweep palette                  # 写 characterization/palette.{jsonl,md}
uv run --extra solver fgen sweep solver-time [--only CASE,...] [--procs 1,4]   # 求解器耗时表 characterization/solver_time.{jsonl,md}（不起服务器）
uv run fgen decode <蓝图字符串或文件>                      # 美化 JSON
uv run fgen encode <json 文件>
uv run fgen render <蓝图字符串或文件> [--rulers]
uv run fgen validate <蓝图字符串或文件>
```

## headless 测试与实测

二进制位置：`$FACTORIO_DIR/bin/x64/factorio`（默认 `~/factorio`；`bin/headless.sh install` 下载，`bin/headless.sh dump` 按 `FGEN_MODS` 指定的 mod 组合导出，默认全开）。

```sh
uv run --extra solver pytest -m "not headless"     # 315 个，不启动服务器，约 11 分钟（CP-SAT 已装时直插链、流体测试用它）
uv run --extra solver pytest -m headless           # 64 个，需要二进制；约 3 分钟（4 核容器实测）
uv run fgen verify                  # VERIFY.md 的 9 项实机检查，JSON 一行一项，约 15 秒
uv run fgen sweep smelting --measure   # 18 组冶炼列实测写入 characterization/，约 2 分钟
uv run fgen sweep mining --measure     # 7 个画矿形状实测，约 30 秒
uv run fgen sweep mining --real-map    # 正常地图（config/ore-map-gen-settings.json）端到端，约 15 秒
uv run fgen sweep site --measure       # 平地整站仿真（M4），约 12 秒
uv run --extra solver fgen sweep site --measure --real-map   # 另加真实地图整站（M5：采矿→带→冶炼列）与红瓶站两档（阶段 7：两矿→两列→红瓶链，含两次 CP-SAT 求解与长预热），约 7 分钟
uv run --extra solver fgen sweep fluid --measure   # 11 行流体（塑料 7 + 硫酸 3 + 1 行不可行），约 7 分钟
uv run --extra solver fgen sweep palette --measure   # 17 行全调色板 × 预设（含两行证明驱动，约 20 分钟），实测约 40 分钟
uv run fgen sweep direct-chain --measure       # 25 行直插链（绿板求解器 vs 手写基线、红瓶、绿瓶；需 --extra solver），绿瓶周期求解约 30 分钟
```

### 把云端会话里的存档带回本地

容器是一次性的，`saves/` 又被 gitignore，所以存档默认不保留。两步带回：

```sh
uv run --extra solver fgen gen direct-chain --rate 2 --simulate --keep-save gc-2ps   # 单元留在地图里，存档保留为 saves/gc-2ps.zip
bin/publish-save.sh saves/gc-2ps.zip "绿板 2/s 直插链"                               # 提交到孤儿分支 saves 并推送
```

任何 `HeadlessServer` 在设了 `FGEN_SAVE_DIR` 时都会把 `/quit` 写出的 run save 复制到那个目录（文件名 `<地图>-<UTC 时间>.zip`），sweep、测试同样适用。本地客户端要有同一组 mod（Space Age DLC）和同一版本才能打开。


- 服务器：`HeadlessServer`（`fgen.sim.server`）在临时目录里建平地存档（`config/test-*.json`，mod 组合取 `DEFAULT_MODS`），`--start-server` 并等 RCON 就绪；退出时 `/quit`，超时再 terminate/kill，并用 `PR_SET_PDEATHSIG` 保证 Python 进程死掉时服务器也被杀。一次只起一个实例：pytest 每个测试模块一个服务器（`tests/conftest.py` 的 `server` fixture），按模块顺序运行。
- 时间一律按游戏 tick：测量时把 `game.speed` 调到 20–64 倍，用游戏内 `script.on_nth_tick` 计数与快照，Python 只轮询 `game.tick`。
- `fgen verify --attach PORT --password PW`、`fgen sweep smelting --measure --attach PORT` 可以复用一个已在运行的服务器。

| 模块 | 内容 |
| --- | --- |
| `fgen.sim.rcon` | 标准库 Source RCON；Factorio 每条命令恰好回一个包（任意长度） |
| `fgen.sim.server` | `HeadlessServer`：建存档、启动、等待就绪、停止、`set_speed`/`tick`/`wait_ticks` |
| `fgen.sim.lua` | `/sc` 片段模板（清场、读实体、导入并建造蓝图、导出蓝图、地形报告） |
| `fgen.sim.deployer` | `RconDeployer`：蓝图导入 + 复活 ghost 放置（`place_direct` 为 create_entity 对照），`read_back`/`clear` |
| `fgen.sim.harness` | 夹具即小 `Unit`（源：infinity chest → loader → 带；汇：带 → loader → 钢箱；电：energy interface + 杆；液体：infinity-pipe / 储液罐）；`plan(unit)` 给每个接口配夹具后交给路由器连上 |
| `fgen.sim.clear` | `clear_obstacles(rcon, area)`：销毁树、石头、地上物品、尸体并按类计数；资源、悬崖、水、玩家建筑不动 |
| `fgen.sim.site_measure` | 整站部署（单元+路由一张蓝图，夹具 `create_entity`）、按 tick 采样汇与物品统计、电网/储罐读数；平地与真实地图两个场景 |
| `fgen.sim.measure` | `RconCharacterizer.measure(unit, warmup_s, sample_s, samples, speed)` |
| `fgen.sim.verify` | VERIFY.md 条目的实机检查，`fgen verify` |
| `fgen.layout.compare` | 忽略顺序与浮点误差的布局比较（往返测试用） |
| `fgen.sim.terrain` | `RconTerrain`：资源、可建（瓦片碰撞层 vs 参考实体碰撞层）、水、已占格、`snapshot(area) -> Terrain`；`paint` 画矿、`set_tiles` 造湖 |
| `fgen.sim.mining` | 采矿单元实测：画矿 → 部署 → 每条带一个汇；正常地图上找最近铁矿端到端 |

所有子命令都接受 `--data data-raw-dump.json`（放在子命令前），用真实 dump 代替种子数据。

## 布局

| 模块 | 内容 |
| --- | --- |
| `fgen.data` | `schema.py` 镜像 `data.raw` 字段名的 dataclass；`loader.py` 的 `load_seed()` / `load_dump(path)` / `fields_used()`；`seed/vanilla-2.0.json` 手写种子数据（含引擎常数），**唯一允许出现数字的地方** |
| `fgen.geom` | 纯函数几何：`Direction`（16 向）/`Vec2`/`Tile`、占地、爪子取放格、电杆覆盖与拉线、带容量与带道、液体连接点、钻机搜索区/出料格/出料带道/产率（`mining`） |
| `fgen.layout` | `Placement`/`Interface`/`Layout`/`Unit`；平移/旋转/镜像；`validate()`（违例列表，不抛异常）；`to_ascii()` |
| `fgen.blueprint` | 字符串编解码、`unit_to_blueprint`、`blueprint_to_layout` |
| `fgen.gen` | `Generator` 协议与 `Infeasible`；`throughput`（解析吞吐模型、`predict`）；`smelting_row`（带供料行 → 冶炼列）；`sweep`（特征化）；`mining_patch`（钻机铺矿，锚定单元）；`mining_sweep`（采矿特征化）；`direct_chain`（配比周期的直插链）、`direct_chain_baseline`（手写经典 3:2 周期）、`direct_chain_sweep` |
| `fgen.solve` | `period`：周期（`Period`）、边界条件 `check_period`、平铺 `tile`、配比与速率 `flows`/`ratio`；`cycle`：周期内穷尽搜索 `solve(spec, gd)`（阶段 4a）；`cpsat`：CP-SAT 后端（阶段 4b，可选依赖）；`cpsat_fluid`：周期内管网的约束（阶段 5a） |
| `fgen.terrain` | `model`：`Terrain` 快照（可建/资源/水/已占）、4 邻接矿区分块 `patches`、`nearest_patch`、渲染背景；`paint`：矩形/L 形/挖洞/随机 blob 形状 |
| `fgen.sim` | 服务器相关：协议与 `NotAvailable` 默认值、RCON、部署、实测（见上节） |
| `fgen.site` | `model`：`Site` = `Terrain` + `PlacedUnit` 列表 + `Link`（按 (单元名, 接口序号) 连接），占格合并、世界坐标接口；`place`：链路驱动放置 `candidates` / `place_linked`（候选按估算带长 + 转弯 + 绕回 + 占资源格排序，取第一个放得下的）、螺旋找空位 `find_spot`、按接口落点放置 `place_port`；`build`：`SitePlan`（锚定单元 + 自由单元 + 链路）→ 顺流顺序逐个放置 → 路由，路由失败就把单元移到下一个候选 |
| `fgen.route` | 纯函数路由：`grid`（可走格、端口保留带、喂入/爪子可达格、液体口、地下段）、`astar`（转向代价 + 跳跃边）、`belt`（直连、侧向汇入桩、地下带、道材料/速率检查）、`pipe`（管 + 地下管、邻接隔离）、`power`（MST + 杆链）、`connect(site) -> Connection`（先带后管再电，失败换序重试，整站 validate + 契约校验） |

## 约定（摘要，全文见任务书 §3）

- x 向东、y 向南；瓦片 `(tx, ty)` 是左上角在该点的 1×1 格；实体 `position` 是中心。
- `Direction` 是 2.0 的 16 向整数（N=0, E=4, S=8, W=12）；顺时针 90° 把 `(x, y)` 变为 `(-y, x)`。
- N 向爪子从北边取、往南边放；带道 left/right 相对流向（东向带 left 在北）；爪子放带落远道（均已在 2.0.76 与 2.1.23 实机核对）。
- 导出的蓝图写出电杆铜线 `wires`（2.0.76 / 2.1.23 都不会给蓝图里的电杆自动连线）。
- 生成器 `generate()` 要么返回通过 `validate()` 的 `Unit`，要么抛 `Infeasible(reason, suggestions)`；`suggestions` 是经过可行性检验的具体参数改法。

## 冶炼列生成器

参数见 `fgen.gen.smelting_row.SmeltingRowParams`。炉数、行长、行偏移（由爪子取放向量推出）、炉步长（由占地推出）、电杆行数与间隔（由 `supply_area_distance` 与 `maximum_wire_distance` 推出：一排电杆的供电区能覆盖上下两排爪子就只放一排，否则两排都放）全部从游戏数据派生。换电杆或熔炉只改参数。5 / 7.5 / 15 板/秒的渲染见 `characterization/smelting_row.md`。

## 采矿生成器（锚定单元）

`fgen.gen.mining_patch.generate(params, gd, patch, terrain)`：输入是 `Terrain` 快照与其中一个 `Patch`（生成器不碰 RCON；单元测试用 `Terrain.from_resources` 手写地形），输出的 `Unit` 用**世界坐标**（部署原点 (0,0)），`meta["anchor"]` 记录矿种、矿区边界、总矿量、钻机数、总产率与粗略枯竭时间。

- 排法：沿 `axis` 铺带子行，两侧各一排钻机面向带子（出料格由 `vector_to_place_result` 推出，实测落在钻机一侧的近道），带子周期 = 两排钻机 + 1；带子行的 y 相位逐个试。钻机沿带子不再落在固定网格上：每条带每侧按"最多不重叠钻机"排满（区间调度）。带子统一流向 `flow` 一端，每条带末端是一个输出接口（`lane` 两侧都有钻机时为 `both`，`rate` = 钻机数 × `mining_speed / mining_time`）；不合流。
- 电杆（阶段 6a）：钻机排定后再放。杆位 = 条带里的空地（带两端、矿区缺口），以及杆为 1 格深且带有同级地下带时的**带上**（杆两侧一对地下带从杆下穿过，钻机可以往地下带入口/出口上出料，已实测）；只用供电区能同时碰到两侧钻机行的杆位（没有就每侧一条链）。每条带用 DP 取最省的杆链（杆数、其次地下带对数），优先能连上上一条带的链；仍连不上的（如大钻周期 11 > 中杆拉线 9）用 `route.power.chain` 在空地上补杆桥接。只有找不到链时才让出钻位（`pole_slot`）；`meta["drills_before_poles"]` 是不放杆时这些带能放的钻机数。
- 每条道只装一侧钻机：超过道容量时从上游端截断，记进 `meta["dropped_drills"]`（原因 `lane_capacity`），`meta["suggestions"]` 给出能保住它们的带等级或换轴向。其他原因：`pole_slot`、`belt_blocked`（带子要穿过不可建格）、`max_belts`、`low_ore`（`min_ore_amount_per_drill`）、`mixed_resource`（搜索区里有别的矿，`allow_mixed=False`）。
- 不变量：`layout.validate` 加 `drill.output_blocked`、`drill.no_ore`（给了 `resources` 时），生成器另查 `terrain.unbuildable`。渲染时 `:` 是矿、`,` 别的矿、`~` 水、`x` 不可建。
- 实测：画矿的 8 个用例（7 个形状 + 大钻配中杆）每条带实测产率与契约完全一致（0% 偏差）；真实地图 seed 20261024 最近铁矿（352 格）42 台钻（阶段 2 为 32）、5 条带，契约 21/s，实测 21/s。改进前后对比见 `characterization/mining_patch.md` 的 "Before vs after"，实验记录见 `docs/experiments/6a-task1.md`。

## 多单元站点与放置（阶段 7 任务 A）

`fgen.site.build.plan_and_connect(SitePlan)`：锚定单元（采矿）带世界坐标进来，自由单元（冶炼列、直插链、夹具）按**顺流顺序**（一个单元的非夹具生产者都放好了才轮到它；再按非夹具优先、已放邻居多、链路速率大）逐个放到"到已放邻居口的估算带长最便宜"的位置（`site.place.candidates`：曼哈顿长 + 每个强制转弯 + 绕回 + 每个占资源格的惩罚，只在放得下的候选里取最便宜；没有锚定单元时最大的单元放在站点原点）。路由失败时把失败链路里后放的单元移到下一个候选再试（有界）。场景（`gen/site_sweep.py`）只描述单元与链路，不含坐标与锚点；每一级的规模由下一级的契约定：冶炼列按直插链输入口的 `MinRate`，矿区按冶炼列矿口的 `MinRate`（`max_belt_rate`）。

红瓶站（`red-science-0.5` / `red-science-1`）：真实地图最近铁矿与铜矿 → 两条一带矿区 → 两条冶炼列 → 红瓶直插链（铜板侧向汇入链的铜带右道，铁板直连铁带；时间盒不同时求解器也可能给"两种板共一条带两道"的周期，路由器两种都接）→ 汇；煤与电由夹具给。`site_sweep.predict` 按链路拓扑两遍算稳态流量（正向产能、反向按下游取量），`fill_time` 估计各级缓冲（链路带 + 消费单元自己的带）灌满的时间，实测预热 = 240 s + 1.25 × 它；同一物料被几个单元消耗时按同一个力统计计数器合并判定。实验记录：`docs/experiments/7-red-science-site.md`。

## 连接路由（阶段 3a）

`fgen.route.connect.connect(site, gd)` 只读接口契约，不回头查单元布局里的端口实体（布局只用来标出哪些格被占），输出 `Connection`：路由实体（`layout`）、每条链路的明细、契约校验（`LaneMaterial` 必须满足，否则 `Infeasible`；`MinRate` 不足记警告；`Compressed` 只记录）、整站 `validate`。契约见 `fgen.layout.model`（阶段 6a 补全，字段设计写在 `docs/plan-phase3a.md` §6）：

- `Interface.entity`：端口下的实体（带等级 → 桩用什么带、道容量、生产者带比消费者慢的警告；电接口的杆种 → 拉线距离、杆中心）。
- `RateProfile`：`supply="fixed"`（`initial` = `rate`，`lanes` 按道给出、相加等于 `initial`，采矿口另有 `depletion_s_lower_bound`）或 `supply="on_demand"`（要多少给多少，`limit` 为上限，`lanes` 为每道上限；`rate` 为 None）。`rate=None` 不再表示"无限供给"。构造用 `RateProfile.fixed(...)` / `RateProfile.on_demand(...)`；`Interface.fixed_rate()`、`lane_rate(lane)`、`on_demand()`。
- `Interface.port_group` + `Unit.groups`（`PortGroup(name, requires)`）：同一条带上的口归一组，带道要求（`LaneMaterial`）挂在组上；路由器按组（不是按格）把口归成一个消费者、共用一个汇入桩。
- `Interface.space`（`PortSpace(approach, upstream)`）：允许的进入方式（`direct` / `side` / `both`）和口前要留的直线空格数；路由器按它决定直连还是侧向汇入、保留口前格，放置规则按它留空（`PortSpace.item(approach)`：直连 2 格，侧向汇入 = 2 格桩 + 1 格）。
- 不变量 `contract.entity` / `contract.space` / `contract.rate` / `contract.group`（`validate_unit` 里）检查这些字段与单元一致；两个生成器与全部夹具都填全字段并通过。
- 带：从生产者口前一格出发；消费者口 `lane` 为 `both` 时直连（最后一节从正后方进入），为 `left`/`right` 时在消费者上游补 2 格直带“桩”，从该道一侧垂直汇入（东向带填左道从北侧汇入）。地下带跳跃 2..`max_distance`，不在别人的地下段里落脚，不在爪子/钻机可达格和别人喂入的格上铺带。
- 管：管 + 地下管（偏移 ≤ `max_underground_distance`），管旁不能有异种液体口。
- 电：所有电接口按欧氏 MST 连，每条边 A* 找杆链（每跳 ≤ 两端较小的拉线距离）。
- 实测（`characterization/site.md`）：平地 7.5/s 冶炼列由两个源夹具侧向汇入矿（左）和煤（右），板 7.5/s；真实地图最近铁矿一条带 7.5/s（`max_belt_rate` 限到冶炼列矿道）→ 带 → 冶炼列，板 7.467/s（99.6%）。

## 直插链生成器（阶段 4a）

`fgen.gen.direct_chain.generate(DirectChainParams(recipe, target_rate, machine, inserter, belt, pole, extra_inserters, layout))`：配方里能被同一台组装机造出来的原料（绿板的铜线）是中间产物，机器到机器由爪子直插，不上带。精确配比由配方与制造速度约分（绿板 3:2）；周期类型 `a':b'`（b' ≤ b，a' = ⌈b'·a/b⌉，略过剩写进契约）× 个数 k 取机器最少者，放不下时依次退到下一种。

- 周期（`fgen.solve.period`）：`W×H` 矩形，沿 x 以步长 W 平铺。边界条件：足迹在矩形内、带行每格同向同级、爪子取放在本周期内、本周期的杆供本周期的电、杆连通且能连到下一周期的像。
- 求解器（`fgen.solve.cycle.solve`）：尺寸（面积升序）→ 带骨架 → 机器锚点 → 电杆（迭代加深，下界 ⌈W/拉线⌉）→ 爪子（只在杆供到的空格里选，最大流检查铜线分配）；`SolveStats` 给出每层节点数和每条约束剪掉的分支数。零第三方依赖；3:2 单等级约 1.5 s，混长臂爪约 23 s。
- 结果（`characterization/direct_chain.md`）：单等级时最小周期就是经典的 19×7（求解器证明 133 以内没有更小的），杆比手写少（小杆 3 vs 4）；允许长臂爪时 9×12 = 108。R = 1、2、4 及各等级变体实测全部达标（0% 偏差）。实验记录：`docs/experiments/4a-cycle-solver.md`。
- 实测前要解锁配方（2.1 + space-age 开局铜线、绿板未解锁，VERIFY §11），`RconCharacterizer` 自动做。

## 流体机器（阶段 5a）

化工厂配方（塑料：石油气 + 煤；硫酸：硫 + 铁 + 水 → 硫酸）仍走 `direct_chain`（单阶段周期，`--machine chemical-plant`），只能用 CP-SAT 后端（穷尽搜索拒绝并提示 `backend='cpsat'`）。

- 几何（`fgen.geom.fluid`，VERIFY §13 实测）：连接点先镜像后旋转；配方的液体按类进盒，同类只有一种液体时该类所有盒都装它（塑料两个输入角都是气），没用到的盒闲置；`validate` 把闲置口对着流体连接也算 `fluid.unintended_adjacency`（保守），另有 `fluid.box_unconnected`、`fluid.underground_unpaired`、`contract.fluid`。
- 求解器（`fgen.solve.cpsat_fluid`）：周期里任意空格可放直管与地下管对，机器朝向是变量（镜像是参数 `mirror`），每种液体在周期内连成一个网并在 (0, y)/(W−1, y) 跨周期；整行母线只是它的一个解。硫酸必须用这种管网：整行母线会把化工厂上下两面都堵死。
- 契约：液体口带 `rate`、`MinRate`、`temperature`、`segment_extent`（单元内这段管网的外包长边）；2.1 段内流量与管长无关，但一段外包超过 `pipeline_extent`（320）就不流，生成器与路由器都检查。
- 结果（`characterization/fluid_chain.md`，全科技实测全部 0% 偏差）：塑料 1/2 棒/秒 3×7（快速 + 长臂，2.8 s 证明最小），4 棒/秒 3×13 两台；硫酸一台 8×5（50/s）、两台 11×7（100/s），输出口实测 = 契约。实验记录：`docs/experiments/5a-fluids.md`。

## 随时可用求解、目标预设、组件调色板（阶段 4c）

- **调色板**（`DirectChainParams.palette`，默认 `"full"`）：种子里每种可用的爪子（电动、1×1）、电杆、带与同速地下带都可以用，`inserter`/`pole`/`belt` 只是首选等级；`exclude` 收窄，`palette="given"` 回到"指定等级（+ `extra_inserters`）"。被支配的项（同取放格、吞吐不高、更贵：无研究时的集装爪对快速爪）自动去掉。每个槛位的等级是变量，可不用；带等级按行选，道容量随之约束；带行里可以有本行等级的地下带对（中间的格子空出来放别的）。
- **预设**（`preset`）：硬约束不变，排序不同。`compact` 面积 → 杆数 → 爪数 → 实体总成本（末项只裁决平手：面积、杆、爪都相同时取便宜的等级，不会为省钱多占一格；`prove=True` 的证明驱动保持 4b 目标，模型本身不裁决平手，只按此序排候选；穷尽搜索后端单等级，无平手可裁）；`cheap` 实体总成本 → 面积（成本由 `fgen.data.cost` 从种子配方递归到原料，每单位原料记 1）；`simple` 实体种类数 → 面积。
- **随时可用**（默认）：先在解析上界的宽松尺寸给出首个可行周期（先只用首选等级），再在时间盒（`time_limit`，按 CP-SAT 确定性时间折算，同一规格总给同一个周期）内逐高度二分收紧宽度、优化预设目标；结果标 `proven`、各阶段耗时（`phases`），时间盒内无解且未证明标 `unproven_infeasible`。`prove=True`（`--prove`）是证明最小的驱动，只给特征化表用；穷尽搜索后端只支持它（且只用给定等级）。
- **仿真什么时候跑**：`fgen gen` 默认不起服务器（`--simulate` 显式触发），sweep 的 `--measure` 不变。只有两种情况需要仿真：(1) **新实体类型或新结构词汇**（例如本阶段带行里的地下带对、变电站）——解析预测从没对它们核对过，仿真的价值是发现缺失的不变量；(2) **轮末回归**。其余时候循环是：可行解 → `validate` → 解析预测（六轮实测偏差 ≤ 2%）。
- 结果（`characterization/palette.md`，17 个单元全部实测）：已有 23 个规格的首个可行解除绿瓶（52.7 s）外全部 ≤ 6.4 s，`prove=True` 结果与 4b/5a 逐项相同；只给全调色板、不提示长臂爪，3:2 绿板证明最小是 **9×11 = 99**（变电站压在带行的地下带对上，1086 s），排除 4a 之后的词汇（中/大杆、变电站、地下带）时自己得出 **9×12 = 108**（92.5 s）；60 s 盒里全调色板只给到首选等级的结果（133）。`cheap` 在平手多的地方最值（硫酸同一个 8×5：92.5 vs 503.5 原料单位），`simple` 把绿瓶从 7 种实体减到 4 种。
- 实验记录：`docs/experiments/4c-anytime-palette.md`；特征化：`characterization/palette.md`。

## 测试样本

`tests/fixtures/` 里 3 条 2.0 蓝图字符串取自 MIT 许可的 factorio-draftsman 仓库，另有 3 条由 headless 2.0.76 游戏自己导出，来源见 `tests/fixtures/SOURCES.txt`。
