# 待实机核对清单（VERIFY）

阶段 0 时本环境没有 Factorio 二进制，下表内容原本都是**凭记忆**写下或由此推出的。阶段 1 起 headless 2.0.76（后迁移到 2.1.23）可用：标"已核对（2.0.76）"的行由 `uv run fgen verify`（`src/fgen/sim/verify.py`，测试 `tests/test_sim_verify.py`）或往返测试（`tests/test_sim_deployer.py`）在游戏里核对过，其余仍按"核对方法"待核。实机核对时只启用 `base` 模组（见 §0）。

"把握"一列是作者自评：**高** = 很确定（多年未变的基础值）；**中** = 记得 1.1 的值，2.0 可能改过；**低** = 只记得大概，或 2.0 格式细节不确定。

通用核对方法（下文简称 *dump 对比*）：在 headless 上执行 `factorio --dump-data`，用 `fgen.data.loader.load_dump(".../script-output/data-raw-dump.json")` 加载，与 `load_seed()` 按 `fields_used()` 列出的字段逐项对比（写个一次性脚本打印差异即可）。

## 0. 实机环境

- 测试服务器与数据 dump 启用 base + space-age + quality + elevated-rails + recycler（2.1 新增的内置 mod），即项目的目标 mod 组合；`FGEN_MODS` 可改。对种子集里的实体，base 与全开组合的原型没有差异，只有配方的 `categories` 不同。
- 平地测试场（`config/test-map-gen-settings.json`）：只把 water 的 `size` 设 0 仍会有湖，需要把 `property_expression_names.elevation` 固定为常数；2.0 的石头是单独的 `rocks` 自动放置项。建图后用 RCON 核对原点 200×200 内无阻挡地块、无实体（`tests/test_sim_server.py`）。
- RCON、`/sc` 与服务器启动的实测细节写在 `src/fgen/sim/rcon.py`、`src/fgen/sim/server.py` 的 docstring 里。

## 1. 种子数据（`src/fgen/data/seed/vanilla-2.0.json`）

**已核对（2.0.76，2.1.23 重新生成）。** 种子文件现在由 `scripts/extract_seed.py` 从真实 `--dump-data` 生成（`_provenance` 记录来源与版本），不再是手写数据，本节原有的逐项核对表作废。重新生成：

```
bin/headless.sh install && uv run python scripts/extract_seed.py "$(bin/headless.sh dump)" 2.1.23
```

手写版与 2.0.76 dump 的差异（供参考，说明哪些"记忆"不可靠）：

- 爪子 `extension_speed` 全部偏低（普通 0.035、快速/集装 0.1、长臂 0.05）；烧煤爪 `rotation_speed` 0.013。
- 大电杆 `maximum_wire_distance` 是 32，不是 30。
- 钢炉 `collision_box` ±0.7（仍是 2×2）；电钻 ±1.35（仍是 3×3）；地下管 y 方向 -0.29..0.2。
- 海水泵：`collision_box` x ±0.6、y -1.05..0.3，但 `tile_width = tile_height = 1`，能耗 60kW（电）；连接点 position (0,0) direction S（向岸侧）。几何库以 `tile_width/height` 优先于包围盒。
- 储液罐四个连接顺序为 N、E、S、W（内容一致）。
- ~~组装机 `crafting_categories` 多出 electronics、pressing 等；铜线、绿板配方类别是 `electronics`~~：这是 space-age 模组的改动，只开 base 时铜线、绿板是缺省的 `crafting`（见 §0）。
- 抽油机 `output_fluid_box` 连接用的是 `positions`（每方向一个），不是 `position`；`load_dump` 已兼容，几何库取 N 向那一个再旋转。

仍未由 dump 覆盖、需实机核对的数据项只剩 §2 的引擎常数。

### 2.0.76 → 2.1.23 迁移记录（2026-10-24）

- 配方的 `category`（单值）改为 `categories`（列表）；`RecipeProto.categories` 随之改为元组，生成器按集合相交判断机器能否生产。2.1 的铜线类别是 `crafting` + `electromagnetics`，1 型组装机的类别只剩 `crafting`、`advanced-crafting`（`basic-crafting` 取消）。
- `defines.inventory.furnace_source` 等熔炉专用常量取消，统一用 `crafter_input` / `crafter_output`。
- `LuaEntity.neighbours` 取消，地下带配对改读 `underground_belt_neighbour`（电杆铜线早已用 `get_wire_connector`）。
- `map-settings.json` 的 `enemy_expansion` 新增三个必填字段，缺了建存档直接失败；`config/test-map-settings.json` 已补。
- 2.1 多了内置 mod `recycler`，游戏会把它自动加进 `mod-list.json`。
- 烧煤钻 `vector_to_place_result` 从 (-0.5,-1.3) 改为 (-0.35,-1.3)；海水泵多了 60kW 能耗字段。
- 其余 VERIFY 条目在 2.1.23 上重跑 `fgen verify` 与全部 headless 测试，结论不变。

## 2. 引擎常数（种子文件 `_constants`，dump 里没有）

