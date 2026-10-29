# 6a 任务 1 实验记录：采矿单元的电杆占位损失

开始：2026-10-29T03:21Z（执行者 Sonnet 5.5，分支 exp/6a-replication-sonnet）

基线（改进前，来自 `characterization/mining_patch.jsonl`，已存为 `characterization/mining_patch_before.json`）：

| case | 钻机 | pole_slot |
| --- | --- | --- |
| rect-1belt | 15 | 5 |
| rect-2belt | 30 | 10 |
| l-shape | 40 | 12 |
| rect-hole | 54 | 14 |
| blob | 41 | 11 |
| rect-axis-y | 61 | 9 |
| lane-capacity | 30 | 8 |
| real-map | 32 | 10 |
| 合计 | 303 | 79 |

验收线：pole_slot 总损失 <= 79/3 = 26.3，即 <= 26；钻机数不减；实测偏差 <= 2%。

## 迭代日志

