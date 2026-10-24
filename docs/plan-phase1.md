# 阶段 1 实施任务书：headless 测试场与冶炼列实测

接续 `docs/plan-phase0.md`。阶段 0 的代码已完成并由真实 `--dump-data` 核对过数据；本阶段把"需要服务器"的部分补上：RCON 部署器、headless 测试场、往返测试、冶炼列实测与特征化表的 `measured` 列，并把 `docs/VERIFY.md` 里能实机核对的条目核掉。本文与 `design.md` 冲突时以本文为准。

## 1. 环境事实

- Factorio headless 2.0.76 已装在 `$FACTORIO_DIR`（默认 `~/factorio`，钩子已设环境变量）；`bin/headless.sh path` 给出二进制路径。没有图形界面，只能靠 RCON。
- 本容器 4 核 15 GB，可以运行一个 headless 服务器实例，`game.speed` 可以调高加速仿真，具体能跑多快自己测。
- Lua 运行时 API 文档在 https://lua-api.factorio.com/2.0.76/ （可访问）。机器可读版可用 `git show master:files/runtime-api.json > /tmp/runtime-api.json` 取出后 grep，这只是查文档，不是复用 master 的代码。
- 官方维基可访问（wiki.factorio.com），`Console`、`Inserters`、`Blueprint string format` 等页面有用，但注意维基部分页面仍是 1.1 内容。
- 没有玩家角色。一切放置都是"上帝建造"：脚本直接 `create_entity` 或导入蓝图后复活 ghost。

## 2. 技术约定（沿用阶段 0）

- 只用 `uv`；核心库零第三方运行时依赖（RCON 用标准库 `socket` 实现 Source RCON 协议）。
- 需要服务器的测试打 `@pytest.mark.headless` 标记；二进制不存在时自动 skip，`uv run pytest -m "not headless"` 必须保持全绿且不启动服务器。
- 任何游戏数字仍只在种子数据里；实测结果写入 `characterization/*.jsonl` 的 `measured` 列，不进代码。
- 每个里程碑 `uv run pytest`、`ruff check`、`ruff format --check` 全绿后提交，不 push，不建 PR。

## 3. 模块

```
src/fgen/sim/
  rcon.py        Source RCON 客户端
  server.py      HeadlessServer：建存档、启动、等待就绪、停止
  lua.py         Lua 片段模板（放置、读回、清场、统计）
  deployer.py    RconDeployer：实现 protocol.Deployer
  harness.py     给 Unit 的接口接无限源 / 无限汇 / 无限电
  measure.py     RconCharacterizer：实现 protocol.Characterizer
  verify.py      针对 VERIFY.md 条目的实机检查脚本
config/
  test-map-gen-settings.json   平地测试场
  test-map-settings.json
  test-server-settings.json
```

### 3.1 `rcon.py`

Source RCON：TCP，包 = `int32 size | int32 id | int32 type | body\0\0`，先 `SERVERDATA_AUTH(3)`，再 `SERVERDATA_EXECCOMMAND(2)`。Factorio 的响应可能超过单包 4096 字节，要正确拼接多包响应（常用做法：命令后紧跟一个空的 `SERVERDATA_RESPONSE_VALUE(0)` 包，等它的回显作为结束标记；若 Factorio 不支持该标记，就按 `id` 收集直到读超时）。提供 `RconClient.command(str) -> str` 和 `lua(code: str) -> str`，后者发 `/sc <code>`。Lua 返回值统一用 `rcon.print(helpers.table_to_json(...))`（2.0 里 `game.table_to_json` 移到了 `helpers`，请以 API 文档为准）。

### 3.2 `server.py`

`HeadlessServer(factorio_dir, workdir, port, password)` 上下文管理器：

