# 待实机核对清单（VERIFY）

本环境没有 Factorio 二进制、访问不到 factorio.com，下表所有内容都是**凭记忆**写下或由此推出的，未经实机核对。拿到 headless 后按"核对方法"逐条确认，确认后在"把握"列改为"已核对（版本号）"。

"把握"一列是作者自评：**高** = 很确定（多年未变的基础值）；**中** = 记得 1.1 的值，2.0 可能改过；**低** = 只记得大概，或 2.0 格式细节不确定。

通用核对方法（下文简称 *dump 对比*）：在 headless 上执行 `factorio --dump-data`，用 `fgen.data.loader.load_dump(".../script-output/data-raw-dump.json")` 加载，与 `load_seed()` 按 `fields_used()` 列出的字段逐项对比（写个一次性脚本打印差异即可）。

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
- 组装机 `crafting_categories` 多出 electronics、pressing 等；铜线、绿板配方类别是 `electronics`（齿轮仍是 `crafting`）。
- 抽油机 `output_fluid_box` 连接用的是 `positions`（每方向一个），不是 `position`；`load_dump` 已兼容，几何库取 N 向那一个再旋转。

仍未由 dump 覆盖、需实机核对的数据项只剩 §2 的引擎常数。

## 2. 引擎常数（种子文件 `_constants`，dump 里没有）

| 条目 | 当前假设值 / 约定 | 所在文件 | 核对方法 | 把握 |
| --- | --- | --- | --- | --- |
| 每秒 tick 数 | 60 | seed json `_constants.ticks_per_second` | 常识；无需 dump | 高 |
| 带速 ×8 常数 | 每格（两道合计）8 个物品，即每道 4 个；`items_per_second = speed × 60 × 8`，黄带 15/s | seed json `_constants.belt_items_per_tile`, `geom/belt.py` | 在 headless 里接满一条黄带，用生产统计测 60 秒流量 | 高（无太空时代堆叠时） |
| 带道数 | 2 | seed json `_constants.belt_lanes` | 常识 | 高 |
| 爪子基础手持量 | 1（未研究任何手持加成；集装爪也按 1 算） | seed json `_constants.inserter_base_hand_size`, `gen/throughput.py` | 新开存档放各种爪子箱到箱，数每次搬几个；集装爪在 2.0 的基础手持量很可能 >1 | 中 |
| 配方默认耗时 | 原型缺省 `energy_required` 取 0.5 | seed json, `loader.py` | Factorio 原型文档 | 高 |
| 燃烧效率 | 烧煤机器 `effectivity` 视为 1（未读取该字段） | `gen/throughput.py` `fuel_rate` | dump 查看 stone-furnace `energy_source.effectivity` | 中 |

## 3. 方向、坐标与几何约定

