# 实验 6a 任务 2：契约补全与生成器、路由器的联动改造

有明确规格的普通工程任务，作为任务 1（只有目标）的对照。记录同样写假设、改动、validate、实测、失败。
开始时间：2026-10-24 20:03 UTC。

## 迭代 0：盘点旁路 + 建离线回归指纹（20:03–20:15）

`grep` 出路由器/放置/夹具"回头查布局"的地方：

| 位置 | 查什么 | 用途 |
| --- | --- | --- |
| `route/connect.py::_consumers` | `belt_at(消费者布局, 口格)` | 消费者带等级（桩用什么带、道容量） |
| `route/connect.py::_consumers` | 同一单元、`i.tile == dst.tile` 的所有物品口 | 按格把矿口和煤口归成一个消费者 |
| `route/connect.py::_belt_checks` | `belt_at(生产者布局, 口格)` | 生产者带比消费者慢的警告 |
| `route/power.py::power_poles` | 口格下的电杆实体 | 杆种（拉线距离）和杆中心 |
| `sim/harness.py::attach_fixtures` | `belt_at(...)`；口格下的电杆 | 夹具用什么带、电源夹具用什么杆 |
| `site/place.py` | `PORT_CLEARANCE = 3` | 口前留空格数 |
| `route/belt.py` | `RESERVE = 2`；`link_mode` 按 `lane` 推直连/侧向 | 口前保留格；进入方式 |
| `route/belt.py::consumer_report` | `rate is None` = 要多少给多少；`both` 口"每道至多一半" | 容量与 MinRate 检查 |
| `connect.py`、`site_sweep.py` | `rate or 0.0` | 估算 |

`assumptions` 只在 `gen/sweep.py` 里格式化成文字进 .md，没有算法读它；保留。
`route/pipe.py::_ports` 读单元布局里的液体连接点，用来判断"自己的"连接不算外来——这是避障用的布局信息
（阶段 3a §6 结论里"单元内部的带和管从合并布局推出"那一类），不在本任务 5 项之内，不动。

回归手段：整站实测要求"不变"，先做离线指纹。起一次服务器各取一份平地和真实地图（清障后）的地形快照存成
pickle（之后不再起服务器），离线重建两个整站 + `connect`，对"单元 + 路由"的全部实体做哈希；再对 8 个采矿
用例和 12 个冶炼列参数组合的测量夹具（`harness.plan`）做哈希。改造过程中每一步对比指纹：指纹不变 ⇒ 部署的
实体完全相同 ⇒ 实测必然不变。

顺手试了两个数值（只改常数、看指纹）：`RESERVE` 2→3、直连口留空 3→2，指纹都不变。所以字段可以用诚实的
数值（直连口 2 格、侧向汇入口 3 格），不必为了凑旧行为统一写 3。

## 迭代 1：字段设计（20:15–20:18，纸上）

| 任务书条目 | 字段 | 谁填 | 谁读 |
| --- | --- | --- | --- |
| 1 实体 | `Interface.entity: str \| None` | 两个生成器、全部夹具（物品口 = 口格上的带，电口 = 杆，液体口 = 承载连接的实体） | 消费者桩的带、道容量；慢带警告；电杆链的杆种与中心（新 `footprint.center_at_tile`）；夹具选带、选杆 |
| 2 按道速率 | `RateProfile.lanes`（tuple 形式以便 frozen），`Interface.lane_rate()` | 采矿（按两侧钻数）、冶炼列（输出远道、输入单道口）、源/汇夹具（每道上限） | `consumer_report`：直连按分道计入，侧向汇入整股进一道 |
| 3 按需 | `RateProfile.supply = fixed \| on_demand`、`limit`；`fixed()`/`on_demand()` 构造；`Interface.on_demand()`、`fixed_rate()` | 夹具 | `consumer_report`、`capacity_suggestions`、`site_sweep` |
| 4 分组 | `Interface.port_group` + `Unit.groups: tuple[PortGroup]`，`PortGroup(name, requires)` | 冶炼列 `feed`（LaneMaterial 左矿右煤）/ `out` / 双侧时 `out-mirrored`；采矿每条带一组；夹具一组 | `connect._consumers` 按 (单元, 组) 归消费者 |
| 5 空间 | `Interface.space = PortSpace(approach, upstream)`；`PortSpace.item(approach)` 用约定常数 `SIDE_LOAD_STUB`、`PORT_LEAD` | 全部物品口 | `link_mode`、`reserve_ports`、`place.port_tiles` |