| 条目 | 当前假设值 / 约定 | 所在文件 | 核对方法 | 把握 |
| --- | --- | --- | --- | --- |
| 每秒 tick 数 | 60 | seed json `_constants.ticks_per_second` | 常识；无需 dump | 高 |
| 带速 ×8 常数 | 每格（两道合计）8 个物品，即每道 4 个；`items_per_second = speed × 60 × 8`，黄带 15/s | seed json `_constants.belt_items_per_tile`, `geom/belt.py` | 在 headless 里接满一条黄带，用生产统计测 60 秒流量 | 已核对（2.0.76）：`fgen verify` #8，loader 喂满的带 60 秒游戏时间实测黄/红/蓝 14.99/30.01/45.00 每秒，与公式一致 |
| 带道数 | 2 | seed json `_constants.belt_lanes` | 常识 | 高 |
| 爪子基础手持量 | 1（未研究任何手持加成；集装爪也按 1 算） | seed json `_constants.inserter_base_hand_size`, `gen/throughput.py` | 新开存档放各种爪子箱到箱，数每次搬几个；集装爪在 2.0 的基础手持量很可能 >1 | 已核对（2.0.76）：`fgen verify` #6，新势力无研究时烧煤/普通/长臂/快速/集装爪箱到箱最大手持都是 1（`bulk_inserter_capacity_bonus` 为 0），与假设一致 |
| 配方默认耗时 | 原型缺省 `energy_required` 取 0.5 | seed json, `loader.py` | Factorio 原型文档 | 高 |
| 燃烧效率 | 烧煤机器 `effectivity` 视为 1（未读取该字段） | `gen/throughput.py` `fuel_rate` | dump 查看 stone-furnace `energy_source.effectivity` | 已核对（2.0.76）：dump 中 stone-furnace / steel-furnace `energy_source.effectivity` 都是 1 |

## 3. 方向、坐标与几何约定

| 条目 | 当前假设值 / 约定 | 所在文件 | 核对方法 | 把握 |
| --- | --- | --- | --- | --- |
| 爪子方向 | N 向（direction=0）爪子**从北边取、往南边放**：取放格 = floor(中心 + 旋转后的 pickup/insert 向量) | `geom/inserter.py`, `gen/smelting_row.py` | headless 放一个 direction=0 的爪子，北边放箱子装货、南边放空箱，看物品流向；再放 E/S/W 各一个 | 已核对（2.0.76）：`fgen verify` #1，N 向爪子从北格取、往南格放（物品实测流向 + `pickup_position`/`drop_position` 取整都与 `geom/inserter.py` 一致）；E/S/W 为顺时针旋转。约定不变 |
| 向量旋转 | 顺时针 90°（N→E）把 (x,y) 映射为 (-y,x) | `geom/vec.py` | 放 E 向长臂爪，看取放格在东/西 | 已核对（2.0.76）：`fgen verify` #1，E 向爪子从东格取、往西格放，与 (x,y)→(-y,x) 一致 |
| 16 向 direction | 2.0 蓝图直接存 16 向整数 N=0,E=4,S=8,W=12；1.1 蓝图是 8 向（E=2），导入时按版本号 <2 换算 | `geom/vec.py`, `blueprint/import_.py` | 游戏导出一个各向带子的蓝图解码查看；fixtures 中 2.0.28 的铁轨 direction=4 与此一致（来源见 §5） | 已核对（2.0.76）：`defines.direction.east` = 4；游戏导出的东向带/地下带 direction = 4；往返测试四个方向全部一致 |
| 占地尺寸 | `ceil(collision_box 宽/高)`，原型有 `tile_width/tile_height` 时以其为准；E/W 时宽高互换 | `geom/footprint.py` | dump 对比；放实体看占格 | 已核对（2.0.76）：往返测试：蓝图导入后游戏实体的 position 与生成器给出的中心完全一致（含 2×2、3×3、2×1 分流器） |
| 中心奇偶 | 偶数边长中心在整数，奇数在 .5；不符合者判为 `position.misaligned` | `geom/footprint.py`, `layout/validate.py` | 游戏导出蓝图核对 position | 已核对（2.0.76）：同上，往返测试 position 逐个相等 |
| 供电区 | 以中心为圆心、半边长 `supply_area_distance` 的正方形内**完整**的格子；用电实体只要任一格落入即算供电 | `geom/pole.py`, `layout/validate.py` | 实放电杆，在覆盖边缘逐格放爪子看是否通电（游戏实际按碰撞箱与覆盖区相交判断，边缘情况可能不同） | 已核对（2.0.76）：`fgen verify` #9，小杆/中杆周围逐格放 1×1 爪子，通电与否与 `pole.supply_tiles` 完全一致；更大实体与覆盖区部分相交的情况未测 |
| 拉线距离 | 两杆中心距 ≤ 两者 `maximum_wire_distance` 的较小值即可连线 | `geom/pole.py` | 实放异种电杆拉线 | 高 |
| 电网连通 | 校验按拉线距离建图判连通；~~导出蓝图不写 2.0 `wires` 字段，假设放置蓝图时电杆会自动连线~~ → 现在导出器按同一张拉线可达图写出生成树 `wires` | `layout/validate.py`, `blueprint/export.py` | headless 导入生成的蓝图并建造，读回电网数量；若不自动连线，需在导出器写 `wires`（fixtures 显示铜线形如 `[e1, 5, e2, 5]`） | 已核对（2.0.76）：**与假设相反**：不写 `wires` 的蓝图建出来的电杆即使在拉线距离内也**不**自动连线（`fgen verify` #2，两个网络）。已改：`blueprint/export.py` 写出蓝图级 `wires`（`geom.pole.copper_tree`：拉线可达图的最小生成森林，连接器 id 来自种子 `_constants.wire_connector_pole_copper`），测试 `test_export_writes_pole_wires_as_spanning_tree`；写了 `wires` 后实测为一个网络 |
| 液体连接格式 | 2.0 `pipe_connections[]` 每项 `position`（实体内、相对 N 向中心）+ `direction`（指向外侧邻格）；dump 中 direction 可能是整数或名称，两者都接受 | `data/loader.py`, `geom/fluid.py` | dump 中查看 pipe / storage-tank / pumpjack 的 pipe_connections 原文 | 低 |
| 液体相邻判定 | 两连接点格相邻且方向相对才算连通；地下连接不参与 | `layout/validate.py` | 实放管道相邻、错位各一例 | 中 |