| 条目 | 当前假设值 / 约定 | 所在文件 | 核对方法 | 把握 |
| --- | --- | --- | --- | --- |
| 爪子方向 | N 向（direction=0）爪子**从北边取、往南边放**：取放格 = floor(中心 + 旋转后的 pickup/insert 向量) | `geom/inserter.py`, `gen/smelting_row.py` | headless 放一个 direction=0 的爪子，北边放箱子装货、南边放空箱，看物品流向；再放 E/S/W 各一个 | 中（与原型向量一致；但游戏 UI 的"朝向"语义可能正好相反，务必实测） |
| 向量旋转 | 顺时针 90°（N→E）把 (x,y) 映射为 (-y,x) | `geom/vec.py` | 放 E 向长臂爪，看取放格在东/西 | 高 |
| 16 向 direction | 2.0 蓝图直接存 16 向整数 N=0,E=4,S=8,W=12；1.1 蓝图是 8 向（E=2），导入时按版本号 <2 换算 | `geom/vec.py`, `blueprint/import_.py` | 游戏导出一个各向带子的蓝图解码查看；fixtures 中 2.0.28 的铁轨 direction=4 与此一致（来源见 §5） | 中 |
| 占地尺寸 | `ceil(collision_box 宽/高)`，原型有 `tile_width/tile_height` 时以其为准；E/W 时宽高互换 | `geom/footprint.py` | dump 对比；放实体看占格 | 高 |
| 中心奇偶 | 偶数边长中心在整数，奇数在 .5；不符合者判为 `position.misaligned` | `geom/footprint.py`, `layout/validate.py` | 游戏导出蓝图核对 position | 高 |
| 供电区 | 以中心为圆心、半边长 `supply_area_distance` 的正方形内**完整**的格子；用电实体只要任一格落入即算供电 | `geom/pole.py`, `layout/validate.py` | 实放电杆，在覆盖边缘逐格放爪子看是否通电（游戏实际按碰撞箱与覆盖区相交判断，边缘情况可能不同） | 中 |
| 拉线距离 | 两杆中心距 ≤ 两者 `maximum_wire_distance` 的较小值即可连线 | `geom/pole.py` | 实放异种电杆拉线 | 高 |
| 电网连通 | 校验按拉线距离建图判连通；**导出蓝图不写 2.0 `wires` 字段**，假设机器人/玩家放置蓝图时电杆会自动连到范围内的电杆 | `layout/validate.py`, `blueprint/export.py` | headless 导入生成的蓝图并建造，读回电网数量；若不自动连线，需在导出器写 `wires`（fixtures 显示铜线形如 `[e1, 5, e2, 5]`） | 低（重要） |
| 液体连接格式 | 2.0 `pipe_connections[]` 每项 `position`（实体内、相对 N 向中心）+ `direction`（指向外侧邻格）；dump 中 direction 可能是整数或名称，两者都接受 | `data/loader.py`, `geom/fluid.py` | dump 中查看 pipe / storage-tank / pumpjack 的 pipe_connections 原文 | 低 |
| 液体相邻判定 | 两连接点格相邻且方向相对才算连通；地下连接不参与 | `layout/validate.py` | 实放管道相邻、错位各一例 | 中 |

## 4. 带子与地下带约定

| 条目 | 当前假设值 / 约定 | 所在文件 | 核对方法 | 把握 |
| --- | --- | --- | --- | --- |
| 带道命名 | `left` / `right` 相对流向；向东流时 left 在北侧 | `geom/belt.py` | 在东向带上手放物品到北侧道，看游戏/脚本 API 报告的 lane 编号 | 中 |
| 爪子放带落远道 | 侧面放带的爪子落在离自己远的那条道；从带子正前/正后放带未定义（代码抛错） | `geom/belt.py`, `gen/smelting_row.py`（输出带只占远道） | 实放爪子往带上放物品观察 | 高（侧放）/ 未知（正放） |
| 爪子从带取两道 | 进料爪从矿带两道都取（矿一道、煤一道的混带能同时供矿和燃料） | `gen/smelting_row.py` | 实测一列石炉 | 高 |
| 带子连续性 | 带的下一格是不反向的带（含侧向汇入）、同向的地下带入口、同向分流器即视为连续；分流器出口不检查 | `layout/validate.py` | 设计选择，非游戏事实；实机按需补充 | — |
| 带子终点声明 | 进料带末端是有意的死路，用 `Unit.meta["belt_terminals"]` 显式声明后不报 `belt.discontinuous` | `layout/validate.py`, `gen/smelting_row.py` | 设计选择；实机无需核对 | — |
| 地下带 `type` 字段 | 蓝图实体 `type` 取值 `"input"` / `"output"`；缺省按 `"input"` | `layout/validate.py` | 游戏导出一对地下带的蓝图解码查看 | 中 |
| 地下带出入口方向 | 入口与出口的 `direction` 都等于物品流向；配对 = 同名同向、距离 1..`max_distance` 内最近的一个且类型相反 | `layout/validate.py` | 同上，并测最远配对距离 | 中 |

## 5. 蓝图字符串

