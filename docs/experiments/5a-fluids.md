# 实验 5a：流体机器——塑料与硫酸

记录规则同 6a/4a/4b：每次迭代写假设、改动、规模/耗时、`validate` 结果、实测结果、失败原因、下一步。失败照记。
开始时间：2026-10-25 06:30 UTC（容器时间）。机器：4 核容器，CPython 3.13，OR-Tools 9.15（4b 装好的可选依赖 `solver`）。
Factorio headless 2.1.23（base + space-age + quality + elevated-rails + recycler），一次只起一个服务器。

## 迭代 F0：实机核对（06:30–06:46，不写几何代码）

一次性 `/sc` spike（scratchpad 脚本：`create_entity` 手搭、蓝图导入），结论写进 VERIFY.md §13。读原型 dump 得到的事实：

- 化工厂（`assembling-machine` 类型，3×3，`crafting_speed` 1，210 kW，`use_mirroring: true`）的流体盒在 2.1 的 dump 里是
  **`fluid_boxes` 列表**（不是 `fluid_box`/`output_fluid_box`）：两个 `production_type = input`，连接在 (−1,−1)、(1,−1) 朝 N
  （`flow_direction = input`）；两个 `output`，连接在 (−1,1)、(1,1) 朝 S。种子与 `loader.py` 都还没有这个字段。
- 塑料：20 石油气 + 1 煤 → 2 塑料棒，1 s。化工厂一台 **2 棒/秒**、吃 1 煤/秒、20 气/秒。所以 1/2/4 棒/秒 = 1/1/2 台。
  硫酸：5 硫 + 1 铁板 + 100 水 → 50 硫酸，1 s：一台 50 单位/秒、吃 5 硫/秒、1 铁/秒、100 水/秒。任务书写的"硫酸 1、2 单位/秒"
  不到一台机器的 1/25；按字面做（各 1 台，产能 50/s 过剩写进契约），另加 100/s（2 台）一档，让多台机器的输出汇流真的被测到。
- `utility-constants.default.default_pipeline_extent = 320`；2.1 changelog："max flow is now derived from FluidBox volume"、
  "overextended pipelines"、"Removed LuaEntity::fluidbox"（改为 `get_fluid_segment_*` 一族方法）。

实机结果（全科技解锁，配方 `enabled = true`）：

1. **口位置 × 朝向 × 镜像**（每个外圈格放一根管子读 `get_fluid_box_neighbours(1)`，24 种组合，脚本建与蓝图导入两条路都测）：
   口按 (x, y) → 先镜像 (−x, y) → 再顺时针旋转，方向同样变换——和 `geom.fluid` 对 N 向连接点的旋转一致，只是要加镜像。
   **但流体盒按配方合并**：塑料（1 种液体原料、0 种液体产物）`fluids_count = 1`，两个输入角**都**属于 1 号盒（石油气），
   两个输出角**根本不连**（放管子 `get_fluid_box_neighbours` 为空）；硫酸 `fluids_count = 2`：两个输入角都是水、两个输出角都是硫酸；
   不设配方时 4 个盒各自独立、镜像交换 1↔2、3↔4；双液体原料的 `heavy-oil-cracking`（只作核对，不进种子）：1 号盒（水）在
   (−1,−1)、2 号盒（重油）在 (1,−1)，镜像后对调，输出两个角都是轻油。
   → **塑料和硫酸的口几何对镜像不变**。镜像只在"两种液体原料（或产物）"的配方上改变哪个角是哪种液体。
2. **`mirror` 字段**：蓝图实体写 `"mirror": true`，`import_stack` + `build_blueprint` + `revive` 后 `LuaEntity.mirroring == true`，
   `create_blueprint` 导出时原样写回 `"mirror": true`（未镜像的不写该键）。`create_entity{mirror=true}` 也认。
3. **输入/输出口区分**：输入连接 `flow_direction = input`——两个输入角之间**不能**互通（气从一角进，另一角接的管子 `get_fluid_segment_id`
   不同、下游储罐始终为空）；化工厂的盒子本身不属于任何段（`get_fluid_segment_id` 报 "does not have a fluid segment"）。
   闲置口（塑料的输出角）不与相邻管道连接。
4. **段内流量与管长**：infinity-pipe 源在**同一段**里瞬间把整段（含 25000 的储罐）灌满——段是一个整体，没有"沿管长传播"。
   用泵（1200/s）往段里打：L = 1、10、100、300 根管子后接储罐，储罐段的增量完全相同（63 tick 里 1260，= 20/tick）；
   L = 319、321、400（段外包框宽 321.8、323.8、402.8 > 320）时**一点都不进**（泵 `working`，段里始终没有液体）。
   → 契约里液体速率**不带距离项**；只有一个硬上限：一段的外包框边长 ≤ `default_pipeline_extent`（302.8 能用、321.8 不能用，
   阈值在两者之间，按 ≤ 320 记）。
