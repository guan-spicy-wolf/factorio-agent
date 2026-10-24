# 待实机核对清单（VERIFY）

阶段 0 时本环境没有 Factorio 二进制，下表内容原本都是**凭记忆**写下或由此推出的。阶段 1 起 headless 2.0.76 可用：标"已核对（2.0.76）"的行由 `uv run fgen verify`（`src/fgen/sim/verify.py`，测试 `tests/test_sim_verify.py`）或往返测试（`tests/test_sim_deployer.py`）在游戏里核对过，其余仍按"核对方法"待核。实机核对时只启用 `base` 模组（见 §0）。

"把握"一列是作者自评：**高** = 很确定（多年未变的基础值）；**中** = 记得 1.1 的值，2.0 可能改过；**低** = 只记得大概，或 2.0 格式细节不确定。

通用核对方法（下文简称 *dump 对比*）：在 headless 上执行 `factorio --dump-data`，用 `fgen.data.loader.load_dump(".../script-output/data-raw-dump.json")` 加载，与 `load_seed()` 按 `fields_used()` 列出的字段逐项对比（写个一次性脚本打印差异即可）。

## 0. 实机环境

- 测试服务器只启用 `base`。headless 安装包自带的 `mod-list.json` 启用了 space-age / quality / elevated-rails，它们会改写 base 原型（例如铜线、绿板配方类别变成 `electronics`，组装机多出 `pressing` 等类别）。阶段 0 的种子文件就是从这样的 dump 抽出来的；阶段 1 起 `bin/headless.sh dump` 改为只开 base 再 dump，种子已重新生成（差异只有上述配方类别 / 组装机类别，以及管道 fluid_box 里 space-age 的冰冻贴图）。
- 平地测试场（`config/test-map-gen-settings.json`）：只把 water 的 `size` 设 0 仍会有湖，需要把 `property_expression_names.elevation` 固定为常数；2.0 的石头是单独的 `rocks` 自动放置项。建图后用 RCON 核对原点 200×200 内无阻挡地块、无实体（`tests/test_sim_server.py`）。
- RCON、`/sc` 与服务器启动的实测细节写在 `src/fgen/sim/rcon.py`、`src/fgen/sim/server.py` 的 docstring 里。

## 1. 种子数据（`src/fgen/data/seed/vanilla-2.0.json`）

**已核对（2.0.76）。** 种子文件现在由 `scripts/extract_seed.py` 从真实 `--dump-data` 生成（`_provenance` 记录来源与版本），不再是手写数据，本节原有的逐项核对表作废。重新生成：

```
bin/headless.sh install && uv run python scripts/extract_seed.py "$(bin/headless.sh dump)" 2.0.76
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
| 爪子吞吐估算（带到炉/带到箱） | 冶炼列里爪子从不成为瓶颈 | 单爪从满带取到箱子测 60 秒，各爪种、带速各一组 |
| `mirror` 字段 | 种子集里没有不对称实体 | 阶段 5（化工厂） |
| 组装机方向（流体配方） | `direction_matters` 对流体配方组装机保留方向，但种子里没有流体配方，未实测 | 阶段 5 |
| 采矿机（电钻）放置 | 平地测试场没有资源，蓝图里的电钻建不出来（ghost 不生成）；往返测试因此不含电钻 | 阶段 2 在测试场里用 `surface.create_entity{name='iron-ore', amount=...}` 铺矿再测 |

