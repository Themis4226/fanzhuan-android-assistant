# 翻转外语 Android USB 辅助工具

Windows x64 的 ADB 截图、scrcpy 投屏、本地 OCR 和有界交互脚本。
采用实际截图识别页面，执行后再次检查；目前是分步工具，尚非适配所有课程的一键完整程序。
与微信、翻转外语、DeepSeek 或相关开源项目不存在官方隶属关系。

## 下载与启动

从本仓库 **Releases** 下载 `fanzhuan-android-windows-x64-*.zip`，校验同页 SHA256SUMS.txt，完整解压后双击 **翻转外语助手.exe**。
GitHub 的 Source code.zip 只有源码，不含运行环境；普通使用请选择 Windows 便携 ZIP。
便携包已包含 Python 3.13.13 x64、锁定版本的 Python 依赖、本地 OCR 模型、ADB 和 scrcpy 5.0.1。
OpenCV 保留截图/图像处理功能，未分发本项目不用的视频文件 FFmpeg 插件。
不复制原开发机的虚拟环境，不需要修改系统 PATH；按 Windows 10/11 x64 交付，已在 Windows 11 上验证。
手机仍需厂商 USB 驱动（Windows 未自动识别时）、开发者选项、USB 调试、解锁并允许 RSA 授权。

窗口提供连接检查、截图、本地 OCR、只读投屏、会话管理、单词/听力/语法阅读批次和停止按钮。
后台脚本日志直接显示在窗口内。新会话默认无付费请求、无自动答题；继续旧会话沿用原有授权和剩余额度。
详细操作见 GUI_USAGE.md。启动器使用 Windows .NET Framework 4.x，当前 Windows 已具备；无需另装 Python GUI 包。
不能只复制 exe，它需要同目录的脚本、runtime 和 tools；源码 ZIP 可以使用本地 .venv 或自行准备环境。
保留 start.cmd 作为兼容入口，launcher.py 为旧控制台菜单。

```powershell
.\python.cmd phone_capture.py doctor
.\python.cmd phone_capture.py capture --open
.\python.cmd assistant_step.py --live
.\python.cmd -m unittest discover -s tests -v
```

`python.cmd` 优先用包内 runtime，其次用项目 .venv；不要只复制单个 python.exe 或直接搬运 .venv。
解压目录建议较短，保留 runtime、tools、tests 和源文件的相对位置。更换目录后可直接重新运行。

## 自动操作与新会话

先手动打开小程序目标页，查看本地截图/OCR，核对题目和入口。
旧运行记录与授权不随包分发。每次新设备、新任务都需创建自己的会话；下面命令明确授权该会话自动答题/提交，但 API 额度为零：

```powershell
.\python.cmd session_init.py --authorize-answers
.\python.cmd daily_navigate.py
.\python.cmd daily_words.py --skip-review --count 5
```

会话绑定当前 USB 序列号，设备不符、页面变更、识别不清、未验证反馈时停止。
其他模块：`daily_spell.py` 处理屏幕拼写键盘；`daily_listening.py` 实际播放并等待计数增加；
`daily_choice_pages.py` 在明确指定的页面做选择和翻页；`daily_suggest.py` 最多提出并执行一次通过校验的模型建议。
各脚本可用 `--help` 查看参数。不要同时启动多个手机控制脚本。
选择题直接选项模式必须在 session_init 使用 `--arbitrary-choice-header "当前标题"` 明确列出允许的页面。
不能把“任务完成”理解成满分；不修改服务器进度，不伪造学习记录，不绕过登录和访问控制。

## v0.1.1 速度改进

四个每日任务脚本默认启用进程内 OCR 缓存：每次读取仍检查微信前台、获取新截图；
只有 PNG 字节完全相同时才复用文字和坐标。通知、页码、答案、时钟等任何画面变化都会重新 OCR。
保留模型返回后的题目核对、答后反馈、听力逐页实际播放及计数增加检查。
翻页后已验证的画面直接用于下一轮，去掉紧接着的重复读取；五张释义卡的读取次数由11次减为6次。
拼写把一个单词的字母点击合并为一次 ADB 传输，确认提交独立执行并检查页面。

本机三张本地任务图、每张连续识别三次的离线对比：OCR 总耗时5.505秒降至1.705秒，
此重复画面负载约3.23倍；文字、置信度和坐标一致。该结果不包含 USB、动画等待、模型响应或音频时长，
不代表完成所有每日任务快3.23倍。随后单个已准备词库的卡包真机实测约3分58秒、25题通过；实际同图缓存命中0次。
测试不调用 API、不控制手机，不增加模型费用。日常启动命令不变，解压新版后按原命令运行即可。