| 条目 | 当前假设值 / 约定 | 所在文件 | 核对方法 | 把握 |
| --- | --- | --- | --- | --- |
| 字符串格式 | `"0"` + base64(zlib(JSON)) | `blueprint/codec.py` | fixtures 已能解码；游戏导入一个生成的字符串 | 高 |
| 版本号打包 | `major<<48 \| minor<<32 \| patch<<16 \| build`，build 缺省 0（plan 只写了前三段；fixtures 里 2.0.28 的低 16 位是 1，故加了 build） | `blueprint/codec.py` | 游戏导出蓝图解码看 `version` | 中 |
| N 向 direction 可省略 | 导出器**总是写出** direction（含 0）；fixtures 中电杆、组装机省略了 direction，说明游戏端接受缺省 | `blueprint/export.py` | 导入一个全写 direction=0 的蓝图确认无报错 | 中 |
| icons 格式 | 导出 `{"signal": {"type": "item", "name": ...}, "index": 1}`；fixtures 中 signal 省略了 `type` | `blueprint/export.py` | 游戏导入确认两种都接受 | 中 |
| `mirror` 字段 | 编解码原样保留；`layout.transform.mirror_*` **不**翻转实体的 `mirror` 标志（种子集里没有不对称实体） | `blueprint/*`, `layout/transform.py` | 阶段 5 放化工厂时核对游戏翻转蓝图时是否切换该标志 | 低 |
| 不可旋转实体的方向 | 变换会旋转所有实体的 direction（熔炉、箱子旋转后可能导出非 0 direction） | `layout/transform.py` | 导入旋转后的蓝图看游戏是否接受/忽略 | 中 |
| 蓝图级字段 | 导入只取 `entities`；`wires`、`tiles`、`schedules` 等丢弃 | `blueprint/import_.py` | 设计选择 | — |
| 真实蓝图样本 | factorio.com、factorioprints.com、factorio.school、factoriobin.com 均被代理 403；改从 MIT 许可的 `redruin1/factorio-draftsman` 仓库取了 3 条 2.0 字符串（2.0.28 ×2、2.0.55 ×1），见 `tests/fixtures/SOURCES.txt`。**无法确认它们是游戏导出的，还是 draftsman 生成的** | `tests/fixtures/` | 从游戏里直接导出几条蓝图替换/补充 | — |

## 6. 吞吐模型

| 条目 | 当前假设值 / 约定 | 所在文件 | 核对方法 | 把握 |
| --- | --- | --- | --- | --- |
| 爪子吞吐估算 | 每次搬运 = 转一整圈（去半圈、回半圈），`物品/秒 = 手持量 × 60 × rotation_speed`；忽略伸缩时间、带上拾取延迟与研究加成。普通爪 ≈0.84/s | `gen/throughput.py` | headless 箱到箱、带到炉各测 60 秒；结果写入 `characterization/smelting_row.jsonl` 的 `measured` | 低（粗略） |
| 机器产率 | `crafting_speed × 产物数 / energy_required`；不计启动、燃料短缺 | `gen/throughput.py` | 特征化实测 | 高 |
| 输出带容量 | 单侧冶炼列的输出只占远道，上限为单道容量（黄带 7.5/s）——这是本排法的性质，导致默认参数下 15/s 不可行 | `gen/smelting_row.py` | 实测一列 24 台石炉的输出 | 高 |

## 7. 数据导出字段路径（`fields_used()`）

| 条目 | 当前假设值 / 约定 | 所在文件 | 核对方法 | 把握 |
| --- | --- | --- | --- | --- |
| 字段名与嵌套路径 | 见 `fgen.data.loader.fields_used()`；如 `energy_source.type`、`energy_source.fuel_categories`（缺省时读 `fuel_category`）、`fluid_box.pipe_connections`、抽油机 `output_fluid_box.pipe_connections` | `data/loader.py` | 对真实 dump 跑 `load_dump`，对每个字段统计"有值的原型数"，为 0 的就是路径错了 | 已核对（2.0.76）：`load_dump` 成功加载全部 53 个原型 |
| 向量/包围盒编码 | dump 中向量可能是 `[x, y]` 或 `{"x","y"}`，包围盒可能是 `[[..],[..]]` 或 `{"left_top","right_bottom"}`，两种都接受 | `data/loader.py` | 看真实 dump 原文 | 已核对（2.0.76）：dump 使用 `[x, y]` 与 `[[..],[..]]`；抽油机连接用 `positions` 列表 |
| dump 不含版本与常数 | `load_dump` 的版本号与引擎常数缺省取自种子文件 | `data/loader.py` | 调用时显式传 `version=` | — |
