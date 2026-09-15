# 安装、配置与排错

所有命令在仓库根目录执行。可先让 agent 阅读 [AGENTS.md](../AGENTS.md) 完成配置，或按本文手动操作。

本文按**自包含仓库**（`git clone` 后含 `weights/` 与 `upstream/firstlight/`）描述。
若使用 `tools/package_release.py` 产出的 ZIP，先按 [upstream.lock.json](../upstream.lock.json)
补齐这两个目录，其余步骤相同；两种渠道的差别见[发布说明](RELEASE.md)。

支持的游戏库 `libg.so` SHA-256：
`110aa2b5cac391c498645e072b0d88729428c2c2845e7e2737ca8ee979059783`。
同一版本号不保证原生库与内容一致，安装器会校验指纹。

## 环境与依赖

| 项目 | 环境要求 |
| --- | --- |
| 主机 | Windows；启动与构建脚本面向 PowerShell |
| 模拟器 | MuMu，启用 Root 与 ADB，连接正确的实例 |
| 游戏 | ARM64 Null’s Royale，原生库指纹与上述支持版本一致 |
| 显示 | 1080×1920 竖屏参考布局；首次使用需核对地面与手牌位置 |
| Python | 3.12 |
| 推理环境 | PyTorch 2.11.0 + CUDA 12.8；另提供 CPU 安装选项 |
| 上游运行时 | 已随仓库分发于 `upstream/firstlight/`（FirstLight 推理代码与冻结目录数据） |
| 模型权重 | 已随仓库分发于 `weights/`，共 5 个，见[模型与权重](MODELS.md) |
| 编译工具 | 使用预编译探针无需 NDK；重新构建与验证使用 NDK r27c |

不需要获取 FirstLight 上游仓库，也不需要执行上游的离线引擎构建或安装步骤：
本仓库只保留推理所需部分，训练、PPO、离线对局引擎与策略服务不在其中。
`upstream/` 是只读运行时，不要在其中修改源码，确有缺陷时在本仓库桥接层做最小修复。

## 快速开始

1. 安装 Python 3.12 与 MuMu（在模拟器设置中启用 Root 与 ADB），在 MuMu 中安装匹配指纹的
   Null’s Royale，游戏停在大厅。然后创建运行环境：

   ```powershell
   powershell -ExecutionPolicy Bypass -File .\setup.ps1
   ```

   脚本创建项目 `.venv`，安装本机验证使用的 Torch 2.11.0 / CUDA 12.8 依赖组合，
   并在缺少时从 `settings.example.json` 复制出 `settings.local.json`。
   CPU 使用 `-TorchBuild cpu`，并将配置中的 `device` 改为 `cpu`。
   已有 Python 可用 `-Python 'C:\path\python.exe'`；现有推理环境也可通过
   `CR_AGENT_PYTHON` 环境变量供启动器使用。
2. 编辑生成的 `settings.local.json`。**必须填写 `adb_path` 与 `adb_serial`**；
   `firstlight_dir` 已指向仓库内的 `upstream/firstlight`，通常无需改动。
   路径相对于配置文件所在目录解析，含空格路径受支持。
   多实例同时运行时，每个副本设置不同的 `probe_port`；设备内部端口固定 26888。

   这两个值从你自己的 MuMu 安装获取，不要照抄示例：

   ```powershell
   # MuMu 自带 adb.exe 与 MuMuManager.exe，默认在安装目录的 nx_device\<版本>\shell\ 下
   $mumu = 'C:\Program Files\Netease\MuMuPlayer\nx_device\12.0\shell'
   & "$mumu\MuMuManager.exe" info -v all
   ```

   - `adb_path`：上面目录里的 `adb.exe` 绝对路径（JSON 中可用 `/`）。
   - `adb_serial`：`info -v all` 输出里目标实例的 `adb_host_ip` 与 `adb_port` 拼成
     `ip:port`，例如 `127.0.0.1:16384`。**多开时每个实例端口不同，必须以该命令的返回值为准**，
     不要按名称或顺序推算。
   - 按名称找到你要用的实例，记下它的 `index`；同一设备还可能以 `emulator-…` 别名出现，
     不要当成第二个实例。