## 4. 带子与地下带约定

| 条目 | 当前假设值 / 约定 | 所在文件 | 核对方法 | 把握 |
| --- | --- | --- | --- | --- |
| 带道命名 | `left` / `right` 相对流向；向东流时 left 在北侧 | `geom/belt.py` | 在东向带上手放物品到北侧道，看游戏/脚本 API 报告的 lane 编号 | 已核对（2.0.76）：`fgen verify` #4，东向带：从北侧侧向汇入的物品在 `get_transport_line(defines.transport_line.left_line)`（=1）上，即 left = 北侧，与 `geom/belt.py` 一致 |
| 爪子放带落远道 | 侧面放带的爪子落在离自己远的那条道；从带子正前/正后放带未定义（代码抛错） | `geom/belt.py`, `gen/smelting_row.py`（输出带只占远道） | 实放爪子往带上放物品观察 | 已核对（2.0.76）：`fgen verify` #4，带北侧的爪子放到 right（南、远道），南侧的放到 left（北、远道），与 `geom.belt.drop_lane` 一致（维基 Inserters 页亦同） |
| 爪子从带取两道 | 进料爪从矿带两道都取（矿一道、煤一道的混带能同时供矿和燃料） | `gen/smelting_row.py` | 实测一列石炉 | 已核对（2.0.76）：冶炼列实测（`fgen sweep smelting --measure`）：矿走左道、煤走右道的混带供石炉/钢炉，所有炉子 `no_fuel` 探针为 0，产量达标 |
| 带子连续性 | 带的下一格是不反向的带（含侧向汇入）、同向的地下带入口、同向分流器即视为连续；分流器出口不检查 | `layout/validate.py` | 设计选择，非游戏事实；实机按需补充 | — |
| 带子终点声明 | 进料带末端是有意的死路，用 `Unit.meta["belt_terminals"]` 显式声明后不报 `belt.discontinuous` | `layout/validate.py`, `gen/smelting_row.py` | 设计选择；实机无需核对 | — |
| 地下带 `type` 字段 | 蓝图实体 `type` 取值 `"input"` / `"output"`；缺省按 `"input"` | `layout/validate.py` | 游戏导出一对地下带的蓝图解码查看 | 已核对（2.0.76）：`fgen verify` #3，游戏导出一对地下带：`type` 为 `"input"` / `"output"` |
| 地下带出入口方向 | 入口与出口的 `direction` 都等于物品流向；配对 = 同名同向、距离 1..`max_distance` 内最近的一个且类型相反 | `layout/validate.py` | 同上，并测最远配对距离 | 已核对（2.0.76）：`fgen verify` #3，入口和出口的 direction 都等于流向（东 = 4）；黄地下带最远配对偏移 = 5 = `max_distance`（即中间隔 4 格），与 `validate.py` 的 1..max_distance 一致 |

## 5. 蓝图字符串

| 条目 | 当前假设值 / 约定 | 所在文件 | 核对方法 | 把握 |
| --- | --- | --- | --- | --- |
| 字符串格式 | `"0"` + base64(zlib(JSON)) | `blueprint/codec.py` | fixtures 已能解码；游戏导入一个生成的字符串 | 已核对（2.0.76）：游戏 `import_stack` 接受导出器生成的字符串（返回 0），`export_stack` 产出的字符串 `decode` 正常 |
| 版本号打包 | `major<<48 \| minor<<32 \| patch<<16 \| build`，build 缺省 0（plan 只写了前三段；fixtures 里 2.0.28 的低 16 位是 1，故加了 build） | `blueprint/codec.py` | 游戏导出蓝图解码看 `version` | 已核对（2.0.76）：`fgen verify` #5，游戏导出的 `version` 解包为 2.0.76.0（build = 0），与 `pack_version` 一致 |
| 16 向 direction | 2.x 的 direction 值是 1.1 的两倍（N=0,E=4,S=8,W=12） | `geom/vec.py`, `blueprint/import_.py` | 维基 Blueprint string format 页已核对（"Direction appears to be double the previous values"） | 已核对 |
| N 向 direction 可省略 | 导出器**总是写出** direction（含 0）；fixtures 中电杆、组装机省略了 direction，说明游戏端接受缺省 | `blueprint/export.py` | 导入一个全写 direction=0 的蓝图确认无报错 | 已核对（2.0.76）：`fgen verify` #5，显式写 direction=0 与省略都能导入（返回 0）并完整建造；游戏自己导出时省略 N 向的 direction |
| icons 格式 | 导出 `{"signal": {"type": "item", "name": ...}, "index": 1}`；fixtures 中 signal 省略了 `type` | `blueprint/export.py` | 游戏导入确认两种都接受 | 已核对（2.0.76）：`fgen verify` #5，带 `type` 与不带 `type` 都接受；游戏自己导出时 signal 不写 `type` |
| `mirror` 字段 | 编解码原样保留；`layout.transform.mirror_*` **不**翻转实体的 `mirror` 标志（种子集里没有不对称实体） | `blueprint/*`, `layout/transform.py` | 阶段 5 放化工厂时核对游戏翻转蓝图时是否切换该标志 | 低 |
| 不可旋转实体的方向 | 变换会旋转所有实体的 direction（熔炉、箱子旋转后可能导出非 0 direction） | `layout/transform.py` | 导入旋转后的蓝图看游戏是否接受/忽略 | 已核对（2.0.76）：旋转后的冶炼列（熔炉、电杆写成 E/S/W）导入无报错；游戏读回熔炉、箱子、电杆、管道以及无流体配方的组装机 direction 一律为 0。比较规则放在 `geom.footprint.direction_matters` |
| 蓝图级字段 | 导入只取 `entities`；`wires`、`tiles`、`schedules` 等丢弃 | `blueprint/import_.py` | 设计选择；注意维基仍写 1.1 的 `neighbours`（铜线）字段，2.0 改为蓝图级 `wires`，维基页标注"需更新到 2.0" | — |
| 真实蓝图样本 | factorio.com、factorioprints.com、factorio.school、factoriobin.com 均被代理 403；改从 MIT 许可的 `redruin1/factorio-draftsman` 仓库取了 3 条 2.0 字符串（2.0.28 ×2、2.0.55 ×1），见 `tests/fixtures/SOURCES.txt`。**无法确认它们是游戏导出的，还是 draftsman 生成的** | `tests/fixtures/` | 从游戏里直接导出几条蓝图替换/补充 | 已补充（2.0.76）：另有 3 条由 headless 游戏自己 `create_blueprint` + `export_stack` 导出（`bp_2.0.76_game_*.txt`），`test_game_export_matches_generator` 核对游戏导出的冶炼列与生成器布局一致 |

