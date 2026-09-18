# 自动发表情

对局中按随机间隔发送一个表情。表情是**独立于策略的附加动作**：它不进入模型输入、
不影响观测与决策，也不会为了发表情而取消或推迟任何一张牌。默认**关闭**。

代码位置：

| 文件 | 职责 |
| --- | --- |
| `bridge/emote.py` | 托盘坐标加载与校验；随机间隔调度器；空闲窗口判定 |
| `bridge/actuator.py` | `Actuator.emote()`：把「开托盘 + 点槽位」作为一条命令发出 |
| `bridge/hero_execution.py` | `covered_by_ability_hud(..., margin=)`：技能按钮 HUD 保护（含余量） |
| `config.py` | `EMOTE_*` 常量 |
| `main.py` | `--emote` 参数、循环内的挂载点、失效软着陆 |
| `tools/multi_match.py` | 有界多局；把 `--emote` 透传给每局的子进程 |
| `tools/forever.py` | 无人值守长跑；表情默认开启，可用 `--no-emote` 关闭 |
| `tools/launch_menu.py` | 启动菜单里的「发表情」与多局运行方式 |
| `tools/capture_emotes.py` | 校准：按 `emote_sent` 逐条抓帧 |

## 开启方式

```
python main.py --once --start-battle --emote
python tools/multi_match.py --matches 5 --emote
python tools/forever.py --checkpoint hog26          # 无人值守长跑，表情默认开启
```

`--emote` 是唯一的开关，没有对应的隐藏默认值；不加这个参数时行为与之前完全一致。
启动菜单（`start_agent.bat`）第五步也有「发表情」选项，多局模式在运行方式里选 5 或 6。

可调参数写入 `settings.local.json`（都会经过范围校验，非法值在导入 `config` 时即报错）：

| 键 | 模板值 | 代码兜底 | 含义 |
| --- | --- | --- | --- |
| `emote_min_interval_seconds` | 3.5 | 20.0 | 两次表情的最小间隔 |
| `emote_max_interval_seconds` | 5.0 | 35.0 | 最大间隔；每次在该区间内随机取值 |
| `emote_first_delay_seconds` | 15.0 | 15.0 | 本局第一次表情之前的等待时间 |
| `emote_tray_open_seconds` | 0.12 | 0.12 | 点开托盘到点槽位之间的等待 |
| `emote_ability_hud_margin` | 24.0 | 24.0 | 技能按钮保护区的额外余量（参考像素） |
| `emote_calibration_path` | `emote_calibration.local.json` | 同左 | 坐标文件路径 |

`settings.example.json` 随仓库分发的是 **3.5–5 秒**这一组：它是实测的甜点区间，
所以复制模板后不做任何改动就是长跑节奏。上面的「代码兜底」是这些键缺失时的回退值，
只有在你删掉对应键、或用一份不含表情参数的自定义设置文件时才会命中。

**间隔是下限而不是精确值**：表情只在执行器空闲时才发，所以实际间隔可能被推迟。
实测把间隔设为 2.5–3.5 秒时，实际中位数是 3.29 秒、最大 4.30 秒。

## 无人值守长跑

长时间挂机用 `tools/forever.py`：它在有界的 `tools/multi_match.py` 之上反复起会话，
让「唯一驱动游戏」的仍然是有界工具，同时补上三件长跑必须的事——

| 守卫 | 行为 | 相关参数 |
| --- | --- | --- |
| 日志轮转 | 一局写 70–260 MB JSONL；只保留最新 N 份每局日志 | `--keep-logs`（默认 40） |
| 哨兵停止 | 创建停止文件后几秒内结束，包括会话进行中 | `--stop-file`（默认 `local/STOP_FOREVER`） |
| 空局退避 | 连续多个会话一局都没打完就停止，不无限重试坏掉的模拟器 | `--max-bad-sessions`、`--retry-backoff` |

```powershell
# 默认：每会话 10 局、表情开启、保留 40 份日志
.\.venv\Scripts\python.exe tools\forever.py

# 换模型、换节奏、换输出目录
.\.venv\Scripts\python.exe tools\forever.py --checkpoint active_il --matches-per-session 5 --keep-logs 20

# 演练：只打印解析后的计划，不启动任何会话
.\.venv\Scripts\python.exe tools\forever.py --dry-run
```

停止：`type nul > local\STOP_FOREVER`，或 Ctrl+C。不要发第二个 AI 进程。

## 校准文件