另加不变量 `contract.entity / space / rate / group`（`validate_unit` 里的 `check_contract`），让生成器自检字段。
镜像变换要同时翻转分道速率与组要求的道。

## 迭代 2：实现（20:18–20:30）

- 改动：`layout/model.py`（新字段与构造）、`layout/transform.py`（带上 groups、翻转 lanes）、两个生成器、夹具；
  路由器 `connect`/`belt`/`power`、放置 `place.port_tiles`、夹具 `attach_fixtures` 改为只读契约；删除 `belt_at`、
  `RESERVE`、`PORT_CLEARANCE`。`route/pipe.py::_ports`（扫单元布局里的全部液体连接）顺手改成只取链路两端接口——
  原来把单元里所有液体连接都当"自己的"，其实更宽松、可能错；改后管道测试全过。
- 离线指纹：两个整站的实体哈希与 12+8 个测量夹具哈希**全部不变**；只有契约校验的顺序/标签变了
  （组要求先报，记为 `row/feed`）。
- 测试第一轮：1 个失败（`test_direct_route_around_water`：测试辅助单元没有 `entity`，路由器按设计拒绝——
  说明路由器确实不再查布局），更新 `tests/route_helpers.py` 后 2 个失败：两个旧测试从口上取 `lane_materials()`，
  改为从组上取。
- **不变量起作用的一次**：加上 `contract.*` 后 18 个采矿测试失败——采矿口给单道口也写了另一道 `0.0` 的分道速率，
  `contract.rate` 判为"分道速率里出现了口不用的道"。修生成器（只写有钻的道），不放宽不变量。
- 新测试 `tests/test_contract.py`（17 个）：画像构造与读取、`PortSpace` 约定、镜像翻转、两个生成器与夹具填全字段且
  `check_contract` 为空、每条不变量各自报错、`belt_at`/`PORT_CLEARANCE`/`RESERVE` 已不存在、消费者桩用契约里的带
  （故意与布局不同）、按组而非按格归消费者（去掉组就变成同格两个桩而失败）、分道速率能查出单道超载（旧的"一半"
  估算会放过）、`rate=None` 不再是按需、`approach` 决定直连/侧向、放置按 `upstream` 留格、电杆链按契约取杆与中心
  （含 2×2 的变电站）。
- 全部测试 282 过（headless 53），ruff 干净。

## 迭代 3：整站实测（`fgen sweep site --measure --real-map`，27 s）

- 结果与任务 1 结束时的整站实测逐位相同：平地矿 7.508、煤 0.525、板 7.5；真实地图钻 7.5、矿 7.478、煤 0.533、板 7.467
  （99.6%）。`site.md` 只有契约校验那一行的文字变了。

## 统计（任务 2）

- 迭代：4 次（0 盘点与指纹、1 设计、2 实现、3 实测）。
- `validate` 失败：1 类——`contract.rate` 18 次（同一个原因，一处修复）；其余 0。
- 测试失败：3 次（测试辅助缺字段 1、旧测试从口上取道要求 2），都是测试跟随契约变化，不是行为回归。
- 实测不达标：0 次。
- 耗时：约 20 分钟（20:03–20:23）。

## 对照任务 1

- 任务 2 规格明确，几乎没有"试"：唯一的设计自由度（直连口留几格）用两次只改常数的指纹对比就定了。任务 1 的
  时间主要花在压力变体和性能上，任务 2 的时间主要花在逐个改调用点。
- 离线指纹是任务 2 最有用的工具：它把"整站实测不变"变成了每次改动后 10 秒内可查的事实，实测只在最后跑一次。
- 新加的契约不变量一上来就抓到生成器的一处不一致，说明"字段由生成器填、不变量自检"这个分工是对的。