## 6. 吞吐模型

| 条目 | 当前假设值 / 约定 | 所在文件 | 核对方法 | 把握 |
| --- | --- | --- | --- | --- |
| 爪子吞吐估算 | 每次搬运 = 转一整圈（去半圈、回半圈），`物品/秒 = 手持量 × 60 × rotation_speed`；忽略伸缩时间、带上拾取延迟与研究加成。普通爪 ≈0.84/s | `gen/throughput.py` | headless 箱到箱、带到炉各测 60 秒；结果写入 `characterization/smelting_row.jsonl` 的 `measured` | 箱到箱已实测（2.0.76，`fgen verify` #6）：普通 0.849/s（估 0.84）、长臂 1.198（1.2）、快速/集装 2.496（2.4）、烧煤 0.749（0.78），偏差 ≤4%。带到炉：冶炼列里爪子从未成为瓶颈（每炉需求 ≤0.65/s，远低于估计），所以这一估算在带取场景的精度仍未测到 |
| 机器产率 | `crafting_speed × 产物数 / energy_required`；不计启动、燃料短缺 | `gen/throughput.py` | 特征化实测 | 已核对（2.0.76）：18 组冶炼列实测稳态产量 = 炉数 × crafting_speed / energy_required（偏差 ≤2%，为 10 秒采样的整数量化） |
| 输出带容量 | 单侧冶炼列的输出只占远道，上限为单道容量（黄带 7.5/s）——这是本排法的性质，导致默认参数下 15/s 不可行 | `gen/smelting_row.py` | 实测一列 24 台石炉的输出 | 已核对（2.0.76）：24 台石炉单侧列输出只占远道，实测 7.5/s（= 黄带单道容量）；15/s 仍须换红带或双侧 |

## 7. 数据导出字段路径（`fields_used()`）

| 条目 | 当前假设值 / 约定 | 所在文件 | 核对方法 | 把握 |
| --- | --- | --- | --- | --- |
| 字段名与嵌套路径 | 见 `fgen.data.loader.fields_used()`；如 `energy_source.type`、`energy_source.fuel_categories`（缺省时读 `fuel_category`）、`fluid_box.pipe_connections`、抽油机 `output_fluid_box.pipe_connections` | `data/loader.py` | 对真实 dump 跑 `load_dump`，对每个字段统计"有值的原型数"，为 0 的就是路径错了 | 已核对（2.0.76）：`load_dump` 成功加载全部 53 个原型 |
| 向量/包围盒编码 | dump 中向量可能是 `[x, y]` 或 `{"x","y"}`，包围盒可能是 `[[..],[..]]` 或 `{"left_top","right_bottom"}`，两种都接受 | `data/loader.py` | 看真实 dump 原文 | 已核对（2.0.76）：dump 使用 `[x, y]` 与 `[[..],[..]]`；抽油机连接用 `positions` 列表 |
| dump 不含版本与常数 | `load_dump` 的版本号与引擎常数缺省取自种子文件 | `data/loader.py` | 调用时显式传 `version=` | — |

## 8. 仍未核对的条目（阶段 1 结束时）

