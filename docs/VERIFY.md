# 待实机核对清单（VERIFY）

（M0 起草，M5 收尾补全。）

| 条目 | 当前假设值 / 约定 | 所在文件 | 核对方法 |
| --- | --- | --- | --- |
| 种子数据全部数值 | 凭记忆手写 | `src/fgen/data/seed/vanilla-2.0.json` | headless `--dump-data`，用 `load_dump` 加载后与 `load_seed` 逐字段对比 `fields_used()` |
