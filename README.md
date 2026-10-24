# fgen — Factorio 蓝图生成器库（阶段 3a：清障、夹具泛化与单元间连接）

agent 的产出是**生成器参数或生成器代码**，不是坐标。总体方案见 `docs/design.md`，任务书见 `docs/plan-phase0.md`（无服务器底座）、`docs/plan-phase1.md`（headless 测试场）、`docs/plan-phase2.md`（采矿锚定单元）与 `docs/plan-phase3a.md`（清障、夹具、连接路由；末尾“结果”一节是接口契约够不够用的结论），数值与约定的核对状态见 `docs/VERIFY.md`。

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
uv run fgen gen mining --case l-shape [--pole medium-electric-pole --axis y ...]   # 画好的矿区上铺钻机
uv run fgen sweep mining                                  # 写 characterization/mining_patch.{jsonl,md}
uv run fgen sweep site                                    # 平地整站（规划+路由）写 characterization/site.{jsonl,md}
uv run fgen decode <蓝图字符串或文件>                      # 美化 JSON
uv run fgen encode <json 文件>
uv run fgen render <蓝图字符串或文件> [--rulers]
uv run fgen validate <蓝图字符串或文件>
```

## headless 测试与实测

二进制位置：`$FACTORIO_DIR/bin/x64/factorio`（默认 `~/factorio`；`bin/headless.sh install` 下载，`bin/headless.sh dump` 按 `FGEN_MODS` 指定的 mod 组合导出，默认全开）。

```sh
uv run pytest -m "not headless"     # 209 个，不启动服务器，约 25 秒
uv run pytest -m headless           # 52 个，需要二进制；约 110 秒（4 核容器实测）
uv run fgen verify                  # VERIFY.md 的 9 项实机检查，JSON 一行一项，约 15 秒
uv run fgen sweep smelting --measure   # 18 组冶炼列实测写入 characterization/，约 2 分钟
uv run fgen sweep mining --measure     # 7 个画矿形状实测，约 30 秒
uv run fgen sweep mining --real-map    # 正常地图（config/ore-map-gen-settings.json）端到端，约 15 秒
uv run fgen sweep site --measure       # 平地整站仿真（M4），约 12 秒
uv run fgen sweep site --measure --real-map   # 另加真实地图整站（M5：采矿→带→冶炼列），约 30 秒
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
| `fgen.gen` | `Generator` 协议与 `Infeasible`；`throughput`（解析吞吐模型、`predict`）；`smelting_row`（带供料行 → 冶炼列）；`sweep`（特征化）；`mining_patch`（钻机铺矿，锚定单元）；`mining_sweep`（采矿特征化） |
| `fgen.terrain` | `model`：`Terrain` 快照（可建/资源/水/已占）、4 邻接矿区分块 `patches`、`nearest_patch`、渲染背景；`paint`：矩形/L 形/挖洞/随机 blob 形状 |
| `fgen.sim` | 服务器相关：协议与 `NotAvailable` 默认值、RCON、部署、实测（见上节） |
| `fgen.site` | `model`：`Site` = `Terrain` + `PlacedUnit` 列表 + `Link`（按 (单元名, 接口序号) 连接），占格合并、世界坐标接口；`place`：螺旋找空位 `find_spot`、按接口落点放置 `place_port` |
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

- 排法：沿 `axis` 铺带子行，两侧各一排钻机面向带子（出料格由 `vector_to_place_result` 推出，实测落在钻机一侧的近道），带子周期 = 两排钻机 + 1；网格相位（周期 × 钻机宽）逐个试，取钻机最多的。带子统一流向 `flow` 一端，每条带末端是一个输出接口（`lane` 两侧都有钻机时为 `both`，`rate` = 钻机数 × `mining_speed / mining_time`）；不合流。
- 电杆放在钻机行里，占一个钻位：先找槽内能同时覆盖两侧钻机行、且隔一个周期仍在拉线距离内的位置（只放一侧），否则两侧都放；沿带子的槽位由供电区与拉线距离的贪心扫描决定，各带子行用同一组槽位以便纵向连线。
- 每条道只装一侧钻机：超过道容量时从上游端截断，记进 `meta["dropped_drills"]`（原因 `lane_capacity`），`meta["suggestions"]` 给出能保住它们的带等级或换轴向。其他原因：`pole_slot`、`belt_blocked`（带子要穿过不可建格）、`max_belts`、`low_ore`（`min_ore_amount_per_drill`）、`mixed_resource`（搜索区里有别的矿，`allow_mixed=False`）。
- 不变量：`layout.validate` 加 `drill.output_blocked`、`drill.no_ore`（给了 `resources` 时），生成器另查 `terrain.unbuildable`。渲染时 `:` 是矿、`,` 别的矿、`~` 水、`x` 不可建。
- 实测：画矿的 7 个形状每条带实测产率与契约完全一致（0% 偏差）；真实地图 seed 20261024 最近铁矿（352 格）32 台钻、5 条带，契约 16/s，实测 16/s。见 `characterization/mining_patch.md`。

## 连接路由（阶段 3a）

`fgen.route.connect.connect(site, gd)` 只读接口契约（格、方向、带道、物料、速率、`requires`）和端口下的实体（带等级、杆种），输出 `Connection`：路由实体（`layout`）、每条链路的明细、契约校验（`LaneMaterial` 必须满足，否则 `Infeasible`；`MinRate` 不足记警告；`Compressed` 只记录）、整站 `validate`。契约升级见 `fgen.layout.model`：`RateProfile(initial, depletion_s_lower_bound)`、`LaneMaterial`、`MinRate`、`Compressed`、`Interface.world(origin, Pose)`、`PlacedUnit`。

- 带：从生产者口前一格出发；消费者口 `lane` 为 `both` 时直连（最后一节从正后方进入），为 `left`/`right` 时在消费者上游补 2 格直带“桩”，从该道一侧垂直汇入（东向带填左道从北侧汇入）。地下带跳跃 2..`max_distance`，不在别人的地下段里落脚，不在爪子/钻机可达格和别人喂入的格上铺带。
- 管：管 + 地下管（偏移 ≤ `max_underground_distance`），管旁不能有异种液体口。
- 电：所有电接口按欧氏 MST 连，每条边 A* 找杆链（每跳 ≤ 两端较小的拉线距离）。
- 实测（`characterization/site.md`）：平地 7.5/s 冶炼列由两个源夹具侧向汇入矿（左）和煤（右），板 7.5/s；真实地图最近铁矿 15 台钻 7.5/s → 带 → 冶炼列，板 7.456/s（99.4%）。

## 测试样本

`tests/fixtures/` 里 3 条 2.0 蓝图字符串取自 MIT 许可的 factorio-draftsman 仓库，另有 3 条由 headless 2.0.76 游戏自己导出，来源见 `tests/fixtures/SOURCES.txt`。
