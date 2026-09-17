# 首次坐标核对

`calibration.json` 是 1080×1920 的参考地面投影。历史样本覆盖两席位各 8 个静止加农炮落点，
不是新设备的自动验收证据。相同比例缩放可复用参考，不同比例会拒绝；换竞技场界面仍需核对。

1. 手动开一局对局（好友战最方便，可以事先约定两边用不同卡组），AI 保持关闭，
   然后截取一张**完整竞技场**画面。截图必须用 ADB 取回，不要用系统截屏或窗口截图工具
   （会带上边框或缩放，叠图就对不上了）：

   ```powershell
   $settings = Get-Content .\settings.local.json -Raw | ConvertFrom-Json
   & $settings.adb_path -s $settings.adb_serial shell screencap -p /sdcard/arena.png
   & $settings.adb_path -s $settings.adb_serial pull /sdcard/arena.png diagnostics\arena.png
   ```

   用 `shell screencap` 加 `pull`，不要用 `exec-out screencap -p > arena.png`：
   PowerShell 的重定向按文本处理二进制，得到的 PNG 会损坏。
2. 使用项目 Python 执行：

   ```powershell
   .\.venv\Scripts\python.exe tools\coordinate_view.py --image diagnostics\arena.png --output diagnostics\coordinates.html
   ```

   用浏览器打开结果；查看左右两路、河边和后场的地面中心，切换 owner 后相同模型落点应仍在同一屏幕位置。
   当前叠图工具使用 1080×1920 参考画布；其他尺寸截图需先准备对应参考尺寸图像，原始设备尺寸还须按比例验证。
3. 核对手牌槽中心与游戏实际手牌；参考点在 `config.py` 的 HAND_CARD_SLOTS 中。
   如果地面网格有偏移，先修改校准矩阵或提供独立 `calibration_path`，不要盲目改左右翻转逻辑。
4. 确认参考后，将 `settings.local.json` 的 `calibration_verified` 改为 `true`，
   进行一局有人观察的好友战，核对建筑的静止落点和两路出牌；发现偏移立即 Ctrl+C 停止并修正。
   静止建筑最适合验证落点；火球飞行起点、移动中的单位位置不能作为出生点真值。

首次建议只观察模式。设置确认标记只是允许受监督实战验证，并不能代替实战本身。

## 英雄火枪手技能

复制 `ability_calibration.example.json` 为 `ability_calibration.local.json`。
在只有一个英雄的对局中部署英雄火枪手，技能按钮出现时核对其中心坐标、屏幕尺寸；
调整 `point` 和 `size` 后才把 `verified` 改为 `true`。
当前仅支持 `controller_slot=1` 的该技能按钮；第二英雄控制器不在此标定范围。
未验证的按钮不会自动点击。不要把自己的 verified 文件打包给其他用户。

## 表情面板

只在需要 `--emote` 时做这一步；不发表情可以完全跳过。

复制 `emote_calibration.example.json` 为 `emote_calibration.local.json`，然后在对局里核对两组坐标：

1. **`emote_button`**：对局内 HUD 左下角的对话框按钮，位于手牌行左侧、`下一张：` 标签上方。
   参考值 `(106, 1640)`，在 1080×1920 的大头锤卡组对局截图上有叠图确认。
2. **`emote_slots`**：点开托盘后各个表情的中心。参考文件给的是两行三列。
   核对方法：把 `emote_first_delay_seconds` 设为 0、`emote_min/max_interval_seconds` 设为 3.5/4.0
   跑一局，同时在每次 `emote_sent` 后用 `adb exec-out screencap -p` 抓一帧——
   表情气泡会在我方国王塔旁停留两三秒，逐帧对照即可确认每个槽位。

**不要照搬上游参考里的第四列（x=869）**：它距离 `ABILITY_HUD_BOUNDS` 左边界只有 6 像素，
误触会消耗 3 圣水。本仓库不采用该列，且 `Actuator.emote` 会用 24 像素余量拒绝过近的槽位。

确认后把 `verified` 改为 `true`。未验证时表情会被禁用并记 `emote_input_disabled`，
但**不影响对局继续运行**。同样不要把自己的 verified 文件打包给其他用户。

细节与实测数据见 [docs/EMOTE.md](EMOTE.md)。
