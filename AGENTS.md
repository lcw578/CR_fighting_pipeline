# CR_fighting_pipeline：agent 工作说明

本仓库（`CR_fighting_pipeline`）是在线游戏的感知—推理—触摸执行桥接层，
**自包含部署端**：FirstLight 推理运行时与冻结数据在 `upstream/firstlight/`，
权重在 `weights/`，默认 `settings` 无需再指向上游目录。
通过 Root MuMu 读取 Null’s Royale 原生状态，使用 ADB 触摸执行动作。
自包含指的是 Git 仓库：`tools/package_release.py` 产出的 ZIP 按设计不含权重与上游运行时，
用该渠道分发时需按 `upstream.lock.json` 补齐两个目录，见 [docs/RELEASE.md](docs/RELEASE.md)。

## 按任务读取

- 新电脑／模拟器配置、启动排错、首次对战验证：读取
  [.agents/skills/configure-cr-fighting-pipeline/SKILL.md](.agents/skills/configure-cr-fighting-pipeline/SKILL.md)。
- 手动安装步骤：[docs/SETUP.md](docs/SETUP.md)。
- 选择模型或说明权重来历：[docs/MODELS.md](docs/MODELS.md)。
- 多局自动化（含天梯"再来一场"直连下一局）：`tools/multi_match.py --help`。
- 无人值守长跑（分块会话、日志轮转、哨兵停止、空局退避）：`tools/forever.py --help`。
- 触碰探针（钩子失效、重新编译、提升产物）：先读 [docs/PROBE.md](docs/PROBE.md)。
- 改动表情开关、间隔或托盘坐标：先看 [docs/EMOTE.md](docs/EMOTE.md)；
  坐标核对步骤在 [docs/CALIBRATION.md](docs/CALIBRATION.md) 的“表情面板”。
- 修改观测或执行逻辑：先看 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)。
- 打包公开版本：[docs/RELEASE.md](docs/RELEASE.md)。

## 保持仓库精简

机器路径、实例、账号、校准确认写入已忽略的 `settings.local.json` 和
`ability_calibration.local.json`。参考模板保持通用；不要为每台机器创建一个启动脚本，
也不要把环境探测、训练或未请求的机制适配混进一次配置任务。
`upstream/` 是按 `upstream.lock.json` 裁剪过的上游只读运行时——不要在其中修改源码；
确有缺陷时在本仓库对应桥接层做最小修复并验证。

优先复用 `setup.ps1`、`tools/preflight.py`、`probe/deploy_probe.ps1`、
`tools/inspect_players.py`、`tools/multi_match.py` 和 `start_agent.bat`。
配置变化需重新启动进程才能生效。同一实例一次只运行一个负责触摸的 AI 进程。

模拟器更新会还原游戏目录里的 `libscid_sdk.so`（探针消失），并可能让新版 ARM 转译器
改写函数入口，导致探针钩子安装失败而**表面仍能响应端口**。症状、诊断与修法见
[docs/PROBE.md](docs/PROBE.md)。

**修改探针时的硬性不变量**：任何针对函数入口的校验都必须走 `entry_matches()`。
探针里除中央的 `install_inline_hook` 外还有若干 `.inc` 自己写的入口守卫，
漏掉任何一处就会让整组钩子静默失效。改动前先全仓库搜索 `memcmp` 入口模式。

遵循用户当前任务范围及已有授权。已授权的安装、重启或自动测试可以继续；
缺少无法读取的关键信息或需要未授权操作时再询问，不要求用户逐步重复确认。

## 修改与验证

保留用户改动。普通环境配置无需修改模型、决策间隔或坐标算法。
修复实际问题时添加相应边界测试，并使用项目 Python 运行相关检查：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -q
```

测试必须用 `.venv` 的解释器（需要 torch）。用无关的系统解释器会因缺 torch 报出
`'test_pipeline' module incorrectly imported`，与真实原因无关。

区分只读检查、模型推理、实际输入与游戏确认。发送成功不能替代手牌／技能变化确认，
终局前未确认的动作保留为未确认。源码测试通过、整局运行通过和策略强度是不同结论。

分发文件由 `SHA256SUMS.json` 控制。只更新已审查文件的校验和，遵守打包工具的文本换行规则；
新增技能文件也要加入清单。不要打包账号配置、`.venv`、SDK 备份、截图或原始对局。
