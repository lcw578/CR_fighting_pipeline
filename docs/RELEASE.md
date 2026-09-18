# 发布说明

## 两条发行渠道

本项目的分发有两条渠道，内容不同，不要混用同一套说法：

| 渠道 | 生成方式 | 内容 | 是否需要额外准备 |
| --- | --- | --- | --- |
| Git 仓库 | `git clone` | 源码 + `weights/`（5 个权重）+ `upstream/firstlight/`（裁剪后的上游运行时与冻结数据） | 不需要，开箱即用 |
| 发行 ZIP | `tools/package_release.py` | 仅 `SHA256SUMS.json` 列出的已审查源码、探针与文档 | 需要按 `upstream.lock.json` 补齐 `weights/` 与 `upstream/firstlight/` |

打包渠道按设计**不含**权重与上游运行时：打包工具显式拒绝 `.pt` 文件，`upstream/` 也不在清单内。
这样做的意图是把"受控的、逐字节锁定的代码与探针"和"体积大、来自上游的权重与数据"分开管理。

## 两个清单的分工

| 清单 | 作用 | 谁在检查 |
| --- | --- | --- |
| `SHA256SUMS.json` | 发行 ZIP 的白名单：只有列出的文件会被打包，且内容必须逐字节匹配 | `tools/package_release.py` |
| `upstream.lock.json` | 上游来源与哈希溯源：上游仓库、固定提交、被固定的运行时文件与权重，以及被裁掉的训练专用文件 | `tools/preflight.py` |

`tools/preflight.py` 会用 `upstream.lock.json` 核对 `upstream/firstlight/` 与 `weights/` 的内容，
所以即使权重不参与打包，它们的来源与完整性仍然可验证。

## 发行 ZIP 的范围

包含：`AGENTS.md` 与配置技能、桥接／推理／执行源码、稳定探针及其完整构建源码、
离线检查与测试、便携配置、安装恢复工具、启动菜单和本文档。

排除：原始游戏与 SDK 二进制、APK、模型权重、上游运行时与资源、原始回放、截图、
个人配置与账号、虚拟环境和实验产物。
公开探针 `.so` 是本项目源码编译的代理库，不是原始游戏 SDK。

## 重新打包

不要直接压缩正在使用的目录。按以下顺序操作：

1. 确认变更已审查。改过源码或文档后，`SHA256SUMS.json` 中对应条目的哈希必须更新；
   打包工具不会自动接受变更，遇到不匹配会中止并报出第一个不一致的文件。
   新增文件（例如新的工具或技能）也要加入清单。
2. 运行打包：

   ```powershell
   .\.venv\Scripts\python.exe tools\package_release.py
   ```

   工具会校验稳定探针、逐个核对清单中的哈希、拒绝把运行期文件与个人文件写进包
   （`local/`、`logs/`、`backups/`、`diagnostics/`、`.venv/`、`__pycache__/`、
   `*.local.*`、`.apk`、`.pt`、`.pyc`），并检查文本文件里是否残留开发机路径。
3. 输出为仓库目录同级的 `<仓库目录名>.zip`，清单本身也会写入包内。
   **ZIP 已存在时工具会拒绝覆盖**，请先把旧包移到另一个归档位置。

### 换行符规则

Git 文本文件统一使用 LF，BAT 使用 CRLF；打包校验按同一规则处理换行符，
即文本文件在计算哈希前会把 CRLF 规范化为 LF（`.bat` 反向处理）。二进制始终逐字节校验。
只有换行符差异会被规范化，源码内容变更仍需重新审查。

更新清单时请用与打包工具相同的规范化方式重算，否则会出现"改了换行符就校验失败"的假告警。
规范化实现就是 `tools/package_release.py` 里的 `release_bytes`，
可用它来计算新哈希：

```powershell
.\.venv\Scripts\python.exe -c "import json,hashlib,sys; from pathlib import Path; sys.path.insert(0,'.'); from tools.package_release import release_bytes; p=Path('SHA256SUMS.json'); m=json.loads(p.read_text(encoding='utf-8')); [m.__setitem__(n, hashlib.sha256(release_bytes(Path(n))).hexdigest()) for n in sys.argv[1:]]; p.write_text(json.dumps(m, indent=2, ensure_ascii=False, sort_keys=True)+chr(10), encoding='utf-8', newline=chr(10))" README.md docs/SETUP.md
```

只把**确实审查过的**文件列进这条命令。批量重算未审查的源码，等于把审查步骤跳过去。

## 已知部署限制

- 当前仅支持 Windows、Root MuMu、精确匹配的 ARM64 游戏版本。
- 需要使用者先在 MuMu 中启用 Root、ADB 并连接正确实例。
- 游戏 SDK 代理要求原始库的全部转发符号可用；缺失时安装拒绝。
- 第一次安装会保留原始 SDK；恢复按模拟器地址和安装目录隔离，更新游戏后不复用旧目录备份。
- 安装步骤完成前后均检查文件指纹；若安装中断导致游戏仍停止，先检查错误和备份后运行恢复命令。
- 原生遥测监听端口 26888。只在本地测试环境使用，不要转发到公共网络。
- 模拟器更新会替换系统镜像、还原游戏目录里的代理库，并可能让新版 ARM 转译器改写函数入口，
  导致探针钩子安装失败而端口仍看似正常。重装探针或重启游戏都不足以解决，
  见[原生探针](PROBE.md)的诊断与重新适配步骤。

已在同一电脑的新实例完成安装和整局实战；具体覆盖与限制见[验证记录](VALIDATION.md)。