复制 `emote_calibration.example.json` 为 `emote_calibration.local.json`
（后者已被 `.gitignore` 覆盖，不要把自己的 verified 文件打包给别人）。
逐槽位核对的完整步骤见 [docs/CALIBRATION.md](CALIBRATION.md#表情面板)；
其中「每次 `emote_sent` 抓一帧」由 `tools/capture_emotes.py` 自动完成，结尾会汇总出现过的槽位索引。

`verified` 不为 `true` 时表情会被禁用，但**只记一条 `emote_input_disabled` 日志，不影响对局继续运行**。

`emote_slots` 的数量可以是任意个，调度器按索引随机选取。

## 动作序列

一次表情是一条 Android shell 命令，顺序固定：

```
input tap <emote_button>; sleep <emote_tray_open_seconds>; input tap <emote_slot>
```

发完之后不额外点击关闭托盘：选中槽位本身就会收起面板，多点一次反而会触发意外动作。

## 五条设计约束

这些约束都是实机验证后确定下来的，改动前请先读它们的原因。

**1. 必须是单条命令。** `Actuator._command` 在整条命令期间持有 ADB 通道的锁。
如果拆成两次 `tap()` 调用，两次之间会释放锁，一个已排队的下牌动作（它自己也是
「选牌 + 落点」的单条命令）就可能插进「托盘已开、槽位未点」的中间，导致托盘状态错位、
落点被面板吃掉。合成一条命令后串行化由现有锁自动完成，`agent/execution.py` 完全不需要
知道表情的存在。

**2. 挂在决策之后，且只在空闲窗口。** 判定条件是
`executor.future is None and not executor.pending and not executor.fault`——
`future` 是持有 ADB 通道的单线程句柄，`pending` 同时覆盖 queued 与 sent-but-unacknowledged
两种状态。放在决策块之后意味着它永远只填充模型选择「等待」时的空档，不会推迟或抢占决策。

**3. 有 tick 下限。** 门限与策略的首次决策一致（`FIRST_POLICY_DECISION_TICK`，第 90 tick）。
开局过场（宝箱动画）阶段托盘尚不存在，早于该门限的点击会打空，且在那个画面上的点击目标是
未定义的。这个问题在实测中被抓到过：首发延迟设为 0 时，修复前表情会在第 63 tick 发出。

**4. 技能按钮 HUD 保护区带余量。** `ABILITY_HUD_BOUNDS` 是英雄技能按钮的触控区。
上游参考坐标里托盘的第四列（x=869）距离该保护区左边界只有 6 像素，而误触技能会消耗 3 圣水，
因此本项目**不采用该列**，并且守卫使用 24 像素余量：落在余量内的槽位会被拒绝
（抛 `ValueError`），而不是发出去。

**5. 失败不重试。** 任何 `RuntimeError` / `OSError` / `ValueError` 都会禁用本局剩余的表情并记
`emote_disabled`，然后继续打牌。结果不确定的点击再点一次只会让状态更糟——这与
`bridge/actuator.py` 既有的「不盲重试」原则一致。表情是附加项，**任何情况下都不应该让对局崩溃**。

## 实测行为

在 1080×1920、Null's Royale 15.535.13、大头锤卡组（`--checkpoint active_il`）的联机对局上实测：

| 观测 | 数值 |
| --- | --- |
| 单次表情耗时 | p50 160 ms（范围 154–179 ms），其中 120 ms 是托盘展开等待 |
| 决策节拍影响 | 间隔 20–35 s（14 次/局）：98.0% 决策仍严格 5 tick<br>间隔 3.5–5 s（39 次/局）：95.5%<br>间隔 2.5–3.5 s（79 次/局）：91.5% |
| 动作损失 | 三局合计 85 次出牌，**85 次全部落到场上**（`spawn_observed`），零 `ack_timeout`、零 `action_rejected` |
| 最紧邻接 | 出现「表情结束 → 出牌开始」仅隔 133 ms 的情况，该次出牌依然正常落地 |
| 游戏端限速 | 未观测到。3.2 秒间隔下逐次抓帧，69 帧全部能看到表情真的显示出来 |

机制解释：单次表情占用主循环约 160 ms，最多把一次决策推到下一个 tick 边界（50 ms），
不会取消动作。占用比例约为 `0.16 / 间隔`，因此 3 秒间隔约 5%，1 秒间隔约 16%。

**频率建议**：3–5 秒是甜点区间。低于约 2 秒收益递减（面板会互相覆盖）且循环占用超过 8%。

## 日志事件

| 事件 | 含义 |
| --- | --- |
| `emote_sent` | 点击已送达。含 `index`、`button`、`slot`、`input_ms` |
| `dry_run_emote` | `--dry-run` 下只记录坐标，不点击 |
| `emote_input_disabled` | 坐标文件缺失或未 verified；本局不发表情，对局继续 |
| `emote_disabled` | 某次点击失败或被守卫拒绝；本局剩余表情停用 |
| `ready` 的 `emote_enabled` 字段 | 本局是否启用了表情 |

## 支持边界

- **已验证**：1080×1920 竖屏、上述游戏版本、大头锤卡组、六个槽位逐一视觉确认。
- **未验证**：其他分辨率与横屏（校准文件的 `size` 与设备宽高比不符会被直接拒绝，不会猜）；
  换卡组不需要重新校准（表情与卡组无关）；其他游戏版本需要重新核对面板坐标。
- 表情**不参与**模型观测与动作契约，因此对策略强度的影响只来自上面那条决策延迟，
  不构成棋盘信息的改变。