3. 连接实例并确认目标是设备：

   ```powershell
   $settings = Get-Content .\settings.local.json -Raw | ConvertFrom-Json
   & $settings.adb_path connect $settings.adb_serial
   & $settings.adb_path devices
   ```

   上述命令适用于示例中的 TCP 实例地址；使用 `emulator-…` 序列号时只需检查 `devices`。
   然后运行启动检查（此时账号未设置，`account` 项失败是预期的，其他项仍须通过）：

   ```powershell
   .\.venv\Scripts\python.exe tools\preflight.py --device-check --model hog26
   ```
4. 安装稳定探针：

   ```powershell
   powershell -ExecutionPolicy Bypass -File .\probe\deploy_probe.ps1 -Check
   powershell -ExecutionPolicy Bypass -File .\probe\deploy_probe.ps1
   ```

   `-Check` 只检查本地文件，不能作为实机安装成功的证据。实际安装会保留原始 SDK、
   停止并重启游戏，不会匹配或下牌。原始 SDK 只从本人的设备保存，不随本项目分发。
5. 在 AI 未启动时手动开一局好友战，两边使用不同卡组，运行：

   ```powershell
   .\.venv\Scripts\python.exe tools\inspect_players.py
   ```

   对照自己的手牌和卡组 ID 找到所属行，把原生 `account_id` 填入配置。
   这是内部数字 ID，不是个人主页的 `#玩家标签`；不要固定填写 owner，席位会变化。
6. 按[坐标核对](CALIBRATION.md)验证自己的界面；确认后将配置中的
   `calibration_verified` 设为 `true`。英雄按钮需要单独校准。
7. 双击 `start_agent.bat`。首次建议选“只观察一局”，确认进入对战后有连续决策与正确卡组。
   正式使用选自动对战，等待 `ready` 后再手动匹配。Ctrl+C 停止。
8. 完成账号设置后，再运行一次第 3 步的启动检查，确认全部通过。
   使用其他权重时，将 `hog26` 换成 `--model` 对应的别名。

## 配置项与日常使用

`settings.local.json` 的字段：

| 字段 | 说明 |
| --- | --- |
| `firstlight_dir` | 上游运行时位置。默认 `upstream/firstlight`，随仓库分发，通常不用改 |
| `adb_path` | 该 MuMu 安装附带的 ADB 绝对路径 |
| `adb_serial` | 管理器返回的真实地址或已核对的设备序列号 |
| `account_id` | 从本人对局快照确认的原生数字 `accountId`；未确认时保留 `null` |
| `device` | `cuda:0` 或 `cpu`；以启动检查中的实际张量计算为准 |
| `probe_port` | 主机侧遥测端口，默认 26888；多实例时各用独立空闲端口 |
| `calibration_verified` | 首次为 `false`；核对本机画面与地面投影后才设 `true` |

可选字段：`checkpoints_dir`、`calibration_path`、`ability_calibration_path`，
以及仅作记录的 `vm_index`、`vm_name`、`mumu_manager_path`、`ndk_root`。
`CR_AGENT_SETTINGS` 可指定另一份配置文件；`CR_AGENT_PYTHON` 可指定启动器使用的解释器。

设备内遥测端口固定为 26888，主机端口可以不同，不要把两者一起随意修改。
独立的地面校准可放到 `local/` 下，保持公开参考文件不带本机确认信息。

双击 `start_agent.bat` 后，菜单提供四组选项，直接回车采用当前默认：

| 菜单 | 可选内容 |
| --- | --- |
| 运行方式 | 自动对战并等待再次匹配／自动对战只运行一局／只观察和记录（不下牌、不点技能）／只观察一局 |
| 模型 | 速猪 specialist2（默认）、速猪 specialist1（对照）、通用模型 general、IL、active IL，见[模型与权重](MODELS.md) |
| 特殊形态 | 自动识别英雄火枪手与觉醒小骷髅／加农炮；或禁用特殊形态执行用于排错 |
| 输入方案 | `reference` 原版规则基线（默认）；或 `extended` 精确事件对照实验 |

**切换模型不会扩大执行支持范围。** 权重决定策略偏好，可执行的卡牌形态、英雄技能与塔兵状态
由桥接层决定。英雄火枪手未校准技能按钮时，技能点击会禁用。

### 常用命令