5. **地下管、储罐**：管–地下管–地下管–管是同一段（`get_fluid_segment_id` 相同）；储罐是段的一部分（段容量 = 管 × 100 + 25000）。
   地下管偏移 1（紧挨着背对背）也配对；同一轴上 W 向地下管之间夹一个 W 向的，外侧那个落单——按最近配对（与 3a 路由器"两端之间
   不得有同轴同名地下管"一致）。化工厂可以压在地下管的跨越段上。
6. **infinity-pipe 作为汇**：过滤 `{name, percentage = 0, mode = 'exactly'}`。和源在同一段时源赢（段始终满）；单独接在化工厂输出段上时把段
   清空，化工厂一直 `working`，1 次合成/秒。**作为计量汇不可用**（读不到吞了多少）。
7. **储罐读量**：`get_fluid(1)` 只返回储罐那一份（段内按容量摊分：段 25200 时罐读 25000）；整段用 `get_fluid_segment_fluid(1)`。
   计量用"每 tick 读整段、累加、`clear_fluid_segment_fluid(1)`"：一台硫酸厂 10 次合成累计 499.9（= 10 × 50）；
   `force.get_fluid_production_statistics(surface).get_input_count('sulfuric-acid')` 同时读到 500，可作交叉核对。
8. 不同液体的管子相邻：`create_entity` 照放、两段合成一段（2.1 只在 GUI 给混液警告）——`fluid.unintended_adjacency` 仍是必要的不变量。

**决定**（由上面的事实推出）：
- 镜像对两个目标配方不改口几何，所以**镜像当参数**（`mirror: bool` 写进化工厂的 `extra`），不当求解变量；朝向当变量。
  几何库仍按"先镜像再旋转 + 按配方合并盒子"实现，镜像对双液体配方照样生效。
- 闲置口在 2.1.23 里不连接；但"配方可以在游戏里改"，所以 `validate` 与求解器都**保守**：闲置口朝向任何流体实体都算违例。
- 液体在段内不限流、不带距离假设；契约给每个液体口记"本单元内这一段已用的外包长度"，路由器连接时检查合起来 ≤ 320。
- 计量汇用储罐 + 每 tick 清段计数（与物品汇箱同一套 per-tick handler），不用 infinity-pipe 汇。

## 迭代 F1：数据与几何库（06:46–06:52）

- 种子：`ADD` 表加化工厂、塑料/硫酸配方、硫/塑料棒、三种液体（新原型类型 `fluid`，只读 `default_temperature`）；
  `extract_seed.py` 新增 `DUMP_CONSTANTS`，从 dump 的 `utility-constants` 读 `default_pipeline_extent` 写进种子常数 `pipeline_extent`
  （其余常数照旧保留）。`loader.py` 字段表加 `fluid_boxes[].production_type`、`fluid_boxes[].pipe_connections`、`use_mirroring`、
  `fluid_boxes_off_when_no_fluid_recipe`。重新生成后旧条目里只有 2/3 型组装机变了（它们也有 `fluid_boxes`，以前没读）。
  种子从 52 KB 涨到 100 KB：脚本按顶层键整段复制，`fluid_boxes` 里带着贴图描述——没去裁剪（裁剪规则会改动别的旧条目）。
- `geom/fluid.py`：`_transform`（先镜像后旋转）、`box_fluids`（2.1 的"同类一种液体灌满全部同类盒"规则，其他数量报错）、
  `machine_ports`（`Port`：格、朝外方向、液体或闲置、角色、盒号）、`entity_ports`（任何流体实体的连接 + 标签，闲置口标 `IDLE`）。
  `connection_points` 也认 `fluid_boxes` 与 `mirror`。
- `validate`：`fluid.unintended_adjacency` 改为按"连接 + 标签"判：两种液体相对，或闲置口对着任何流体连接（保守）；新增
  `fluid.box_unconnected`（配方用到的盒子没有一个口对着同种液体）与 `fluid.underground_unpaired`（地下管最近配对规则）。
  `check_contract` 新增 `contract.fluid`（液体口要指向一个真实连接，带 `temperature` 与 `segment_extent`，且段长 < 上限）。
  路由网格的 `fluid_ports` 也改用 `entity_ports`（机器的闲置口同样挡路由管道）。
- **失败 1**：`fluid.box_unconnected` 第一次把塑料厂报了两次——两个输入盒在游戏里合成一个，我按盒号分组了。改为按（角色, 液体）分组。
- **失败 2（测试）**：旧测试 `test_fluid_adjacency_needs_facing_connections` 里有一个落单的地下管，新不变量 `fluid.underground_unpaired`
  报了它。测试本意是"地下管只在法向一侧连接"，给它补上配对的另一端。
- 测试：`OBSERVED_NO_RECIPE`（8 种姿态的实测口位置表）对 `machine_ports`、单液体配方的合并、2 型组装机无流体配方时无口。
- 环境坑：`uv run` 会按锁文件精确同步、把可选依赖 OR-Tools 卸掉（第一次全量测试 1 个 CP-SAT 用例报 "needs OR-Tools"）。
  全程改用 `uv run --extra solver`。
