# 原生探针

探针是本项目唯一的感知来源。它替换游戏进程加载的一个 SDK 库，
在游戏内部挂钩若干函数，把原生战斗状态通过 TCP 端口暴露给主机侧的 `bridge/probe_client.py`。
**它同时是整条链路里最容易因为环境变化而失效的一环**，所以单独成文。

## 它是怎么装上去的

游戏安装目录下有一个 MuMu 自带的 `libscid_sdk.so`。安装器把它改名保留，再用探针顶替：

```
lib/arm64/libscid_sdk.so        ← 探针（对外仍是原文件名，游戏照常加载）
lib/arm64/libscid_sdk_real.so   ← 被顶替的原版 SDK，探针启动后转发给它
```

原始 SDK 同时会在 `local/backups/<key>/` 留一份备份（`key` 由序列号与安装目录派生），
`probe/deploy_probe.ps1 -Restore` 就是靠它还原。

安装器在动手前会校验三件事，任一不符就**不做任何修改**：游戏库 `libg.so` 的指纹、
备份的身份与校验和、传输过去的字节。目标文件通过 `.new` + `mv` 原子替换，
所以不会出现"半截文件"的状态。

## 它挂了哪些钩子

钩子分为九组。前两组的失败会直接让流水线失去作用，其余影响遥测完整度。

| 组 | 钩子名 | 提供什么 |
| --- | --- | --- |
| 状态 | `GameStateManager::step`、`BattleController::fullUpdate` | 整个战斗快照的来源；`g_in_battle` **只有这两处**会置位 |
| 攻击边缘 | `attack release`、`attack reset`、`attack timing scale`、`attack stop effect` | 攻击相位与时间线。**这是模型输入特征**（`attack_phase_remaining_over_5000ms`），缺失会让输入偏离训练分布 |
| 部署 | `causal consume`、`causal spawn` | 部署分组（哪张牌放下了哪个单位） |
| 召唤 | `spawn action`、`spawn pending`、`spawn mode1`、`spawn buff tick`、`spawn tagged pending` | 召唤关系（单位召唤出的单位） |
| 命中 | `processed projectile target` | 投射物命中事件 |
| 伤害 | `proved HP and shield damage` | 伤害事件 |
| 治疗 | `proved HP and built-in shield healing` | 治疗事件 |
| 效果来源 | `effect origin constructor / reset / decode / source setter` | 效果的来源链 |
| 输入时序 | `client input timing` | 触摸指令的时序边缘 |

（`area_origin`、`projectile_origin` 两组只在 `CR_EXPERIMENTAL_PRODUCER_ORIGINS=1` 时编译，
稳定产物不含它们。`attack dispatch` 是唯一的手写钩子，见下文的特例。）

## 一个必须知道的边界

`g_in_battle` **只被置真，从不复位**。对局结束后游戏仍在步进状态机，
探针因此会**无限期返回上一局的冻结终局快照**——"真实大厅"与"仍停在结算页"在探针层面不可区分。

这是设计如此，不是缺陷。所以：

- 判断终局要看 `battle_result.validated && finalized`，不要去等"空闲探针"；
- 绑定新局要靠 **tick 是否推进**，`tools/multi_match.py` 的事件里会记为
  `match_bound {"basis": "probe_tick_advancing"}`。

## 稳定产物与候选产物

| 路径 | 作用 |
| --- | --- |
| `probe/artifacts/stable/libscid_sdk.so` | 发行安装器**只装这个**，哈希被钉死 |
| `probe/artifacts/stable/build.json` | 该产物的构建档案：25 个源文件哈希、编译参数、验证结论 |
| `probe/artifacts/candidates/` | 候选产物，已被 `.gitignore` 覆盖，不随发行分发 |

稳定产物的哈希钉在**四处**，改动时必须一起更新，否则打包或安装会拒绝：

1. `probe/probe_artifacts.ps1` 的 `$script:StableProbeSha256`
2. `tools/install_probe.py` 的 `PROBE_SHA`
3. `release-validation.json`
4. `SHA256SUMS.json` 的 `probe/artifacts/stable/libscid_sdk.so`

**提升候选产物为稳定产物的步骤**：把候选 `.so` 复制到 `artifacts/stable/`，
重新生成 `build.json`，更新上面四处，再跑
`tools/install_probe.py --check`、`python -m unittest discover -s tests -q` 与打包审查。

## 重新编译

只有需要改探针时才用得上 NDK（验证版本 r27c）：

```powershell
powershell -ExecutionPolicy Bypass -File .\probe\build_probe.ps1 -NdkRoot 'C:\Android\android-ndk-r27c'
```

不装 PowerShell 时可以直接调编译器，参数与脚本一致（`.cmd` 只是给 `clang++.exe`
加上 `--target=aarch64-linux-android24`）：

```bash
clang++.exe --target=aarch64-linux-android24 -std=c++17 -O2 -fPIC -fvisibility=hidden -shared \
  -Wl,-z,max-page-size=16384 -DCR_EXPERIMENTAL_PRODUCER_ORIGINS=0 \
  -o artifacts/candidates/<名字>/libscid_sdk.so \
  nulls_probe.cpp card_selection_arm64.S spawn_relations_arm64.S attack_start_arm64.S heal_events_arm64.S \
  -llog -ldl
```

构建脚本会在编译前后自校验源文件哈希。