| 条目 | 为什么还没核 | 建议做法 |
| --- | --- | --- |
| 每秒 tick 数（60） | 实测全部按 tick 计时，这个常数只用来把 tick 换成秒；带速、产率的实测与按 60 换算的预测一致，算间接证据 | 无需专门核对 |
| 带道数、配方默认耗时 | 常识/原型文档；dump 里种子配方都显式写了 `energy_required` | 无需专门核对 |
| 拉线距离（异种电杆、恰在边界） | 只测了同种小杆相距 7 格（< 7.5）能连；导出器只在 `wire_reaches` 为真时写 `wires`，游戏若拒绝过长的线会表现为 `power` 检查失败 | 在 verify 里加：小杆–中杆、恰好 7.5 格与 8 格各一例 |
| 供电区：多格实体部分落入覆盖区 | `fgen verify` #9 只用了 1×1 爪子 | 用 2×2 熔炉、3×3 组装机沿覆盖区边缘逐格平移 |
| 液体连接格式 / 液体相邻判定 | 阶段 1 没有液体单元；往返测试只证明管道、地下管能放下并保持方向，没有测连通 | 阶段 5 前放管道相邻、错位、地下管配对各一例，读 `fluidbox.get_connections` |
| 爪子吞吐估算（带到炉/带到箱） | 冶炼列里爪子从不成为瓶颈；组装机→组装机已在阶段 4a 测过（§11），带→机器仍未单测 | 单爪从满带取到箱子测 60 秒，各爪种、带速各一组 |
| `mirror` 字段 | 种子集里没有不对称实体 | 阶段 5（化工厂） |
| 组装机方向（流体配方） | `direction_matters` 对流体配方组装机保留方向，但种子里没有流体配方，未实测 | 阶段 5 |
| ~~采矿机（电钻）放置~~ | ~~平地测试场没有资源，蓝图里的电钻建不出来~~ → 已核对（2.1.23），见 §9 | — |


## 9. 采矿（阶段 2，2.1.23 实机）

核对脚本是阶段 2 开头的一次性 `/sc` 探测（结论落在 `src/fgen/geom/mining.py`、`src/fgen/sim/terrain.py` 的 docstring 里），之后由 headless 测试 `tests/test_sim_terrain.py`、`tests/test_sim_mining.py`、`tests/test_sim_real_map.py` 持续覆盖。