```powershell
# 只观察一局：推理并记录，不下牌
.\.venv\Scripts\python.exe main.py --checkpoint hog26 --dry-run --once

# 使用通用模型自动执行一局；等待 ready 后手动匹配
.\.venv\Scripts\python.exe main.py --checkpoint general --once

# 连续多局：天梯结算页用"再来一场"直接衔接下一局
.\.venv\Scripts\python.exe tools\multi_match.py --matches 50 --checkpoint hog26 --rematch

# 在已有本地采集记录上比较两套输入，不操作游戏
.\.venv\Scripts\python.exe tools\compare_observation_profiles.py --help
```

`main.py` 常用参数：`--checkpoint`、`--device`、`--account-id`、`--dry-run`、`--once`、
`--observation-profile`、`--sample`、`--base-only`、`--start-battle`、`--log`。
完整列表见 `main.py --help`。

只观察模式仍会建立 ADB 连接、端口转发并读取屏幕尺寸，但不下牌或点击技能。
`--once` 在一局结束后退出；遥测中断时暂停动作并等待同一局恢复，检测到已换局则退出，
避免意外接管下一局。

`tools/multi_match.py` 是外部监督器，按阶段推进并写 JSONL 事件（默认 `logs/multi_match_log.jsonl`）。
它是**有界**循环：`--matches` 默认 1，按需显式提高局数，不存在无限重试；`--dry-run` 只打印计划，
不接触 ADB、探针或任何进程。它有一个已知边界：对战外状态机仍在步进时探针会持续返回冻结的
终局快照，"真实大厅"与"仍停在结算页"因此无法区分，所以它从不等待"空闲探针"，
而是把 `validated && finalized` 当作"可能是结算页"，每轮对每个已知的确定位置各点一次。
解围轮次与各阶段超时可用 `--result-rounds`、`--battle-timeout`、`--matchmaking-timeout` 等参数调整；
失败的一轮不会自行重试：任一轮失败即终止整个会话并返回退出码 2，
需要连续多局时要由外层脚本决定是否重新启动。

**分辨率约束**：大厅、结算页与"再来一场"的点击位置是固定的 1080×1920 像素
（`config.py` 的 `LOBBY_*` 与 `tools/multi_match.py` 的 `RESULT_OK_POSITIONS`、`REMATCH_BUTTON`），
**不随分辨率缩放**；战斗内的下牌坐标则会按实际分辨率缩放。换用其他分辨率时，
除了跑坐标核对，还必须重新标定这些大厅与结算页的像素位置。

## 工具一览

| 工具 | 用途 |
| --- | --- |
| `tools/preflight.py` | 启动前后检查：设置、Python 版本、ADB、账号、探针指纹、上游哈希、目录加载、张量设备、校准 |
| `tools/install_probe.py` | 探针安装与恢复；校验游戏库指纹、原始 SDK 与转发符号 |
| `probe/deploy_probe.ps1` | 探针安装／`-Restore` 恢复／`-Check` 本地校验的入口 |
| `tools/inspect_players.py` | 手动对局中打印双方身份与卡组，用于确认 `account_id`；不发送触摸输入 |
| `tools/coordinate_view.py` | 用一张截图生成可点击的地面网格叠图，用于核对坐标；不接触游戏 |
| `tools/capture_native.py` | 采集原生遥测（含卡牌预览画面）为 JSONL；不推理、不输入 |
| `tools/compare_observation_profiles.py` | 在已记录对局上反事实比较两套输入；不接触游戏，也不是胜率评测 |
| `tools/audit_relations.py` | 核对 V4 关系张量与实测对象；离线运行，不发输入 |
| `tools/multi_match.py` | 多局自动化监督器 |
| `tools/package_release.py` | 打包已审查文件；不含运行产物、权重与上游 |
| `tools/launch_menu.py` | 启动菜单，由 `start_agent.bat` 调用 |
| `test_pipeline.py` | 离线回归套件入口；`--model` 追加真实权重推理，不碰 ADB |

只读采集前先用 `tools/inspect_players.py` 建立端口转发，例如
`tools\capture_native.py --seconds 60 --output diagnostics\capture.jsonl`。

## 恢复原始 SDK

```powershell
powershell -ExecutionPolicy Bypass -File .\probe\deploy_probe.ps1 -Restore
```

恢复仅使用本次设备和安装目录对应的 `local/backups` 备份，会重启游戏。
不要将其他账号、其他实例或旧安装目录的备份复制过去。重装或更新游戏后需重新进行兼容检查。
备份不能省略，建议保留至确认不再需要恢复为止。