1. 没有存档就 `factorio --create <save> --map-gen-settings config/test-map-gen-settings.json --map-settings config/test-map-settings.json`。测试场要求：无水、无资源、无悬崖、无树、无敌人、`peaceful_mode`，地块全部可建。方法是把 `autoplace_controls` 里各项 `size` 设 0（或 `"none"`，以 2.0 example 文件为准），`cliff_settings.richness` 0，必要时启用 `property_expression_names` 把 elevation 设成常数（查阅 `data/map-gen-settings.example.json` 的注释）。在存档建好后用 RCON 验证：以原点为中心 200×200 格内 `find_tiles_filtered{collision_mask=...}` 没有不可建地块，没有任何实体。
2. `--start-server <save> --rcon-port <port> --rcon-password <pw> --server-settings config/test-server-settings.json`（关闭 `visibility.public/lan`、`require_user_verification: false`、`auto_pause: false`——否则无人在线会暂停，`autosave_interval` 设大）。等日志出现 RCON ready 后再返回。
3. 退出时优雅停止（`/quit` 或 SIGTERM），超时再 kill。清理临时存档可选。
4. 提供 `set_speed(x)`（`game.speed`）与 `tick()` 读取。

### 3.3 `deployer.py`

`RconDeployer` 实现 `protocol.Deployer`：

- `place(unit, origin)`：**主路径走蓝图**：`unit_to_blueprint` → `encode` → Lua 里 `game.create_inventory(1)[1].import_stack(s)`，`build_blueprint{surface, force, position, force_build=true, raise_built=false}` 得到 ghosts，逐个 `revive()`（或 `silent_revive`）。这样阶段 0 的导出器真正被游戏验证。再提供 `place_direct(layout, origin)`：逐实体 `surface.create_entity{name, position, direction, force, type=..., recipe=...}`，作为对照与排错手段。
- `read_back(area) -> Layout`：`find_entities_filtered{area}`，读 `name, position, direction, type`（地下带的 input/output，2.0 字段名以 API 为准）、`recipe`。坐标减去 origin 换回单元坐标。
- `clear(area)`：销毁区域内所有实体。
- `fgen.layout` 里加 `Layout.normalized()`/比较工具，使"读回的 Layout 与原 Layout 相等"的判断忽略实体顺序和浮点误差；不可旋转实体（熔炉、箱子、管道）读回的 direction 可能是 0，比较时按实体类型忽略。这个"哪些类型忽略方向"的规则放在几何库而不是测试里。

### 3.4 `harness.py`

把 `Unit.interfaces` 接到无限资源上，全部用脚本可放置的实体：

- **物品输入**（带，带道有要求）：在接口格上游延长同种带若干格，用**侧向汇入**分道供料：从北侧侧向汇入的带只填主带的左道（东向时），从南侧填右道。每条喂料带由 `infinity-chest`（设 `infinity_container_filters`，模式 `exactly`，数量充足）经隐藏的 `loader` / `fast-loader` / `express-loader` 实体（与带速匹配）送出。先在 `verify.py` 里确认 loader 在 2.0.76 base 里可用并与带速匹配；不行就改用多个 `bulk-inserter` 并通过 `force.bulk_inserter_capacity_bonus` 把手持量调满。
- **物品输出**：接口格下游延长带，末端 loader 进一个普通 `steel-chest`（不要用会自动删物品的 infinity chest，否则没法计数）。每个采样周期读 `get_inventory(defines.inventory.chest).get_contents()` 后清空，得到该周期流量。箱子容量要够一个周期。
- **电**：`electric-energy-interface`（隐藏实体，无限电）+ 一根与单元电杆同类的电杆，放在单元电接口格旁并保证拉线距离内；检查电网确实连通（读单元内任一爪子的 `energy` 或 `electric_network_id`）。
- 燃料走输入带的指定道，由上面的分道供料提供。
- harness 自身的占地要避开单元占地，并在 `read_back` 比较时被排除（记录 harness 放置的实体列表）。

### 3.5 `measure.py`

`RconCharacterizer.measure(unit, *, warmup_s, sample_s, samples, speed) -> dict`：