| 条目 | 结论 | 所在文件 | 核对方法 | 状态 |
| --- | --- | --- | --- | --- |
| 种子：`resource` 与大钻 | `resource` 原型读 `minable.mining_time`、`minable.result`（或 `results[]`）、`category`（缺省 `basic-solid`）、`infinite`、`minable.required_fluid`；矿 `mining_time` 1，铀矿 2 且要硫酸，原油 `basic-fluid`/`infinite`。钻机另读 `resource_categories`、`drops_full_belt_stacks`。大钻 5×5、`mining_speed` 2.5、半径 6.49、出料向量 (0,-2.85)、`resource_categories` 含 `hard-solid`。2.1 的电钻原型多了 `input_fluid_box`（采铀用），fgen 不读 | `data/loader.py`、`scripts/extract_seed.py`（`ADD` 表） | dump 重新生成种子 | 已核对（2.1.23 dump） |
| 出料落格 | 出料点 = 中心 + 旋转后的 `vector_to_place_result`，落在其所在格：N 向电钻在 (5.5,5.5) 的 `drop_position` 是 (5.5,3.65)，即足迹正前方一格、中间列；E/S/W 同理（旋转） | `geom/mining.py` | `drop_position` 读数 + 前方放带看物品 | 已核对 |
| 出料带道 | 落在**钻机一侧的近道**（出料点在带格里离钻机近的那半边，偏离中线 0.15 格）：N 向钻在东流带下方 → right（南）道；S 向钻在上方 → left（北）道；E 向钻在南流带西侧 → right（西）道。所以两侧钻机各占一道，带容量按道算 | `geom/mining.py` `drop_lane`、`gen/mining_patch.py` | 600 tick 后读 `get_transport_line(left/right).get_item_count()` | 已核对 |
| 钻机状态名 | `defines.entity_status`：`working`、`no_minable_resources`、`waiting_for_space_in_destination`（带满）、`no_power`、`low_power`；代码只用名字 | `sim/measure.py` | 遍历 `defines.entity_status` | 已核对 |
| 搜索区边界 | 资源格**中心**落在以钻机中心为心、半边长 `resource_searching_radius` 的正方形里才算：电钻 2.49 → 相对中心格偏移 -2..2（5×5）在内，偏移 3 不在（正/负方向、对角都测过）；大钻 6.49 → 偏移 6 在、7 不在（13×13）。正方形边（2.99）伸进偏移 2 那格 0.99 格，到不了偏移 3 那格——"半格"问题不存在 | `geom/mining.py` `search_range` | 单格矿放在不同偏移，看 `status` 与 `mining_target` | 已核对 |
| 运行时原型字段名 | `LuaEntityPrototype` 没有 `resource_searching_radius`（运行时叫 `mining_drill_radius`）；fgen 只在数据层读原型 dump 的名字 | — | `/sc` 读取报错 | 已核对 |
| tile 碰撞层格式 | `LuaTilePrototype.collision_mask` 是 `{layers = {名字 = true, ...}}`（不再是列表）。草/土只有 `ground_tile`；`water`/`deepwater` 有 `water_tile`、`item`、`player`、`resource`、`floor`、`doodad`；`out-of-map` 另有 `out_of_map`、`object` 等。电钻碰撞层 `is_lower_object, is_object, item, meltable, object, player, water_tile`；带 `floor, meltable, object, transport_belt, water_tile`；小杆 `is_lower_object, is_object, item, object, player, water_tile` | `sim/terrain.py` | `serpent.line(prototypes.tile[...].collision_mask)` | 已核对 |
| `find_tiles_filtered{collision_mask=列表}` | 匹配**含任一**层的格；"不可建" = 与参考实体（钻、带、杆）碰撞层的并集有交 | `sim/terrain.py` | 平地全可建、`set_tiles` 造的 water/deepwater 恰好被判为水且不可建；正常地图上 snapshot 的水 = `water`+`deepwater` 格数（1290），不可建格也正好是这些 | 已核对 |
| 同步生成区块 | 同一条 `/sc` 里 `request_to_generate_chunks` + `force_generate_chunk_requests()` 之后 `is_chunk_generated` 立即为真 | `sim/lua.py` `generate_chunks` | 调用前后读 `is_chunk_generated` | 已核对 |
| 画矿 | `create_entity{name='iron-ore', position={x+0.5,y+0.5}, amount=N}` 一格一个实体；`find_entities_filtered{type='resource'}` 读回位置与 `amount` 完全一致；同格再画需先销毁旧实体 | `sim/terrain.py` `paint` | `test_paint_and_read_back` | 已核对 |
| 钻机经蓝图放置 | 有矿时蓝图 ghost 能建出并 revive；足迹下无矿但搜索区内有矿的钻机也能放 | `sim/deployer.py` | 7 个画矿形状部署 | 已核对 |
| `mining_target` | 刚放下（还没跑 tick）时为 nil，开始采后才有值；实测里在探针中补记 | `sim/measure.py` | 读数 | 已核对 |
| 单钻产率 | 无科技时 = `mining_speed / mining_time`：电钻 0.5/s；画矿 7 形状共 271 台钻、20 条带，每条带 6×20 秒样本全部与契约相等（0% 偏差）。一道 15 台（=黄带单道 7.5/s）时实测 7.5/s，仅有 4/7560 个探针见到 `waiting_for_space_in_destination` | `geom/mining.py` `drill_rate` | `fgen sweep mining --measure` | 已核对 |
| 真实矿区边缘 | 正常地图起始区铁矿边缘格只有 2–90 矿，几秒就采空，钻机转 `no_minable_resources`；与相邻石矿重叠的钻机会混出石头。第一次端到端 5 条带偏差 -6%～-50%。生成器因此加了 `min_ore_amount_per_drill`（`low_ore`）与 `allow_mixed=False`（`mixed_resource`）；端到端把阈值抬到没有钻机会在实测窗口内采空（`first_dry_s_lower_bound`）后偏差 0% | `gen/mining_patch.py`、`sim/mining.py` | `fgen sweep mining --real-map` | 已核对 |
| 树与石头的实体类型 | 树 `tree`、石头 `simple-entity`；端到端前在矿区周围销毁（seed 20261024 清了 150 个），悬崖 `cliff` 保留为障碍 | `sim/mining.py` | 销毁计数 | 部分核对（只看了计数，没逐类核对类型名覆盖了所有障碍） |
| 已占格 | 非资源、非角色实体的 `bounding_box` 覆盖的格（钢箱 1 格、石炉 2×2 实测正确） | `sim/terrain.py` `occupied` | `test_occupied` | 已核对；树的碰撞盒很小，按 bounding_box 取整后只占 1 格（未逐棵核对） |
| 正常地图生成配置 | `config/ore-map-gen-settings.json`（测试场配置去掉 elevation 常数、各项 size 1、enemy-base size 0、peaceful）在 2.1.23 上可建图；seed 20261024 出生点 ±150 内有铁/铜/煤/石各一块，最近铁矿 352 格、29×29、(-66,35) 起，总量 156978 | `config/ore-map-gen-settings.json` | `tests/test_sim_real_map.py` | 已核对 |
| 大钻 `drops_full_belt_stacks` | 种子里有该字段。阶段 6a 实测大钻 + 极速带（`big-drill-medium`，63 台）：每道 9 台 × 2.5/s = 22.5/s，实测每条带 45/s，与按"单件"算的道容量一致（无堆叠科技时不影响道容量） | `gen/mining_sweep.py` | `fgen sweep mining --measure` | 已核对（只测了无科技、极速带） |
| 钻机出料到地下带 | 钻机的出料点落在地下带**入口或出口**格上时照常上带、道不变（阶段 6a：spike 12 台钻、落在 2 个入口 1 个出口上，6/s 全到；`lane-capacity` 每道 15 台满载两道都 7.5/s；8 个画矿用例 + 真实地图 0% 偏差）。不变量 `drill.output_blocked` 本来就把 `underground-belt` 算作合法输出 | `gen/mining_patch.py` | `fgen sweep mining --measure` | 已核对（2.1.23） |
| `lua.clear_area` | 改为默认保留 `resource` 实体（否则实测前的清场会把画的矿一起删掉）；冶炼列实测不受影响 | `sim/lua.py` | 全部 headless 测试 | — |

## 10. 连接路由与夹具（阶段 3a，2.1.23 实机）

一次性 `/sc` 探测之后由 headless 测试持续覆盖：`tests/test_sim_route.py`（电网、管道、侧向汇入地下带、悬崖旁可建性）、`tests/test_sim_clear.py`、`tests/test_sim_site.py`、`tests/test_sim_site_real.py`。

