# 站点的判断题与答案文件（阶段 8 任务 A）

站点里有两件事是**偏好**而不是计算：每种矿选哪个矿区，放置时带长、转弯、绕回、占矿之间怎么换。`fgen ask site` 把它们写成一份问题文件（候选 + 事实 + 默认答案 + 每个旋钮的含义），回答者（人或 agent）写一份答案文件放在这个目录，`fgen sweep site --real-map --decisions config/decisions/<name>.json` 按它规划、实测，行名加后缀 `@<name>`。没有答案文件就是默认（最近的矿区、默认权重），与阶段 7 的行为逐字节相同。

## 问题文件（`fgen ask site --product logistic-science-pack --rate 1 --out q.json`）

```json
{
  "product": "logistic-science-pack", "rate": 1.0, "window_s": 600,
  "patches": {
    "iron-ore": {
      "demand": 5.5,                       // 本站从这种矿要的矿石，件/秒（按契约算出）
      "default": "iron-ore@(-66,35)",      // 现行规则的答案：离出生点最近的 ≥50 格矿区
      "candidates": [ { "id": "iron-ore@(-66,35)", "tiles": 352, "total_amount": 156978,
                        "bounds": [[-66,35],[-38,63]], "size": [29,29], "centroid": [...],
                        "distance_to_spawn": 51.2, "mixed_ore_tiles_at_edge": 0,
                        "water_tiles_within_45": 0, "blocked_tiles_within_45": 12,
                        "distance_to_other_candidates": {"copper-ore": [["copper-ore@(-2,61)", 36.0]]},
                        "mining_at_demand": { "demand": 5.5, "drills": 12, "belt_rate": 5.5,
                                              "first_dry_s_lower_bound": 9000, "est_depletion_s": 28541,
                                              "dropped_by_reason": {...}, "params": {...} } }, ... ]
    },
    "copper-ore": { ... }
  },
  "place_costs": { "default": {"step": 1, "turn": 2, "detour": 2, "resource": 2, "slack": 12},
                   "meaning": { "step": "...", ... } },
  "order_priority": { "default": ["unit", "placed_neighbours", "linked_rate"], "meaning": "..." }
}
```

事实只来自地形快照、契约和生成器：`mining_at_demand` 是采矿生成器在该候选上按本站需求（一条带、窗口内不枯竭）实际跑出来的结果。问题里**没有坐标让你填**。

## 答案文件（`config/decisions/<name>.json`）

```json
{
  "name": "haiku",
  "patches": { "iron-ore": "iron-ore@(101,192)" },      // 候选 id；没写的资源取默认
  "place_costs": { "resource": 4.0 },                      // 只写要改的权重；没写的取默认
  "order_priority": ["unit", "linked_rate", "placed_neighbours"],   // 可选，三个键的一个排列
  "answered_by": { "who": "claude-haiku", "date": "2026-10-30", "given": "q.json + this README" },
  "rationale": "一两句：为什么"
}
```

回答时该权衡的（没有标准答案）：储量 vs 到出生点 / 到另一种矿的距离（带越长，路由越慢、填充越久、占地越多）；`first_dry_s_lower_bound` 与 `est_depletion_s`（窗口内枯竭的候选会被生成器抬高 `min_ore` 而丢钻机）；混矿与周围的水 / 不可建格（放不下冶炼列和链）；`resource` 权重（压在矿上不违法，但挡住以后采矿）；`slack`（窗口越大候选越多、越慢）。

评价答案只看 agent 改不了语义的硬指标：路由带格与地下带数、钻机数、`first_dry_s_lower_bound`、`fill_time`、放置重试次数、实测产率与偏差。