- 部署 unit + harness，`game.speed = speed`，等待 warmup（游戏时间，按 tick 计，不按墙上时间），然后采样 N 个周期，每周期读汇箱计数并清空。
- 返回：`throughput`（稳态中位数，物品/秒游戏时间）、`startup_ticks`（输出首次出现的 tick）、`samples`、以及饥饿诊断：逐炉读 `get_inventory(defines.inventory.furnace_source)` 是否为空、`status`（`defines.entity_status`），汇总成"第 k 台输入饥饿比例"。
- `fgen sweep smelting --measure` 把结果写进 `characterization/smelting_row.jsonl` 的 `measured`，`.md` 增加预测 vs 实测对比表。先用小网格（石炉/钢炉 × 小杆/中杆 × 5/7.5），再按时间扩大。

### 3.6 `verify.py` 与 `fgen verify`

针对 `docs/VERIFY.md` 里"实机核对"的条目各写一个检查，输出结论而不是替人判断。至少：

1. **爪子方向语义**：放两只箱子 A(北) B(南)，中间 `direction=0` 的爪子，A 放铁板，跑 120 tick，看铁板去了哪。再对 E/S/W 各做一次。结论决定 `geom/inserter.py` 的注释和 VERIFY 该行。
2. **蓝图不写 `wires` 时电杆是否自动连线**：导入只有两根相距在拉线距离内的小杆的蓝图，读 `electric_network_id` 是否相同。
3. **地下带**：导出一对游戏放置的地下带，看蓝图里 `type` 取值与 `direction` 是否等于流向；测最远配对距离。
4. **带道编号**：东向带北侧道放物品，读 `get_transport_line(1/2)` 哪条有物品，核对 `left/right` 约定。
5. **N 向 direction=0 显式写出、icons 写 type 字段**：导入生成的蓝图无报错。
6. **爪子基础手持量**：无科技时各爪子箱到箱每次搬几个（含 bulk-inserter）。
7. **loader / electric-energy-interface 可用性**：能否 `create_entity`，loader 吞吐是否与带速匹配。
8. **带速 ×8**：满压缩黄带 60 秒流量。

每条结果以 JSON 打印，并在 VERIFY.md 对应行把"把握"列改成"已核对（2.0.76）：<结论>"。结论与现有代码假设相反时，**改代码并说明**，不要只改文档。

## 4. 里程碑与验收

| # | 内容 | 验收 |
| --- | --- | --- |
| M1 | `rcon.py` + `server.py` + 测试场配置 | headless 测试：启动、`/sc rcon.print(game.tick)` 有返回、200×200 无实体无不可建地块、停止干净无残留进程 |
| M2 | `deployer.py` + `Layout` 比较工具 | **阶段 0 完成判据**：冶炼列 Unit 与 3 个手写布局（含地下带、不同方向爪子、电杆）经蓝图导入放置后 `read_back` 与原布局相等 |
| M3 | `verify.py` + `fgen verify` | 8 项检查都有结论，VERIFY.md 对应行更新；代码假设有错的已改并有测试 |
| M4 | `harness.py` + `measure.py` + `fgen sweep --measure` | 5、7.5（黄带石炉/钢炉）实测 ≥ 目标；15（红带）实测 ≥ 目标；`.jsonl` 有 `measured`，`.md` 有预测 vs 实测表；预测与实测偏差超过 10% 的要在 `.md` 里解释原因 |
| M5 | 收尾 | README 增加 headless 测试的运行方式与耗时；`docs/VERIFY.md` 剩余条目列清楚；`fgen.sim.protocol` 的 NotAvailable 占位保留作为无服务器时的默认 |

M1–M3 必须完成；M4 尽力；M5 收尾必做。

## 5. 不要做的事

- 不做采矿锚定单元、不做选址、不做单元间连接（阶段 2、3）。
- 不改生成器的排法去"凑"实测结果；实测不达标就记录并解释，让生成器的问题暴露出来。
- 不在测试里写死任何游戏数值。
- 不要同时起多个服务器实例。
