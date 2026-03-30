# Factorio Agent 时间控制机制 (Time Control)

## 核心痛点
由于 Agent 调用大模型思考的过程需要数十秒，这段“现实时间”如果对应到游戏内的“运行时间”，会导致游戏内部流失大量 Ticks。这不仅使得 `ticks_elapsed` 指标被污染，还可能导致基地在 Agent 思考期间被异星虫族摧毁。

## 解决方案：回合制异星工厂 (Bullet-Time)
为了让 Agent 获得完全与现实时间脱钩的控制权，我们在 Agent 的 Mod 层引入了“时间锁”机制。

### 1. 服务端配置 (`server-settings.json`)
设置 `"auto_pause": false`。
默认情况下，Factorio 无头服务器在 0 玩家连接时会彻底挂起 (Suspend) 游戏引擎。如果引擎挂起，Mod 脚本也将无法通过 tick 计数来恢复或推进时间。因此，我们必须让引擎在无人时依然保持运转。

### 2. Mod 级时间锁 (`control.lua`)
在 Mod 的初始化事件 (`on_init` / `on_load`) 中，立即执行：
```lua
game.speed = 0
```
或在 Factorio 最新 API 中配置 `game.tick_paused = true`。
此时，引擎循环依然在运作，RCON 监听也能正常收发数据，但原版游戏世界的流逝速度变为了 0（传送带、虫群、机器全部静止）。

### 3. 精准步进 (Step Execution)
当 Agent 完成一轮决策并调用 `advance.lua` 要求经过 N 个 ticks 时：
1. Mod 将游戏时间恢复：`game.speed = 1`。
2. Mod 注册一个 `on_tick` 事件监听。
3. 当且仅当经过了 N 个 ticks 后，Mod 触发回调函数，再次执行 `game.speed = 0`。
4. Mod 通过 RCON 返回成功信号给 Agent。

### 总结
这种机制剥离了大模型推理速度对游戏状态的惩罚，实现了 100% 的确定性。此时的 Factorio 从一个即时战略游戏 (RTS) 降维成了一个回合制策略游戏 (TBS)，为基于强化学习或基于 LLM 的 Agent 提供了极度干净的测试台。
