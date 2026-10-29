# 安卓应用搬家（hoardy-adb）

免费的 Mac 安卓应用迁移工具：从旧手机选择应用，将 APK 备份到 Mac，再安装到新手机。
本仓库基于 [hoardy-adb 上游项目](https://github.com/Own-Data-Privateer/hoardy-adb) 开发，保留原有命令行备份、解密、拆分、合并和 TAR 转换功能。

**当前桌面版：0.3.3。命令行工具沿用上游版本号 2.0.1，两者独立维护。**

- [下载 Apple 芯片版 ZIP](release/Android-App-Mover-macOS-arm64.zip)
- [查看应用目录](release/)
- [中文版本与修改记录](CHANGELOG.md)
- [三套交互设计演示（尚未实装）](release/设计演示/交互方案对比.html)
- [反馈问题](https://github.com/wx383847364/hoardy-adb/issues)

> 桌面版目前迁移的是应用安装包，不包含登录状态、聊天记录和应用私有数据。
> Android 的系统权限与厂商策略会影响调试连接、备份和安装；应用无法保证免除手机端安装确认。
> 文件夹备份与恢复已列入后续计划，当前版本尚未实现。

## 目录

- [Mac 桌面版使用方法](#mac-桌面版使用方法)
- [三套交互设计演示](#interaction-demo)
- [源码运行与编译发布](#源码运行与编译发布)
- [版本维护与提交约定](#版本维护与提交约定)
- [上游工具与设计背景](#上游工具与设计背景)
- [命令行快速开始](#命令行快速开始)
- [备份应用安装包并恢复](#备份应用安装包并恢复)
- [传统 Android Backup 备份与单应用恢复](#传统-android-backup-备份与单应用恢复)
- [命令与参数说明](#命令与参数说明)
- [命令行示例](#命令行示例)
- [其他工具与替代方案](#其他工具与替代方案)
- [常见问题](#常见问题)
- [开发、许可与贡献](#开发许可与贡献)

## Mac 桌面版使用方法

### 下载与启动

下载 `release/Android-App-Mover-macOS-arm64.zip`，解压后打开 `安卓应用搬家.app`。
当前发布包面向 Apple 芯片 Mac，内置 Python、Qt、ADB、可用的 AAPT2 及相关许可证，不需要另外安装 Python。
发布包经过本地签名校验，未进行 Apple 开发者公证。

正式交付物统一位于项目的 `release/` 目录：

```text
release/
├── 安卓应用搬家.app
└── Android-App-Mover-macOS-arm64.zip
```

旧版应用如有归档，会保留在 `release/历史版本/`。构建中间文件位于 `build/`，不会提交到 Git。

### 连接两台手机

1. 在手机上开启开发人员选项和 USB 调试。
2. 用支持数据传输的数据线连接 Mac，解锁手机并允许这台电脑进行 USB 调试。
3. 在侧栏分别选择“旧手机 · 来源”和“新手机 · 目标”，同一台手机不能同时担任两个角色。
4. 只有一根数据线时，可以先连接旧手机备份，再连接新手机安装；安装阶段不要求旧手机同时在线。

启动时会先从已有安装清单恢复目标手机身份，再决定是否自动扫描旧手机，避免把唯一在线的新手机当成来源。
授权失败或 USB 调试被关闭时，需要在手机上重新处理；电脑端无法替手机开启已关闭的调试权限。
“始终允许这台电脑调试”保存的是调试授权，不代表每个 APK 都能免确认安装。

### 选择并导出应用

手机应用页支持名称或包名搜索，以及以下操作：

- **全选当前结果**：仅选择当前搜索、筛选和隐藏条件下可见且可导出的应用，支持部分选中状态。
- **清空选择**：取消全部勾选，包括因搜索而暂时隐藏的选择。
- **隐藏已导出**：只隐藏已确认存在同版本有效备份的应用；每次启动默认关闭，同一窗口切页保留设置。

列表状态分为“已导出”“未导出”“版本核对中”，顶部显示各状态数量。
核对中的应用保持可见；确认已导出并应隐藏后，会同步取消其选择和尚未启动的导出请求。
普通搜索导致隐藏时仍保留选择。

扫描先发布完整应用清单，再补充名称、版本、大小和图标；已解析资料会缓存复用。
资料未齐时大小显示“待确认”，仍可勾选导出。点击导出会结束后台资料读取并优先处理所选应用。
APK 资源按块读取，名称解析优先使用已有 AAPT2，无法运行时回退到 Python。

### 已导出状态的判定

每次导出完成后，都会读取实际落盘基础 APK 的版本，避免沿用扫描后已过期的版本资料。
解析失败时保留成功导出的文件并标记“版本未确认”。

双方都有 `versionCode` 时按整数比较，版本号不同就不能用相同版本名称判为一致；缺少版本号时才比较明确的版本名称。
同包同版本有多份备份时，任一完整有效副本都可确认已导出，新近失败或损坏的副本不会挡住旧的有效备份。
应用更新后会重新显示为未导出，并提示已有其他版本备份。

列表核对只检查所需文件是否齐全、总大小是否符合记录；完整校验放在安装前执行。
旧备份缺少版本时在后台读取基础 APK 补充判断。切换应用库会清空旧索引并重新核对。

### 从全部或指定导出批次安装

手机页的“选择备份并安装”进入本地应用库。
批次下拉框默认显示全部导出批次，也可单独选择某一次导出。
全部视图按包名去重，优先使用最近的完整副本；损坏的新副本不会挡住有效旧副本。

选定范围后，勾选部分应用或全选当前结果，确认目标手机，再点击底部安装按钮。
切换批次会清空该页选择，避免安装上一批次中不可见的勾选；单纯刷新列表会保留有效选择。
已有应用默认跳过，不卸载或覆盖。

### 安装进度与失败处理

安装时，顶部显示当前应用、安装包大小、已处理数量和单项已用时间。
当前行以蓝色边条、“正在安装”文字和旋转提示高亮，其他未开始的应用显示“排队等待”。
旋转提示仅更新当前状态图标和任务卡，不刷新列表、不自动滚动；可以主动点击“定位当前应用”。
大应用等待较久时提示检查手机安装确认，不伪造单项安装百分比。
成功、失败或暂停后自动停止动画。

顶部计数区分别显示成功、已跳过、失败和等待数量；主列表同步显示本次安装结果。
单项失败会记录原因并继续下一项。目标手机断线后显示“已暂停”，保留应用清单与结果。
重连原来的新手机后，清空应用选择即可点击“继续剩余 N 个应用”，只处理尚未执行项。

“查看失败详情”在右侧展开结果面板，默认收起。
失败项与未处理项分开展示，可查看、复制完整错误；可逐项勾选或全选失败项，再重试所选应用。
失败重试不混入未处理项。继续与重试沿用原清单，保留其他成功、失败和未处理记录，并核对原目标手机。
重启后进入应用库，会恢复最近一次安装的队列、设备身份与详情。

### 局部刷新与日志

设备检测在独立的只读后台任务中运行，不锁定业务操作或刷新应用列表。
相同设备状态不会重建设备控件；断线时保留原设备角色、应用列表与勾选，并标注未连接。
检测命令失败时暂时禁用传输，保留界面等待再次检测。

- **刷新当前列表**：仅按稳定标识增删改当前页面的行，保留勾选、排序和滚动位置。
- **重新检测设备**：单独检测连接状态。
- **打开日志目录**：打开 `~/Library/Logs/HoardyADB/`。

日志文件为 `AndroidAppMover.log`，每份上限 2 MB，保留当前文件和最多 3 份滚动备份。
日志记录设备状态、检测耗时、实际共用的 ADB 服务来源与 USB 后端、ADB 错误、安装结果、局部刷新数量和界面异常，不记录 APK 内容或完整应用清单。
Mac 离线诊断会区分未连接 USB、未提供调试接口和 ADB 通道不可用，并在设备区显示对应原因。

### 数据保存位置

默认应用库为 `~/Documents/AndroidApps`，可在界面更改。
每批任务的 `manifest.json` 保存版本、完整文件组、校验值和每项结果。
拆分 APK 必须完整导出后整组安装，失败或未完成文件不会作为可用备份。
缓存位于系统缓存目录。日志、应用库和手机备份均不应提交到源码仓库。

“停止后续任务”会等当前文件传输或安装事务完成后停止，不会强杀手机端安装。

<a id="interaction-demo"></a>
## 三套交互设计演示（尚未实装）

[打开本地可点击演示](release/设计演示/交互方案对比.html)。下载后用 Chrome 或 Safari 打开 HTML 即可，不需要安装依赖或启动服务；GitHub 文件预览页不会直接运行 HTML。

| 方案 | 风格与动效 | 展开态预览 |
| --- | --- | --- |
| A：系统简洁，推荐 | 白底浅蓝、轻阴影、150ms 轻量展开，按钮轻微缩放。 | [方案 A](release/设计演示/方案A-下拉展开.png) |
| B：柔和卡片 | 更圆润的卡片和层次、200ms 缩放展开，按钮悬停轻微浮起。 | [方案 B](release/设计演示/方案B-下拉展开.png) |
| C：紧凑专业 | 细边框与选中边条、120ms 快速展开，按钮按下移动 1px。 | [方案 C](release/设计演示/方案C-下拉展开.png) |

三套使用相同的数据和布局；可直接切换风格、展开旧/新手机选择、筛选应用、选择导出批次、查看失败分组，以及勾选和清空应用。
顶部体验场景可切换已连接、设备为空、待授权、控件禁用和执行中。设备为空时可在菜单中模拟重新检测；角色冲突或不可用设备不能选中。
支持 Tab、方向键、Enter、Escape 和点击外部收起，尊重系统的减少动态效果设置。

演示为单一 HTML，内置全部样式、图标和示例数据，不连接 ADB、不读取手机、不备份文件或安装应用。
本轮仅交付设计原型和三张截图，正式桌面版保持 **0.3.3**，选定方案后再接入实际 App。

## 源码运行与编译发布

从本仓库源码运行桌面版：

```bash
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -e '.[gui,build]'
.venv/bin/hoardy-adb-gui
```

源码运行会从 `HOARDY_ADB`、系统路径及常见 SDK 位置寻找 ADB，也可在界面中选择 ADB 文件。

验证与编译：

```bash
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/pyinstaller --noconfirm AndroidAppMover.spec
```

**不需要指定 `--distpath`。** 打包配置会将收集中间文件放在构建目录，把正式应用和 ZIP 固定输出到项目 `release/`。
即使从其他目录调用此 spec，交付位置仍以项目目录为准。
打包末尾会校验应用签名，并同步生成 ZIP，避免应用与下载包版本不一致。

可以额外检查：

```bash
codesign --verify --deep --strict 'release/安卓应用搬家.app'
unzip -tq release/Android-App-Mover-macOS-arm64.zip
```

当前编译包已在本机验证，未承诺支持未实测的 Intel Mac。
不需要桌面界面时，只安装基本依赖即可继续使用原命令行工具。

## 版本维护与提交约定

1. 桌面版本号在 `AndroidAppMover.spec` 的 `CFBundleShortVersionString` 中维护；同步更新本 README 的当前版本和 `CHANGELOG.md` 中文记录。
2. 后续功能、问题修复、验证方式与已知限制统一用中文写清楚。
3. 所有正式 `.app` 与 ZIP 都放在 `release/`，发布时与对应源码一起提交到本仓库的 `origin`。
4. Git 提交标题和正文使用中文，说明变更内容、原因及验证结果；不向上游仓库推送。
5. 不提交虚拟环境、构建中间文件、日志、手机备份、密钥或密码文件。
6. `update-readme.sh` 只生成可选的 HTML 预览，不会用英文帮助覆盖中文 README；`update-changelog.sh` 不再自动重写人工维护的中文版本说明。
7. 替换运行中的应用前，确认后台任务和 ADB 传输都已结束；没有 ADB 子进程并不代表后台校验任务已经结束。

详细要求见 [项目维护约定](AGENTS.md)。

<a id="what-is-hoardy-adb"></a>
## 上游工具与设计背景

`hoardy-adb` 原本是一个纯 Python 命令行工具：一方面简化 `adb` 的备份和恢复调用，另一方面处理 Android Backup 文件。
它支持列出内容、去除或重新应用加密与压缩、转换 TAR、按应用拆分，以及合并备份。
其设计参考了 [Adebar](https://codeberg.org/izzy/Adebar)、[android-backup-extractor](https://github.com/nelenkov/android-backup-extractor)、[android-backup-toolkit](https://sourceforge.net/projects/android-backup-toolkit/) 与 [android-backup-processor](https://sourceforge.net/projects/android-backup-processor/)。
项目曾使用名称 `abarms`，主要支持 Linux 和其他 Python 可运行的 POSIX 系统；上游也补充了 Windows 兼容处理。

<a id="why"></a>
<a id="adb-backup"></a>
### 为什么开发这个工具

上游作者希望个人设备拥有像传统 UNIX 与 TAR 一样可控的本地备份能力。
早期 Android 的 `adb backup` 可以通过设备端 `bu` 工具，将应用逐一写入 `.ab` 文件；其中包含带额外头部的 PAX 格式 TAR。
但 Android Platform Tools 没有提供足够方便的备份拆分、解密与重组工具。
一旦用户只想从整机备份恢复一个应用，就需要额外软件。

Android 还有 `bmgr` 备份服务，但可用传输方式、备份位置和访问权限受系统版本及设备策略影响。
应用的 `android:allowBackup` 设置也会影响传统备份是否包含应用数据。
厂商换机工具、云备份和应用自带备份不能完全替代用户可自主保存的本地副本。

作者曾尝试使用基于 Java 和多个脚本的工具链，发现构建、修改与运行较繁琐，因此实现了这个 Python 工具。
过程中还需要处理 Python 标准 `tarfile` 模块不适合的流式场景，以及 Android 对 PAX 头和加密校验的特殊要求。
项目希望改善本地备份体验，也鼓励应用和系统开发者提供可靠的备份能力。

以上是上游历史背景。现代 Android 是否支持 `bu`、`adb backup` 和数据恢复，应以实际设备为准；桌面版 APK 搬家不依赖传统 `.ab` 数据备份可用。

<a id="quickstart"></a>
## 命令行快速开始

### 准备环境

安装 Python 3 和 [Android Platform Tools](https://developer.android.com/tools/releases/platform-tools)。
Windows 安装 Python 时勾选 `Add python.exe to PATH`；Linux 和 macOS 可使用包管理器安装 Python。

安装上游命令行发行包：

```bash
python -m pip install hoardy-adb
python -m hoardy_adb --help
```

使用本 fork 的新增功能时，应安装本仓库源码，而不是依赖 PyPI 上游包：

```bash
python -m pip install -e .
hoardy-adb --help
```

`hyadb` 是同一命令行工具的简短入口。以下示例可在 `hyadb` 与 `hoardy-adb` 间替换。
Windows 可以在 `cmd.exe` 中执行，必要时使用 `python -m hoardy_adb`。

使用 Nix：

```bash
nix-env -i -f ./default.nix
hoardy-adb --help
```

进入上游开发环境：

```bash
nix-shell ./default.nix --arg developer true
```

<a id="backup-all-app-apks-and-then-restore-them"></a>
## 备份应用安装包并恢复

<a id="backup-all-apks"></a>
### 备份 APK

先在手机上开启 USB 调试、连接电脑并批准调试授权，然后执行：

```bash
hyadb backup-apks
```

单 APK 应用输出为 `backup_<日期>__<包名>.apk`。
拆分 APK 应用输出到 `backup_<日期>__<包名>/`，其中包含全部安装包。
这一操作只备份 APK，不包含应用数据，即使应用未允许传统数据备份，也不等同于能备份其私有数据。

包含系统应用：

```bash
hyadb backup-apks --system
```

<a id="restore-the-apks"></a>
### 恢复 APK

恢复一个应用：

```bash
hyadb restore-apks backup_2024-01-01__org.fdroid.fdroid.apk
```

恢复多个应用：

```bash
hyadb restore-apks backup_2024-01-01__*
```

命令行恢复不会递归遍历任意目录。每个应用需要单独作为一个参数传入；拆分 APK 应用应传入只包含该应用 APK 的目录。
`backup-apks` 按此结构输出，因此通常可以直接恢复。
默认跳过目标手机上已安装的应用，不覆盖后来更新的版本。
确实需要覆盖时，可明确使用 `--force`，或自行调用 `adb install`；这与桌面版默认不覆盖的策略不同。

<a id="backup-all-apps-from-your-android-device-then-restore-a-single-app-without-root"></a>
## 传统 Android Backup 备份与单应用恢复

这些步骤描述上游 `.ab` 文件功能，能否使用取决于 Android 版本及厂商实现。

### 准备手机与电脑

开启开发人员选项和 USB 调试，解锁手机后连接电脑，确认调试授权。
部分设备长时间备份会因休眠中断，可在实际需要时开启开发人员选项中的保持唤醒。
Windows 可手动执行 `adb start-server`。
Linux 应配置合适的设备访问规则；上游也给出了以下需要管理员权限的服务重启方式：

```bash
sudo adb kill-server
sudo adb start-server
```

### 创建整机备份

```bash
hoardy-adb backup
# 或者
hyadb backup
```

默认输出 `backup_<日期>.ab`。若自动确认未生效，请解锁手机并手动点击“备份我的数据”。

包含系统应用：

```bash
hyadb backup --system
```

恢复旧系统应用可能导致设备异常，上游不建议普通备份默认包含系统应用。
若需要自行控制原始备份参数，可在支持该功能的设备上执行：

```bash
adb shell bu backup -apk -obb -all -keyvalue -nosystem > backup_2024-01-01.ab
# 或者
adb backup -f backup_2024-01-01.ab -apk -obb -all -keyvalue -nosystem
```

这些参数基本对应 `hyadb backup` 的默认行为。

### 查看内容与拆分应用

```bash
hyadb ls backup_2024-01-01.ab
hyadb split backup_2024-01-01.ab
```

拆分后文件名称类似 `hoardy_adb_split_backup_2024-01-01__020_org.fdroid.fdroid.ab`。
将其中一个文件交给设备恢复，即可只恢复该应用：

```bash
adb shell bu restore < hoardy_adb_split_backup_2024-01-01__020_org.fdroid.fdroid.ab
# 或者
adb restore hoardy_adb_split_backup_2024-01-01__020_org.fdroid.fdroid.ab
```

### 从分片重建完整备份

```bash
hyadb merge hoardy_adb_split_backup_2024-01-01__*.ab backup_2024-01-01.rebuilt.ab
# 去除原始备份的加密与压缩后比较
hyadb strip backup_2024-01-01.ab backup_2024-01-01.stripped.ab
diff backup_2024-01-01.stripped.ab backup_2024-01-01.rebuilt.ab
```

<a id="usage"></a>
## 命令与参数说明

Android Backup 文件由元数据头部和 PAX 格式 TAR 组成，可选使用 zlib 压缩与 AES-256 加密。
输入解密参数适用于所有读取 `.ab` 的子命令，输出加密参数适用于所有生成 `.ab` 的子命令。

### 通用参数

| 参数 | 说明 |
| --- | --- |
| `--version` | 显示命令行工具版本后退出。 |
| `-h, --help` | 显示帮助。 |
| `--markdown` | 将帮助格式化为 Markdown；命令行原始帮助仍沿用上游语言。 |
| `-p, --passphrase PASSPHRASE` | 指定输入备份的解密口令。 |
| `--passfile PASSFILE` | 从文件逐字节读取口令，包括换行与特殊编码；默认尝试把输入 `.ab` 或 `.adb` 扩展名替换为 `.passphrase.txt`。 |
| `--ignore-checksum` | 忽略输入口令校验值，供特殊固件生成的备份使用。 |
| `--output-passphrase OUTPUT_PASSPHRASE` | 指定输出备份的加密口令。 |
| `--output-passfile OUTPUT_PASSFILE` | 从文件读取输出加密口令。 |
| `--output-salt-bytes SALT_BYTES` | PBKDF2HMAC 盐长度，默认 64 字节。 |
| `--output-iterations ITERATIONS` | PBKDF2HMAC 迭代次数，默认 10000。 |

输入是加密备份且未指定口令、口令文件，旁边也没有默认口令文件时，会从终端交互式读取口令。
输入路径为 `-` 时通常表示标准输入；支持的输出路径为 `-` 时表示标准输出。

### 子命令总览

| 子命令 | 别名 | 用途 |
| --- | --- | --- |
| `backup` | 无 | 调用设备端 `bu backup`，保存为 `.ab`。 |
| `backup-apks` | 无 | 备份可访问的应用 APK。 |
| `restore-apks` | 无 | 通过 `adb install` 或 `adb install-multiple` 安装 APK。 |
| `ls` | `list` | 列出备份内容与格式、压缩、加密参数。 |
| `rewrap` | `strip`、`ab2ab` | 去除或重新应用加密和压缩，内部 TAR 与版本参数保持不变。 |
| `split` | `ab2many` | 将完整备份拆分为按应用保存的备份。 |
| `merge` | `many2ab` | 把多个应用备份合并为一个，常用于验证拆分结果。 |
| `unwrap` | `ab2tar` | 解密、解压并去掉头部，输出原始 TAR。 |
| `wrap` | `tar2ab` | 为原始 TAR 增加 Android Backup 头部，可选压缩与加密。 |

### `hoardy-adb backup`

只备份设备允许备份的数据。

| 参数 | 说明 |
| --- | --- |
| `--system` | 包含系统应用；默认只包含用户应用。 |
| `--no-auto-confirm` | 不通过 `adb shell input` 尝试自动确认，由用户在手机端手动开始。 |
| `--to OUTPUT_AB_FILE` | 输出路径；默认 `backup_<日期>.ab`，`-` 表示标准输出。 |

### `hoardy-adb backup-apks`

通过包管理器查找 APK，再逐个拉取安装包；不包含应用数据。

| 参数 | 说明 |
| --- | --- |
| `--system` | 包含系统应用；默认仅用户应用。 |
| `--prefix PREFIX` | 输出文件名前缀，默认 `backup_<日期>`。 |

### `hoardy-adb restore-apks`

位置参数 `APK_OR_DIR` 可重复传入：单 APK 应用传文件，拆分 APK 应用传目录。
`--force` 强制重新安装目标设备已有的应用；默认跳过。

### `hoardy-adb ls`

位置参数 `INPUT_AB_FILE` 为输入备份，`-` 表示标准输入。
内容列表类似 `tar -tvf`，还会显示 Android Backup 版本、压缩及加密信息。

### `hoardy-adb rewrap`

位置参数为 `INPUT_AB_FILE` 和可选 `OUTPUT_AB_FILE`。
默认去除加密与压缩；普通文件的默认输出名使用 `.stripped.ab`，标准输入对应默认标准输出。
内部 TAR 和版本字段不被改写。
适合更换口令、去掉重复的存储加密，或改用更适合长期保存的外部压缩格式。

| 参数 | 说明 |
| --- | --- |
| `-k, --keep-compression` | 逐字节保留原有压缩标志和压缩数据，通常是最快的去加密方式。 |
| `-c, --compress` | 重新压缩输出，默认压缩级别高于 Android 常用默认值，可能耗时较长。 |
| `-e, --encrypt` | 加密输出；有 AES 加速的现代处理器开销通常较小，旧处理器可能较慢。 |

### `hoardy-adb split`

位置参数 `INPUT_AB_FILE` 为整机备份。
输出的单应用备份可分别交给 `adb restore`；定期备份时也可对这些结果进行文件级去重。

| 参数 | 说明 |
| --- | --- |
| `-c, --compress` | 压缩各输出备份，默认不压缩。 |
| `-e, --encrypt` | 加密各输出备份；共享输出口令参数，但每份文件的密钥和盐各自独立。 |
| `--prefix PREFIX` | 标准输入默认使用 `hoardy_adb_split_backup`，普通输入默认使用 `hoardy_adb_split_<去掉扩展名的输入文件名>`。 |

### `hoardy-adb merge`

依次传入多个 `INPUT_AB_FILE` 和最后一个 `OUTPUT_AB_FILE`。
`-c/--compress` 压缩合并结果，`-e/--encrypt` 加密结果，默认均关闭。
这是 `split` 的逆操作。

### `hoardy-adb unwrap`

位置参数为 `INPUT_AB_FILE` 和可选 `OUTPUT_TAR_FILE`。
自动解密、解压并去除 Android Backup 头部，内部 TAR 字节保持原样。
普通输入默认输出同名 `.tar`，也支持标准输入输出。

### `hoardy-adb wrap`

位置参数为 `INPUT_TAR_FILE` 和可选 `OUTPUT_AB_FILE`。
需要 `--output-version OUTPUT_VERSION` 明确指定 Android Backup 格式版本。
默认不压缩、不加密，可使用 `-c/--compress` 与 `-e/--encrypt`。
普通输入默认输出同名 `.ab`；输入为 `-` 时默认输出到标准输出。

**不要把 Android 备份随意解包、修改，再用普通 GNU tar 重新打包后恢复。**
设备恢复过程对文件顺序与 PAX 头有要求，普通 tar 重新生成的内容可能导致恢复失败甚至设备异常。
应使用 `unwrap` 原样产生的 TAR，或在充分理解格式约束后再操作。

<a id="examples"></a>
## 命令行示例

查看备份或用 tar 查看内容：

```bash
hyadb ls backup.ab
hyadb unwrap backup.ab - | tar -tvf -
```

提取备份中的文件：

```bash
hyadb unwrap backup.ab - | tar -xvf -
```

去除加密与压缩：

```bash
hyadb strip backup.ab backup.stripped.ab
# 使用默认输出名，效果相同
hyadb strip backup.ab
```

显式指定口令：

```bash
hyadb strip --passphrase secret backup.ab
# 简写形式
hyadb strip -p secret backup.ab
```

从文件读取口令：

```bash
printf '%s' secret > backup.passphrase.txt
hyadb strip backup.ab
# 显式指定文件，效果相同
hyadb strip --passfile backup.passphrase.txt backup.ab
```

保留包含换行或特殊字节的口令：

```bash
printf 'secret\r\n\000another line' > backup.passphrase.txt
hyadb strip backup.ab
```

只去掉加密，保留原压缩内容：

```bash
hyadb strip --keep-compression backup.ab backup.stripped.ab
# 简写并使用默认输出名
hyadb strip -k backup.ab
```

改用 xz 保存，并在需要时列出内容：

```bash
hyadb strip backup.ab - | xz --compress -9 - > backup.ab.xz
xzcat backup.ab.xz | hyadb unwrap - | tar -tvf -
```

转换 TAR：

```bash
hyadb unwrap backup.ab backup.tar
# 使用默认输出名
hyadb unwrap backup.ab
```

把原始 TAR 包装回格式版本 5 的 Android Backup：

```bash
hyadb wrap --output-version=5 backup.tar backup.ab
hyadb wrap --output-version=5 backup.tar
```

<a id="alternatives"></a>
## 其他工具与替代方案

以下是上游介绍过的相关工具，供理解方案差异，不代表本项目逐一验证了其当前兼容性。

### APK 备份与恢复

- [App Manager](https://f-droid.org/packages/io.github.muntashirakon.AppManager/)：在 Android 上选择应用并保存 APK。
- [BARIA](https://f-droid.org/packages/com.easwareapps.baria/)：选择应用后复制安装包。
- [Adebar](https://codeberg.org/izzy/Adebar)：提供 `getapk`、`restoreapks` 等脚本。
- 也可手动组合 `adb shell pm path <包名>`、`adb pull`、`adb install` 与 `adb install-multiple`。

### 传统备份与拆分

[AnatomicJC 的 ADB 示例](https://gist.github.com/AnatomicJC/e773dd55ae60ab0b2d6dd2351eb977c1) 展示了直接组合 ADB 命令按应用备份的方法。
反复调用传统备份可能需要多次解锁和确认；先生成完整备份再拆分通常更方便。
Adebar 还会生成带自动输入操作的脚本，但等待时间和锁屏可能影响长时间任务。

### 文件格式处理

- [android-backup-extractor](https://github.com/nelenkov/android-backup-extractor)：基于 Java 解密、解压并转换 TAR。
- [android-backup-toolkit](https://sourceforge.net/projects/android-backup-toolkit/)：在前者基础上提供按应用拆分等功能。
- [android-backup-processor](https://sourceforge.net/projects/android-backup-processor/)：相关工具链的较早实现。
- [abpy](https://github.com/xBZZZZ/abpy)：Python 实现的 Android Backup 与 TAR 双向转换工具。上游作者后来参考过其中的口令校验计算思路，以兼容 Android 文件格式的特殊历史行为。
- [ABX](https://github.com/info-lab/ABX)：去除未加密备份的头部，相当于不带解密支持的 `unwrap`。
- Adebar 的 `ab2tar`：依赖 `openssl` 与 `zlib-flate`，不提供解密支持。

<a id="if-you-have-root-access-on-your-device"></a>
### 已有 root 权限时

可以研究 [Neo Backup](https://f-droid.org/packages/com.machiav3lli.backup/)、[Syncthing-Fork](https://f-droid.org/packages/com.github.catfriend1.syncthingandroid/)，或 Adebar 的 `root_appbackup.sh`、`root_apprestore.sh`。
Syncthing 类工具不必依赖 root 才能同步可访问文件，但这不代表能够迁移其他应用的私有数据。

也可使用 `adb pull`，或由已授权的 root 环境执行 `tar`。
部分旧系统的本地备份传输示例：

```bash
adb shell bmgr enabled
adb shell bmgr list transports
adb shell bmgr transport com.android.localtransport/.LocalTransport
adb shell bmgr enable true
adb shell bmgr fullbackup
```

再从 `/data/data/com.android.localtransport/files/` 取回单应用备份；访问该路径通常需要额外权限。
这段历史示例是否可用取决于实际系统提供的备份传输组件。

<a id="frequently-asked-questions"></a>
## 常见问题

### `.ab` 中没有想备份的应用，能强制补进去吗

通常不能。
只需要安装包时可使用 `backup-apks`；恢复后不会保留应用数据状态。
若同时需要私有数据，应优先使用应用自己提供的备份与恢复功能。

上游提到可以重新打包并设置 `android:allowBackup`，再用自己的密钥重新签名。
但重新签名的 APK 通常不能直接覆盖原签名应用，需要先卸载原应用，导致数据丢失；每次更新也要重新处理。
可参考[相关技术说明](https://stackpointer.io/mobile/android-enable-adb-backup-for-any-app/462/)，但不能把它当成无损迁移现有数据的通用办法。

### 应用提供了自己的备份功能，怎么验证

先生成备份，再在另一台手机上安装原应用并验证恢复。
没有第二台手机时，上游也提到可使用 [Shelter](https://f-droid.org/packages/net.typeblog.shelter/) 在工作资料中测试；文件复制与权限处理需按其帮助操作。
确认可恢复后，再考虑更换安装版本，并用应用内恢复功能还原数据。

### 应用没有自带备份功能怎么办

可以向应用开发者请求开启受支持的数据备份或增加应用内导出功能。
已有 root 的设备可研究相应备份工具；普通 APK 搬家无法绕过应用私有目录权限。
不要为了重新签名安装而贸然卸载仍有重要数据的原应用。

### 还有哪些相关 F-Droid 工具

上游建议查看 F-Droid 的系统分类与搜索，也提到 [Droid-ify](https://f-droid.org/packages/com.looker.droidify/) 作为可选客户端。

<a id="development"></a>
## 开发、许可与贡献

命令行回归测试使用已有脚本：

```bash
./test-hyadb.sh --help
./test-hyadb.sh backup.ab backup2.ab
```

脚本支持 `--wine` 和 `--output-version VERSION`，用于测试环境与输出格式设置。
桌面版和 APK 模块使用 `tests/` 中的测试；测试通过模拟设备验证，不自动向真实手机安装或删除应用。

本项目使用 [GPLv3 或更高版本](LICENSE.txt)，部分小型库组件使用 MIT 许可。
第三方运行依赖的许可与说明随发布包提供，上游作者与版权声明保留。

欢迎在本仓库提交问题和合并请求。
向上游贡献时，也可按上游要求提供 `git format-patch` 形式的补丁；较大改动应先与对应维护者讨论。