自行复测（默认使用合成题面、零 API）：

```powershell
.\python.cmd benchmark_speed.py
.\python.cmd -m unittest discover -s tests -v
```

`benchmark_speed.py --image "本地任务图.png"` 可测试自己的任务图，不会上传。
遇到异常可在四个每日任务脚本后加 `--no-ocr-cache` 禁用缓存。
`runs/当前会话/events.jsonl` 新增 `ocr_observation` 的 `elapsed_ms`、`cached`，结合 ADB 耗时排查瓶颈。
`fast_observer.py` 为公共缓存实现，`tests/test_speed_safety.py` 验证缓存失效、通知、翻页、播放和键盘边界。

## 可选 DeepSeek

在本机把 `.env.example` 复制为 `.env.local`，填写自己的 `DEEPSEEK_API_KEY`。
密钥不应发到聊天、GitHub 或共享压缩包；发行包只含空白模板。
模型 API 会产生服务商费用，且将所选题目相关 OCR 文字及位置发到官方接口；不发送原始截图、聊天、账号凭证或设备序列号。
当出现通知横幅时暂停。请只在任务页启用，不把聊天页面当作任务输入。

需要付费调用时，须在新会话明确开启，并给出次数上限，例如最多5次：

```powershell
.\python.cmd session_init.py --authorize-answers --allow-api --api-call-limit 5
```

API 默认额度0；每个会话最大100次，失败和超时也计入次数，不自动重试；次数上限不是金额硬上限。
`assistant_step.py` 独立单步模式仍会要求本机确认发送和点击。
不要通过编辑旧会话的 completed 状态绕过额度；已有未结束会话时初始化器会拒绝覆盖。

## 验证与排错

- 先运行离线测试，再检查 USB；OCR 合成演示用 `ocr_demo.cmd`，无需模型 API。
- `unauthorized`：解锁手机允许 USB 调试；无弹窗时拔插数据线。
- 无设备：使用数据线而非充电线，检查厂商驱动，保持只连接一台 USB 手机。
- 缺 DLL / 启动失败：先确认完整解压、Windows x64；部分机器可能需要 Microsoft Visual C++ 2015–2022 x64 Redistributable，请从微软官方安装。
- OCR 缺模型：重新解压完整环境包；程序不会自动联网补模型。
- 通知遮挡、页码不变、未知页面：保留现场，等待通知消失后重新核对，不反复盲点。
- `runs/` 仅在本机生成，包含截图、日志、文字和授权，不要把它提交到仓库。
- 使用 Windows 系统字体，不随包复制微软字体；中文显示异常时检查系统中文语言/字体组件。

离线测试与解压后运行验证记录见 `PACKAGE_VALIDATION.json`；真实页面布局可能随小程序更新而变化。
本包未在全新 Windows 虚拟机上验证，USB 驱动和系统运行库仍取决于目标电脑。

## 第三方组件与源码

各组件保留自己的许可证，见 `THIRD_PARTY_NOTICES.md` 和运行环境内的许可证文件。
便携包保留 FFmpeg、libusb、dav1d、SDL、GEOS 对应版本的源压缩包及 scrcpy 构建脚本；动态库可按各自条款替换。
不对 LGPL 组件的修改或为调试此类修改进行的逆向工程增加限制。
本项目原创脚本未另行授予开源许可证；第三方许可不因此改变。

便携 Python 的官方说明：[Python embeddable package](https://docs.python.org/3.13/using/windows.html#the-embeddable-package)。
Python 校验来源：[官方 Windows release manifest](https://www.python.org/ftp/python/3.13.13/windows-3.13.13.json)。
scrcpy 来源与校验：[官方 v5.0.1 Windows 文档](https://github.com/Genymobile/scrcpy/blob/v5.0.1/doc/windows.md)。

## 从源码准备开发环境

安装官方 Python 3.13 x64 后，在源码目录运行：

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-ocr.lock.txt
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

ADB/scrcpy 按上方官方链接放入 tools/scrcpy-win64-v5.0.1，或设置 ADB_PATH。
构建脚本 `packaging/build_portable.py` 从这些锁定依赖及经校验的官方文件生成独立源码、便携包和校验表。
