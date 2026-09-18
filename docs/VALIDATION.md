# 验证记录

除另有注明外，下面的实机记录来自同一台 Windows 电脑上的 Root MuMu 实例：
推理设备为 CUDA（RTX 5080），torch `2.11.0+cu128`，游戏构建为 Null’s Royale 15.535.13。

## 发布整理之前的基线（2026-09-13）

- 213 项 Python 测试通过。
- 稳定探针重编译与此前实机验证二进制逐字节一致；当时的稳定 SHA-256 为
  `9d1c8d79712c7116e27c324dd9bcbd85d6be07923e7c8b989c61bee57cd095b0`（该产物已于
  2026-09-18 因模拟器更新被替换，见下文）。
- 真实治疗对局回放：速猪和 General、reference 和 extended，各 664 次推理，共 2656 次，无推理错误。
- 相同回放两方案动作不同不代表胜率提升，动作不会反过来改变记录中的对局。
- 旧战斗回放各 679 次张量化通过；军团回放 599 次张量化，四组各 15 个成员、同时存活与阵亡减员保留。

原始对局、截图、账号和机器日志未包含在发行包中。以上是已有基线证据，不是别人电脑上的安装验收。

## 发布副本验证

2026-09-13，在同一电脑的新 MuMu 实例上完成独立 `.venv` 安装：
`setup.ps1` 从公开包源安装全部依赖，未复用旧环境的 site-packages。
未装探针的 ARM64 游戏通过指纹检查；发布安装器保存原始 SDK，安装稳定代理并重启，成功收到大厅遥测。
新环境完成 224 项原有测试与双席位 6 次 CUDA 推理；修复 Git 换行符兼容后完整 226 项测试通过。
随后在该实例完成账号识别、坐标校准和整局自动执行，详见下述记录。

后续已从真实对局核对新账号、手牌、静止加农炮地面位置与单英雄技能按钮，
观测进入真实模型并产生合法技能候选。首局 AI 的 11 次下牌全部收到手牌变化确认；
对局在 tick 1847 附近出现网络重连，旧版 `--once` 因遥测暂停提前退出。
现已修复该退出路径，保留同局恢复、禁止重发已提交动作、检测新局后退出；
同时允许读取冻结 tick 的确切终局证据，并在旧结果页启动时等待下一局。
包括这些边界及 ADB UTF-8 输出在内的完整 234 项测试通过。

## 新实例整局复测（2026-09-13）

修复后，AI 从旧结果页等待下一局，于 tick 1 识别开局，完成 474 次决策。
21 次触摸动作完成发送，其中 20 次收到手牌确认并观察到部署对象；最后一次动作在确认前遇到终局，
记录为未确认，未在终局后重试。觉醒小骷髅完成一次觉醒确认；本局没有英雄技能触发，
不将此前的合法技能候选作为技能点击成功的证据。

原生终局 tick 2457，比分 3:0，AI 获胜。程序识别终局并按 `--once` 正常退出。
本次没有遥测暂停或断线，因此同局重连恢复的覆盖来自回归测试，不是这局实战。
此前网络中断的根本原因尚无充分证据；已修复的是 AI 将遥测暂停误判为单局运行结束的行为。

检查摘要见 [release-validation.json](../release-validation.json)，
文件清单与校验和见 [SHA256SUMS.json](../SHA256SUMS.json)。
安装器的备份、恢复、错误库拒绝和失败处理另有模拟 ADB 测试；实机验证覆盖上述新实例安装与整局运行。

新实例与旧实例位于同一电脑，另一台物理电脑未测试。
发布状态仍为 preview；单局通过不代表全卡兼容、胜率提升或时延改善。

## 源码与离线产物复跑（2026-09-15）

本节是在**同一台电脑上的只读检查**，未接触模拟器、未安装探针、未进行对局，因此不是实机验收。

| 检查 | 命令 | 结果 |
| --- | --- | --- |
| Python 测试套件 | `python -m unittest discover -s tests -q` | **258 项通过，0 失败**（耗时约 16 秒） |
| 离线探针产物检查 | `powershell -ExecutionPolicy Bypass -File .\probe\test_artifacts.ps1` | **10 项通过**，未执行任何 ADB 命令 |

测试在 Python 3.12 下运行，torch 为 CPU 版本（`2.5.1+cpu`），不是发行默认的
`2.11.0+cu128`。项目自身的 `.venv` 在本次复跑时尚未创建，因此**未覆盖**：
设备检查、探针安装与恢复、GPU 上的张量计算、真实模型的 CUDA 推理、以及实战对局。
上一节 2026-09-13 的实机结论仍然有效，但本次没有重现它们。