| 条目 | 结论 | 所在文件 | 核对方法 | 状态 |
| --- | --- | --- | --- | --- |
| infinity-pipe 过滤字段 | `LuaEntity.set_infinity_pipe_filter{name, percentage?, temperature?, mode?}`，`mode` 取 `at-least`（缺省）/`at-most`/`exactly`/`add`/`remove`；`get_infinity_pipe_filter()` 读回 `{name='water', percentage=1, temperature=15, mode='at-least'}`（温度缺省为该液体最低温）。种子新增 `infinity-pipe` 原型（四向连接，同管道） | `sim/harness.py`、`scripts/extract_seed.py` | 设过滤后读回；接管道 + 储液罐 120 tick 后罐满 25000 | 已核对 |
| 读液体量 | **2.1 没有 `LuaEntity.fluidbox`**（访问报 `LuaEntity doesn't contain key fluidbox`）；改用 `get_fluid(1)`（`{name, amount, temperature}` 或 nil）、`get_fluid_count(name?)`、`get_fluid_contents()`、`fluids_count`、`get_fluid_box_neighbours(i)`（连接到的实体列表） | `sim/site_measure.py` `tank_fluid` | 储液罐读数 | 已核对 |
| 地下管最远配对 | 偏移 10（= `max_underground_distance`，中间隔 9 格）能配对，偏移 11 不能；路由跳跃偏移取 2..`max_underground_distance` | `route/pipe.py` | 两端相对放置，`get_fluid_box_neighbours(1)` 计数 | 已核对 |
| 地下带跳跃上限 | 沿用 §4：最远配对偏移 = `max_distance`（黄带 5）。任务书 §3.4 写的 `n ≤ max_distance + 1` 与 §4 实测不符，按实测取 2..`max_distance` | `route/belt.py` | §4 | 已核对（2.0.76） |
| 侧向汇入到地下带入口/出口格 | 东向地下带入口或出口从北侧被带子侧向喂入，物品都只上 **left（近）道**，与普通带一致（900 tick 后下游带 left 有货、right 为 0）。路由器只往自己补的直带桩上汇入，从不选地下带格作汇入点，所以不需要禁止规则；吞吐未测 | `route/belt.py` | `test_side_load_onto_underground_ends_fills_near_lane` | 已核对（只看了道，没测速率） |
| 悬崖旁的可建性 | 斜向悬崖（如 `west-to-north`）的 `bounding_box` 带 `orientation`（0.125），`left_top/right_bottom` 是**未旋转**的框；阶段 2 按轴对齐取整会漏掉 3 格、多算 4 格。`RconTerrain.occupied` 改为取旋转后四角的外接框：6 种朝向的悬崖周围，小杆、中杆、带子、管道 `can_place_entity` 为假的格全部落在 `occupied` 里（保守：117 格中 57 格其实可放） | `sim/terrain.py` `AABB_FN` | `test_snapshot_marks_every_cliff_tile_poles_cannot_use` | 已核对；真实地图 seed 20261024 的矿区 ±45 格内没有悬崖，端到端没遇到 |
| 清障的实体类型与势力 | 树 `tree`、石头 `simple-entity`、地上物品 `item-entity`（名 `item-on-ground`）、尸体 `corpse`，势力都是 `neutral`；`clear_obstacles` 只删 neutral 的这四类。seed 20261024 最近铁矿 ±45 格：366 棵树、2 块石头、0 悬崖 | `sim/clear.py` | `test_clear_counts_match_snapshot`（计数 = 清前计数，清后快照 occupied = 剩余实体占格） | 已核对 |
| 脚本建的杆自动连线 | `create_entity` 建的夹具电杆会自动连到拉线范围内的杆，包括蓝图建出（并已写 `wires`）的杆；整站电网 id 唯一 | `sim/site_measure.py` | `networks()`：杆与用电实体各只有一个 `electric_network_id` | 已核对 |
| 物品统计口径 | `force.get_item_production_statistics(surface)`：`get_output_count` = 消耗（炉子吃矿、烧煤），`get_input_count` = 生产（钻机出矿、炉子出板）。infinity chest 出货与脚本清空汇箱都**不**计入（平地整站 iron-ore 生产 0、消耗 7.5/s）。燃料在开始燃烧时按整个计数，2 分钟窗口里石炉煤耗有 ±3% 的量化 | `sim/site_measure.py` | 平地整站读数 | 已核对 |
| 路由出的带与夹具 | 夹具 Unit + 路由器替代了阶段 1 的固定夹具；`test_sim_measure`（冶炼列）、`test_sim_mining`、`test_sim_real_map` 在新夹具下全部照旧通过 | `sim/harness.py` | 全部 headless 测试 | 已核对 |
| 真实地图整站预热 | 采矿供给恰等于冶炼需求（7.5/s）时，冶炼列 24 台炉的输入缓冲要很久才填满：40 s 预热后第一段只有 2/s，120 s 预热后 7.456/s（钻 7.5/s，差额是仍在进缓冲的矿）。平地用满带源时 60 s 足够 | `sim/site_measure.py` `REAL_MEASURE` | 实测样本 | 已核对 |

## 11. 直插链与组装机（阶段 4a，2.1.23 实机）

核对方法：阶段 4a 开头的一次性 spike（`create_entity` 手搭链，scratchpad 脚本，结论见 `docs/experiments/4a-cycle-solver.md` 迭代 1），之后由 `tests/test_sim_direct_chain.py` 与 `fgen sweep direct-chain --measure` 持续覆盖。