## 常见问题

**其他人的同版本游戏可以直接用吗？**

模型权重、上游运行时和桥接代码都可以复用，但仍需配置本机路径、模拟器连接、账号身份和坐标。
游戏库指纹不匹配时需要重新适配；只看版本号不能判断兼容。

**需要每个用户重新绑定所有卡牌 ID 吗？**

同一受支持游戏数据版本通常不需要。卡牌目录与词表来自冻结的上游数据。
但“识别 ID”与“完整支持该卡牌机制和动作”是两回事，尤其是英雄技能、觉醒状态和塔兵资源。

**启动后为什么一直等待，或者英雄不点技能？**

先检查控制台的 `waiting`／`telemetry_rejected`／`ability_input_disabled` 信息和本地 `logs`。
常见原因包括未进入对局、账号身份不匹配、遥测断开、形态不受支持或英雄按钮未校准。
模型也可以主动选择 `wait`；日志中的等待不一定是程序卡死。

游戏断线重连时，AI 会暂停动作并等待遥测恢复；“只运行一局”也会继续等待同一局，
不会重发已经提交的指令。若恢复后已进入另一局，该模式会退出。在上一局的结果页启动 AI 时，
会等待新的对战开始。

**第一次启动就报 `ValueError` 怎么办？**

自动对战启动前会检查两项前置条件，缺任何一项都会以 `ValueError` 终止并打印堆栈。
这是刻意的前置保护：账号不对会读到对方的席位，坐标未核对会下错位置，所以宁可拒绝启动。

- `Set account_id in settings.local.json; see tools/inspect_players.py`：还没完成第 5 步的账号识别。
- `Verify ground coordinates, then set calibration_verified=true in settings.local.json`：还没完成第 6 步的坐标核对。

按提示补完配置并重启进程即可。只观察模式（`--dry-run`）不受 `calibration_verified` 限制，
可以先用来确认链路，但它仍然要求 `account_id`。

**当前模型接入有哪些限制？**

实时观测与训练观测在采样、完整可见性和特殊机制覆盖上仍有差异。
执行时延和重复施法是后续优化方向，详见[架构与边界](ARCHITECTURE.md)。

**为什么 `extended` 信息更多，却不是默认选项？**

精确事件更多不保证更接近已有权重的训练观测，也不保证决策更好。
保留 `reference` 作为基线，便于对比输入变化，避免把额外字段误当作已证实的性能提升。

**现在能使用图像识别吗？**

当前版本仍依赖原生数据。后续视觉感知需要对象跟踪、状态估计和缺失信息处理，
不是把截图直接替换到当前模型接口里即可完成。

## 开发与验证

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -q
.\.venv\Scripts\python.exe test_pipeline.py --model
powershell -ExecutionPolicy Bypass -File .\probe\test_artifacts.ps1
```

测试必须在安装了 torch 的项目解释器下运行。若用无关的系统解释器执行，
套件会因为缺 torch 而以一条与真实原因无关的导入错误终止
（`'test_pipeline' module incorrectly imported`）——这是仓库根的 `test_pipeline.py`
与 `tests/test_pipeline.py` 同名导致的报错，改用 `.venv` 的解释器重跑即可。

`probe/test_artifacts.ps1` 是离线回归检查，只用伪造产物验证安装器必须拒绝的情形，
不调用 ADB、不部署、不启动游戏。
原生探针源码位于 `probe/`，只有需要重新编译的开发者才需要 NDK（验证版本 r27c）：

```powershell
powershell -ExecutionPolicy Bypass -File .\probe\build_probe.ps1 -NdkRoot 'C:\Android\android-ndk-r27c'
```

构建输出候选产物，不覆盖发行包内锁定的稳定探针；发布安装器只安装稳定产物。
源码中的 `Experimental` 构建开关仅供开发研究，默认关闭。

重新打包使用 `tools/package_release.py`，它只收录 `SHA256SUMS.json` 中列出的文件，
并逐字节校验；改过源码或文档后必须先审查变更并更新清单，打包工具不会自动接受变更。
输出 ZIP 已存在时会拒绝覆盖，请先将旧包移到另一个归档位置。
文本文件的换行符规则与更新清单的方法见[发布说明](RELEASE.md)。