测试数量从记录中的 234 项增加到 258 项，是套件自身有新增，不是同一批测试的重复计数。
用无关的系统解释器运行该命令会因缺少 torch 而报出与真实原因无关的导入错误
（`'test_pipeline' module incorrectly imported`），必须使用 `.venv` 的解释器。

本次还发现两项与发布相关的问题，均已在同日处理，处理过程同样没有实机验证：

- `SHA256SUMS.json` 与内容不一致：13 个文件的哈希已过时，`tools/package_release.py`
  因此拒绝运行。逐个比对了这些文件与上一版发行副本的差异后（其中 5 个源码文件与副本逐字节一致，
  其余只是内置上游路径与权重目录的重命名，未发现个人数据或开发机路径），已重算并更新。
- `tools/multi_match.py` 未列入该清单。审查源码后已加入，它与清单中原有的运行时文件一样
  只通过 `config` 读取路径与账号，不含硬编码的个人信息。

处理后 `reviewed_files()` 对 129 个受控文件全部通过，实际打包产出 130 项的 ZIP
（含 `SHA256SUMS.json` 本身），内容与规则一致：不含 `weights/`、`upstream/`、日志与个人配置，
`.bat` 为 CRLF 而文本文件为 LF，稳定探针二进制逐字节一致。
该 ZIP 仅用于验证打包链路，验证后已删除；需要时重新运行 `tools/package_release.py` 生成。

**以上都只是文件与离线检查层面的结论。** 本仓库仍未在本次复跑中验证：
设备连接、探针安装与恢复、GPU 推理、以及真实联机对局中的下牌与执行确认。

## 全新克隆复现（2026-09-15）

为了确认新用户仅凭仓库内容就能走完离线流程，从仓库克隆了一份全新副本（约 460 MB，含 `.git`），
在副本上执行下表检查。**全程未连接模拟器、未安装探针、未进行任何对局。**

| 检查 | 命令 | 结果 |
| --- | --- | --- |
| 全量编译 | `python -m compileall`（本项目代码与内置上游运行时） | 通过 |
| 推理链导入 | 逐个导入 `config`、`bridge.*`、`agent.*` | 通过 |
| 回归测试 | `python -m unittest discover -s tests -q` | **258 项通过** |
| 启动检查 | `tools/preflight.py` | `probe`、`upstream`（提交 `28d66cc0a5d6`）、`catalogs`（122 张卡牌规格）、`calibration` 通过；`adb` 与 `account` 因未配置而失败，符合预期 |
| 探针本地校验 | `probe/deploy_probe.ps1 -Check` | 稳定探针 `9d1c8d79…095b0`，目标游戏库 `110aa2b5…59783` |
| 坐标叠图工具 | `tools/coordinate_view.py`，输入合成 1080×1920 图 | 已生成 HTML |
| 前置保护 | 未设账号、未确认校准时启动 `main.py` | 按设计以 `ValueError` 拒绝并给出可操作提示 |
| 换行符 | 克隆后检查 `.bat` 与文本文件 | `.bat` 为 CRLF、文本为 LF，与打包规则一致 |

此外核对文档里引用的 54 个脚本路径全部存在，10 个被文档化的命令行参数在当前 `--help` 中有效。
依赖方面确认 `setup.ps1` 固定的 `torch==2.11.0` 在 CUDA 12.8 索引中确实存在（`2.11.0+cu128`）；
`requirements.txt` 的各项在本机因代理故障无法向 PyPI 解析，**本次未能验证**。
上游自带的 `native_runner/tests/` 已随本次裁剪移除（它依赖未安装的 pytest，本项目从不运行它）；
本项目的回归测试全部在 `tests/` 下，用标准库 `unittest` 运行。

**本次仍未覆盖**：设备连接、探针安装与恢复、GPU 推理、真实对局中的下牌与执行确认。
所以"新用户能跑通完整流程"目前只对离线部分成立，实机部分必须在有可用实例的机器上验证。

## 模拟器更新后的探针适配与实机验证（2026-09-18）

本次在同一台电脑的 Root MuMu 实例（MuMu 12 6.6.4.0，更新替换了 `system.vdi`）上完成，
推理设备为 CUDA（RTX 4070 Laptop），torch `2.11.0+cu128`。

**现象**：更新后探针的 TCP 服务正常，但两个关键钩子安装失败：

```
E NullsProbe: Prologue mismatch for GameStateManager::step at 0x830e0a4
E NullsProbe: Prologue mismatch for BattleController::fullUpdate at 0x7f34bbc
```

这两个钩子是 `g_in_battle` 的唯一写入点，所以探针永远返回 `{"in_battle":false}`，
AI 一直 `waiting: idle`，联机对局无法接管。

**根因**：更新后的 MuMu ARM 转译器会在**已翻译的热函数入口就地写入自己的绝对跳转桩**：

```
ldr x17, #8  ; 0x58000051
br  x17      ; 0xd61f0220
.quad translated_code
```

