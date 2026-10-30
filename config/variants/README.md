# 变体文件与对照表（阶段 9 任务 B）

模型可靠的长处不是想出一个新排法，而是**同时开几条路、按硬指标并排看、选一个**。这个目录把每一次这样的选择写成一份**变体文件**；`fgen compare` 把文件里的每个成员各放一个进程、各给各的预算去生成，写出一张只含硬指标的表到 `compare/<name>.{jsonl,md}`。凡是进仓库的选择（钉住的参数、答案文件、任务书里的"取 X"），都要指向它所依据的那一行（`compare/<name>.jsonl` 的 `variant` 与 `blueprint_sha`），不能凭记忆钉。

```sh
uv run --extra solver fgen compare config/variants/circuit-smoke.json            # 穷尽后端，秒级，演示用
uv run --extra solver fgen compare config/variants/green-chain.json --procs 2     # 绿瓶链：当年手钉 GREEN_CHAIN 回答的那道题
uv run --extra solver fgen compare FILE --only a,b --rank cheap --out-dir /tmp/x # 只跑几个成员、换排序、换输出目录
```

退出码：0 有可行成员；2 一个都不可行。进度一行一个成员打到 stderr（并行时按完成顺序）。

## 文件格式

```json
{
  "name": "green-chain",
  "family": "direct-chain",
  "base": {"recipe": "logistic-science-pack", "target_rate": 0.5, "backend": "cpsat", "time_limit": 60},
  "variants": [
    {"name": "pinned-4b", "params": {"palette": "given", "intermediates": "lane"}, "note": "为什么试它"}
  ],
  "grid": {"palette": ["full", "given"], "intermediates": ["lane", "auto"]},
  "rank": "compact",
  "asked_by": {"who": "claude-…", "date": "2026-10-30", "question": "这组变体要回答什么"}
}
```

- `family`：`direct-chain`（`DirectChainParams`）或 `smelting`（`SmeltingRowParams`）。采矿要地形与矿区，待任务 B 加。
- `base`：所有成员共用的参数；`variants[].params` 与 `grid` 是在它上面的覆盖。字段名必须是该参数 dataclass 的字段，写错直接报错（否则一个错字就是一个默认值，表就白跑了）；JSON 列表在元组字段上自动变元组。
- `grid`：笛卡尔积，成员名由取值拼成（`intermediates=lane;palette=full`）。`variants` 与 `grid` 可以同时给。
- `rank`：`compact` / `cheap` / `simple` 用该预设的词典序项（`fgen.solve.preset.PRESETS`）比周期指标；没有周期的家族只比面积；`first` 比到首个周期的秒数。只是排序，硬约束不变。
- `asked_by`：谁、何时、为了回答什么问题开的这组变体。表头会打出来。

## 表里有什么、没有什么

有：状态（`ok` / `infeasible` / `error`）与原因、墙钟秒、到首个周期的秒数、求解秒数与是否证明、周期尺寸 / 杆 / 爪 / 管、成本、种类数、单元面积、机器数、预测产率、蓝图哈希、渲染与契约。全是 agent 改不了语义的量。

没有：生成器自报的诊断、任何坐标、"好不好"的判断。判断是读表的人或 agent 做的，做完把选的行写进它的产物（答案文件、任务书、代码注释）。

## 机器与可复现性

- 随时可用驱动的时间盒是**确定性预算**（CP-SAT 确定性时间折算），所以同一成员单独跑和并排跑给同一个周期；并排只改墙钟。墙钟上限（`WALL_FACTOR`）在机器被占满时仍可能被触到，那时结果会变，表头记了前后负载，任务 C 要把这条路堵上。
- 一个 CP-SAT 模型约吃 1.5 核：4 核机器 `--procs 2` 为宜；默认取核数的一半。成员进程里证明驱动自己的进程池关掉（`cpsat.PROCS = 1`），不嵌套。
- 成员多于进程数时排队；同一张表里的成员各自独立，没有共享预算、也不会因为一个成员先出解而取消别的（那是任务 A 要做进求解器里的事：组合驱动、先出解者赢）。