| 条目 | 结论 | 所在文件 | 核对方法 | 状态 |
| --- | --- | --- | --- | --- |
| 配方开局锁定 | 2.1.23 + 目标 mod 组合（含 space-age）下，`copper-cable`、`electronic-circuit` 开局 `enabled = false`：组装机能建、`recipe` 能设，但状态是 `recipe_not_researched`，永不开工。实测前 `force.recipes[name].enabled = true`（`lua.enable_recipes`，`RconCharacterizer.measure` 按单元里出现的配方自动解锁） | `sim/lua.py`、`sim/measure.py` | spike：解锁前 5 条链产出 0、状态全是 `recipe_not_researched`；解锁后正常 | 已核对 |
| 蓝图里组装机的 `recipe` | 蓝图实体的 `recipe` 字段经 `build_blueprint` + `revive` 后保留，组装机按它开工 | `blueprint/export.py`、`sim/deployer.py` | 16 个直插链单元实测全部达标 | 已核对 |
| 直插不需要隔格 | 两台 3×3 组装机之间只隔爪子那一格（取放向量 (0,-1)/(0,1.2) 本身决定：取自一侧相邻格、放进另一侧相邻格）即可机器到机器转运，不需要额外空格或箱子 | `solve/cycle.py`（槽位由取放向量算出） | spike + 全部直插链实测 | 已核对 |
| 组装机→组装机的爪子速率 | 爪子封顶时实测：快速爪 2.52/s（估计 2.4）、普通爪 0.85/s（估计 0.84），3 型铜线机（5/s）→ 绿板机（要 7.5/s）。组装机产出被取空的节奏**不**限制转运，`inserter_throughput_estimate` 对这种用法偏保守 | `gen/throughput.py` | spike：用 3 型让爪子成为瓶颈，绿板产率 × 3 = 铜线转运率 | 已核对（只测了快速、普通两种；长臂未单测） |
| 一个铜线机分给两台绿板机 | 中间的铜线机用两只爪子分别喂左右两台绿板机，产出按需分走（3:2 周期每台绿板机满速 1/s） | `solve/cycle.py`（最大流） | 3:2 周期实测 2/s、4/s | 已核对 |
| 混带取料 | 铁、铜各占一道的带上，爪子只取目标组装机配方需要的那种（铜线机只拿铜、绿板机只拿铁），两道都会取 | `solve/cycle.py`（混带骨架） | 全部单等级直插链实测达标 | 已核对（间接：按吞吐） |
| 近似配比的过剩 | 2:1 周期（铜线 4/s 供 3/s）里铜线机交替 `full_output` 停机，绿板产率不受影响；铜板只按实际用量消耗 | `solve/period.py` `flows` | R = 1 实测 1.0/s，探针 `full_output` 约 17% | 已核对 |
| 长臂爪跨带放货 | 长臂爪（pickup (0,-2)、insert (0,2.2)）从绿板机取货、跨过紧邻的铁带放到下一条输出带；9×12 周期实测 2/s、4/s 达标 | `solve/cycle.py`（`extra_inserters`） | `fgen sweep direct-chain --measure` 的 `fastlong-*` | 部分核对：吞吐已测；落在输出带哪一道（契约写 right = 远道）没有单独读道，夹具汇点两道都收 |
| 长臂爪从组装机取货的速率 | 估计 1.2/s（箱到箱已测 1.198，§6）；9×12 周期里每只长臂爪只运 1/s，没有逼到上限 | `gen/throughput.py` | 未单测组装机场景 | 待核：用 3 型铜线机→长臂爪→绿板机测封顶速率 |


## 12. 运行时状态（阶段 4b，2.1.23 实机）

核对方法：阶段 4b 的 spike（`force.research_all_technologies()` 前后读 `LuaForce`，记录见 `docs/experiments/4b-cpsat-and-science.md`
迭代 B0），之后由 `tests/test_sim_runtime.py` 与四份 `fgen sweep ... --measure` 持续覆盖（每条实测记录带 `runtime`）。

| 条目 | 结论 | 所在文件 | 核对方法 | 状态 |
| --- | --- | --- | --- | --- |
| 全科技后的 force 加成 | 2.1.23 + 目标 mod 组合：254/277 科技研究完（无限科技停在当前等级）；`inserter_stack_size_bonus` 3、`bulk_inserter_capacity_bonus` 11、`belt_stack_size_bonus` 3、`mining_drill_productivity_bonus` 0.3 | `data/runtime.py`、`sim/runtime.py` | spike + `test_sim_runtime.py`（只断言 > 0，不写死数值） | 已核对 |
| 采矿产能进入产量 | 电钻每道产量 = 钻数 × 钻速 × (1 + 产能)，未满道的带实测与预测差 ≤ 1.5%（8 个画矿用例 + 真实地图） | `gen/mining_sweep.py` `predicted` | `fgen sweep mining --measure --real-map` | 已核对 |
| 大钻成叠放带 | `drops_full_belt_stacks` 的钻机按 `1 + belt_stack_size_bonus` 一叠放到带上：快带一道（22.5/s 件数上限）实测 29.2/s，不封顶 | `gen/mining_sweep.py` | big-drill-medium 用例 | 已核对（只测了 big-mining-drill + express 带） |
| 手持 4 的普通/快速/长臂爪 | 直插链、冶炼列在手持 4 下实测与研究前相同（机器封顶）；爪子估计按 `hand × tps × rotation_speed` 线性放大，未在爪子封顶的场景单测 | `gen/throughput.py` | 四份 characterization | 部分核对：没有用"爪子是瓶颈"的单元测手持 4 的实际速率 |
| 冶炼列测量统计量 | 10 s 样本只能分辨 1 件（5/s 时 2%），对比改用整窗口均值 `throughput_mean` | `sim/measure.py`、`gen/sweep.py` | 冶炼列重测：中位数 ±2%、均值 ≤ 0.7% | 已核对 |