排查依据：设备 `libg.so` 哈希仍等于支持指纹，且**文件**在该偏移处正是探针期望的原始序言，
但**运行时内存**已是上述桩；桩的跳转目标每个进程都不同。因此失配来自运行时改写，
与偏移常量、基址（与 `/proc/maps` 首行一致）、页大小（4096）均无关。

**适配**：`install_inline_hook` 现在把转译器桩视为合法入口状态 —— 复制这 16 字节进 trampoline
后，桩自身会跳到转译代码，续跳链路保持不变。所有会预先校验入口的钩子站点
（`deployment_timing.inc`、`causal_deployment.inc`、`spawn_relations.inc`）改用同一个
`entry_matches()` 判定，避免它们继续拒绝热函数。

**实机验证**（当轮稳定探针 `7cc0df2012ea5eb2cd98c26f3d9b79caa867e39e70a0b18759def66e58a98013`，
已被下述第二轮取代）：

| 检查 | 结果 |
| --- | --- |
| `tools/preflight.py --model active_il` | 9 项全 PASS，0 failure |
| 钩子安装 | `GameStateManager::step`、`BattleController::fullUpdate` 以及 consume / spawn / impact / damage / heal 全部经转译器桩安装成功 |
| 探针绑定对局 | 点击「对战」后约 4.5 秒返回 `in_battle:true`、`tick` 推进、`entities` 6 座塔，含圣水与手牌 |
| 整局只观察（`--dry-run --once --start-battle --emote`） | `battle_start`（tick 2，读到 8 张卡组与双方塔资产）→ 模型预热 → 每 5 tick 决策 → 动作解码含格点/屏幕/世界坐标 → 终局 `crowns [0,3]` 正确识别 → 干净退出 |
| 表情调度 | `dry_run_emote` 按 3.5–5 秒间隔触发，空闲窗口外自动顺延 |

**本次仍未覆盖**：真实下牌与执行确认（本次为只观察）、`tools/forever.py` 的整夜长跑。
自动对战的端到端仍以更新前 2026-09-18 凌晨的 5 局实机记录为准。

### 第二轮：补齐 attack_edges，恢复攻击相位遥测（2026-09-18）

第一轮只修了 `install_inline_hook` 的中央判定，**漏掉了 `probe/attack_edges.inc` 里自己的入口守卫**：

```c
for (int i=0;i<5;++i) if (memcmp(libg_base+offsets[i],bytes[i],16)) return false;
```

这 5 个入口与 `libg.so` 文件逐字节一致，但运行时**全部**被转译器写成跳转桩，因此该组整体放弃
（`install_attack_edges -> 0`），并因 `g_effect_origin_ready = g_attack_hooks_ready && …` 的短路
连带跳过了 `effect_origin`。

把更新前 6 局日志与第一轮后的一局逐项对比，确认这是**真实退化**（不是装饰）：

| 观测项 | 更新前 6 局峰值 | 第一轮后 | 本轮修复后 |
| --- | --- | --- | --- |
| `attack_phase_known_count` | 7–17 | 0 | **6** |
| `attack_event_count` | 10–17 | 0 | **3** |
| `effect_runtime_known_count` | — | （被短路跳过） | **13** |

`attack_phase` 不是装饰项：`training/v4/config.py` 的特征表含 `attack_phase_remaining_over_5000ms`，
而 `bridge/runtime_state.py` 用探针的 `edges` 数组推导该相位。缺了它，模型输入即落在训练分布之外。

**修法**：该组 4 个标准钩子改用共享的 `entry_matches()`；`kActionDispatchOffset` 是手写钩子
（把入口 `+4` 起 12 字节拷进 trampoline、跳到 `+16`，靠 `if (action) g_dispatch(...)` 复现原始
`CBZ x2` 分支），入口为桩时那 12 字节是桩的中段，语义不成立，因此**该单个钩子跳过**并记一条
`attack dispatch entry holds a translator stub … skipped`。

**实机验证**（稳定探针 `a63b1826b87cd390b08a0fc69884830b16f3fcbad4cc88dcb01edf964a18530c`）：

- 9 个钩子组全部安装（`install_attack_edges`、`install_effect_origin_observers`、
  `Attack edge hooks ready` 均由 0 变为 1）
- 一局真实自动对战（`--checkpoint active_il --emote --once --start-battle`）：
  857 次决策、`action_queued` 26 → `hand_ack` 25 → `spawn_observed` 25、
  表情 45 次、觉醒形态 3 次、终局 `crowns [0,1]` `result=win`
- `attack_phase_known_count`、`attack_event_count` 回到非零

注：本局实体峰值为 7，战斗规模小于此前 6 局（实体 12–37），所以这两项的绝对值低于当时区间，
属战斗规模差异，不是仍缺数据。