## 模拟器更新会做什么

**这是本文最重要的一节。** 更新 MuMu（实测 6.6.4.0，会替换 `system.vdi`）后，
模拟器会用新的镜像**把 `libscid_sdk.so` 还原成自带的原版** —— 探针随之消失，
需要重新运行 `probe/deploy_probe.ps1`。

更麻烦的是另一件事：新版模拟器的 **ARM 转译器会在已经翻译过的热函数入口就地写入自己的跳转桩**：

```
ldr x17, #8          ; 0x58000051
br  x17              ; 0xd61f0220
.quad translated_code
```

于是那些入口不再持有原始序言，而探针原先要求入口逐字节等于原始序言。症状是：

```
E NullsProbe: Prologue mismatch for GameStateManager::step at 0x...
E NullsProbe: Prologue mismatch for BattleController::fullUpdate at 0x...
I NullsProbe: Attack edge hooks ready: 0
```

探针的 TCP 端口仍然正常响应 `PING`/`GET`，但**永远返回 `{"in_battle":false}`**，
AI 一直停在 `waiting: idle`。**重启游戏、重装探针都不会好。**

### 怎么诊断

先看钩子安装日志（这是最直接的证据）：

```bash
adb -s 127.0.0.1:16384 logcat -G 16M
adb -s 127.0.0.1:16384 logcat -c
adb -s 127.0.0.1:16384 shell am force-stop nullsroyale.rel.free
adb -s 127.0.0.1:16384 shell am start -n nullsroyale.rel.free/com.supercell.clashroyale.GameApp
adb -s 127.0.0.1:16384 logcat -d | grep NullsProbe
```

如果确实出现 `Prologue mismatch`，再确认究竟是"常量过期"还是"运行时被改写"——
这两者的修法完全不同：

1. 拉取游戏库，比对**文件**在该偏移处是否等于期望序言；
2. 读取**运行时内存**同一地址的字节。

取运行时内存必须在设备端落盘再 `adb pull`。**不要用 `adb shell ... > 本地文件`：
adb 的 stdout 会做 CRLF 转换，二进制会被污染**（32 KB 会读成 32 KB + 换行数）。

```bash
pid=$(adb -s 127.0.0.1:16384 shell pidof nullsroyale.rel.free)
base=$(adb -s 127.0.0.1:16384 shell "su -c 'grep libg.so /proc/$pid/maps | head -1'" | cut -d- -f1)
adb -s 127.0.0.1:16384 shell "su -c 'dd if=/proc/$pid/mem bs=1 skip=$((0x$base + 0x10620a4)) count=16 of=/data/local/tmp/x.bin'"
adb -s 127.0.0.1:16384 pull /data/local/tmp/x.bin ./x.bin
```

判读：文件相符而运行时是 `51 00 00 58 20 02 1f d6 …`，就是转译器改写的，
属于本节描述的情况；文件本身就不符，说明常量过期，需要重新定位偏移。

### 怎么修

中央判定已经处理了这种情况：`entry_matches()` 把转译器桩当作合法入口状态，
复制那 16 字节进 trampoline 后，桩自身会跳到翻译后的代码，续跳链路保持不变。

**但有一个特例：手写钩子不能这样处理。** `attack dispatch` 挂在
`kActionDispatchOffset` 上，它的 wrapper 会"重放"从入口偷来的指令
（拷 `target+4` 起 12 字节，再跳 `target+16`），入口变成桩之后那些字节是桩的中段，
语义不成立。所以**入口为桩时该钩子直接跳过**，只保留同组的另外四个标准钩子。

改完之后需要实机确认两件事，缺一不可：

1. logcat 里九组钩子是否都安装成功（`install_* -> 1`、`Attack edge hooks ready: 1`）；
2. 真打一局，确认 `in_battle` 真的变 `true`，且
   `attack_phase_known_count`、`attack_event_count` 不再是 0。

只做到第 1 条不代表成功——钩子装上但从不被调用是完全可能的。

### 修改探针时的硬性不变量

> **任何针对函数入口的校验，都必须走 `entry_matches()`。**

探针里除了中央的 `install_inline_hook`，还有若干 `.inc` 自己写的入口守卫
（`attack_edges.inc`、`deployment_timing.inc`、`causal_deployment.inc`、`spawn_relations.inc`）。
它们历史上各自展开了 `memcmp(entry, 期望序言, 16)`。只要漏掉其中任何一处，
那一组钩子就会整体放弃，而症状（某个 `*_count` 恒为 0）非常不显眼。

排查时请**全仓库搜索这类模式**，而不是只改中央函数：

```bash
grep -rn "memcmp(reinterpret_cast<void\*>(g_libg_base" probe/
grep -rn "Prologue mismatch" probe/          # 已知的两处例外见下
```

`phase_runtime_layout.h` 里还有一批 `*_words` 校验，它们检查的是函数**内部**的
指令序列（调用点证明），不是入口，转译器不会改写它们，因此保持原样。

## 恢复原始 SDK

```powershell
powershell -ExecutionPolicy Bypass -File .\probe\deploy_probe.ps1 -Restore
```

还原后游戏恢复正常，但本项目也失去感知能力（`preflight` 的 `probe` 一项会失败）。
备份按"序列号 + 安装目录"隔离，不要把一台机器的备份拷到另一台——
`libscid_sdk_real.so` 是与该安装目录绑定的。
