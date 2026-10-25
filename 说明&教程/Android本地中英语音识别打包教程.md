# Android 本地中英语音识别：模型准备与 APK 打包

适用于已经包含 `AionApp/scripts/prepare-local-asr.py` 的小家源码版本。本文命令以 Windows PowerShell 为例。

## 1. 先分清：安装 APK 和从源码打包

- **安装已经包含模型的 APK**：手机不需要另外下载模型，也不需要安装或启动独立语音测试 App。
- **拉取源码后自己打包**：电脑需要先准备模型和运行库，然后一起打进 APK。大文件没有提交到 Git，所以只更新代码、直接点打包，还不够。
- **已有完整模型文件**：准备脚本会检查 SHA256，校验通过就复用，不会每次重新下载。

识别在手机上完成，但小家的聊天服务仍需要正常连接服务器。松手发送时，仍会上传语音附件供回听，并携带本地识别出的文字；这不等于整个小家都能离线使用。

## 2. 使用哪个模型，需要哪些文件

采用 **sherpa-onnx 1.13.8** 运行库和以下官方中英双语流式模型：

```text
sherpa-onnx-streaming-zipformer-small-bilingual-zh-en-2023-02-16
```

这是 small bilingual Zipformer transducer，支持边说边识别中文和英文。当前组合为 INT8 encoder、FP32 decoder、INT8 joiner；请使用下面的准确文件名，不要随意替换成同一归档里的其他版本。

| 文件 | 用途 | 大小（约） |
| --- | --- | --- |
| `encoder-epoch-99-avg-1.int8.onnx` | 音频编码模型 | 43.0 MB |
| `decoder-epoch-99-avg-1.onnx` | 解码模型 | 13.9 MB |
| `joiner-epoch-99-avg-1.int8.onnx` | 联合网络模型 | 3.2 MB |
| `tokens.txt` | 词表 | 56 KB |
| `sherpa-onnx-1.13.8.aar` | Android 识别运行库 | 约 48 MB |

四个模型文件共 **60,142,871 字节，约 60 MB（57.4 MiB）**。官方完整模型压缩包约 458 MB，包含其他模型变体；准备脚本只保留上表需要的四个文件。运行库中的不同 CPU 原生库也会影响最终 APK 大小。

官方来源：

- [Zipformer 模型说明](https://k2-fsa.github.io/sherpa/onnx/pretrained_models/online-transducer/zipformer-transducer-models.html)
- [模型压缩包下载](https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/sherpa-onnx-streaming-zipformer-small-bilingual-zh-en-2023-02-16.tar.bz2)
- [Android AAR 下载](https://github.com/k2-fsa/sherpa-onnx/releases/download/v1.13.8/sherpa-onnx-1.13.8.aar)

下载脚本固定了这些文件的 SHA256，完成下载后会验证内容。通常直接运行脚本即可，不需要逐个手动下载。

## 3. 电脑准备

先拉取包含本功能的完整项目代码，准备：

1. **Python 3**：运行模型准备脚本，仅使用 Python 标准库，不需要额外 `pip install`。
2. **curl**：下载脚本会调用系统 `curl` 程序。Windows 可用 `curl.exe --version` 检查是否安装。
3. **Android Studio / Android SDK**：当前工程使用 Android SDK Platform 34、Android Gradle Plugin 8.2.2、Gradle Wrapper 8.5。首次构建还会下载 Gradle 和 Maven 依赖。
4. **JDK**：配置能运行当前工程的 JDK；本次打包使用 Android Studio 自带的 JBR 21。命令行构建通过 `JAVA_HOME` 指定 JDK。

用 Android Studio 打开项目的 `AionApp` 文件夹，并通过 SDK Manager 安装所需 SDK。可以让 Android Studio 自动生成本机的 `local.properties`，不要直接复制别人的电脑路径。

如果命令行报找不到 Android SDK，可在 `AionApp/local.properties` 中填写自己的实际路径，例如：

```properties
sdk.dir=C:/Users/你的用户名/AppData/Local/Android/Sdk
```

## 4. 下载并准备模型

打开 PowerShell，进入自己电脑上的 `AionApp` 目录。下面的路径只是示例，请替换为实际位置：

```powershell
cd 'D:\你的项目目录\AionsHome\AionApp'
python scripts/prepare-local-asr.py
```

如果电脑使用 Python Launcher，也可以将 `python` 换成 `py -3`。

首次运行需要访问 GitHub，下载量约为模型归档 458 MB 加运行库 48 MB；Gradle 构建所需下载另算。脚本会解出需要的文件、校验 SHA256，并自动放到以下位置：

```text
AionApp/
└─ app/
   ├─ libs/
   │  └─ sherpa-onnx-1.13.8.aar
   └─ src/main/assets/asr/
      ├─ SHERPA-LICENSE.txt
      └─ zipformer-small-bilingual/
         ├─ encoder-epoch-99-avg-1.int8.onnx
         ├─ decoder-epoch-99-avg-1.onnx
         ├─ joiner-epoch-99-avg-1.int8.onnx
         └─ tokens.txt
```

`SHERPA-LICENSE.txt` 随项目源码提供，其余上述二进制资源由准备脚本下载。成功时会输出：

```text
Verified sherpa-onnx 1.13.8 and bilingual model (60,142,871 bytes).
```

看到这条信息后再打包。**当前 Gradle 构建不会自动运行模型准备脚本**，首次准备这一步不能省略。

### 如果已经手动下载了文件

将 AAR 放到 `AionApp/app/libs/sherpa-onnx-1.13.8.aar`。解压官方模型包后，可以将所需四个文件直接放入上面的模型目录，再运行准备脚本校验。

也可以让脚本从现有模型目录复制，例如在 `AionApp` 目录执行：

```powershell
python scripts/prepare-local-asr.py --trial-model 'D:\模型目录\sherpa-onnx-streaming-zipformer-small-bilingual-zh-en-2023-02-16'
```

虽然参数名叫 `--trial-model`，任何包含上述四个正确文件的文件夹都可以使用。参数应指向直接包含 `.onnx` 和 `tokens.txt` 的那一层目录。**该参数只复用模型；AAR 如果尚未就位，脚本仍会联网下载运行库。**

## 5. 打包 APK

继续在 `AionApp` 目录操作。先设置自己的 JDK 路径，以下为 Android Studio 常见安装位置示例：

```powershell
$env:JAVA_HOME = 'C:\Program Files\Android\Android Studio\jbr'
```

### 给 vivo X300 Pro 等 ARM64 手机打包

```powershell
.\gradlew.bat assembleDebug -PtargetAbi=arm64-v8a
```

仅打入 ARM64 原生库，可以缩小 APK。本次 vivo X300 Pro 使用的就是这种构建。

如果不确定目标手机架构，已连接 ADB 时可检查：

```powershell
adb shell getprop ro.product.cpu.abilist
```

### 为不同架构设备准备通用 APK

```powershell
.\gradlew.bat assembleDebug
```

省略 `targetAbi` 会打入运行库包含的所有 CPU 类型，APK 会更大。模型仍是同一份，不会按 CPU 重复保存。

构建显示 `BUILD SUCCESSFUL` 后，APK 位于：

```text
AionApp/app/build/outputs/apk/debug/app-debug.apk
```

手机直接安装这个 APK 即可，不需要另装模型 App。USB 调试连接电脑后，也可以在 `AionApp` 目录执行：

```powershell
adb install -r app/build/outputs/apk/debug/app-debug.apk
```

`-r` 用于覆盖安装并保留数据，前提是应用包名和签名兼容。上述命令生成的是 debug APK；如果正式发布给其他人，请通过 Android Studio 的签名 APK 功能使用自己的发布密钥，并保持后续更新使用同一签名。

## 6. 同步更新小家网页端并启用

本功能同时涉及 Android 原生代码和 `aion-chat` 网页代码，部署时需要一起更新。只装新版 APK、服务器仍提供旧版聊天页面，可能不会出现本地识别入口或实时字幕。

1. 更新服务器上的 `aion-chat` 到同一版本，按项目原有方式运行服务。
2. 安装包含模型的新版 APK，完全退出小家后重新启动，让客户端检查前端资源更新。
3. 打开 **设置 → 语音转文字 → 本机聊天语音识别**。
4. 选择 **手机本地 · 中英流式识别**，选择后立即保存在这台手机上。
5. 回到主聊天或聊天室，允许麦克风权限，按住说话。

预期体验：按住时边识别边显示文字，松手后发送，上滑后松手取消。选择手机本地时，设置页会隐藏云端配置；切回云端时恢复显示，之前填的配置仍保留。

此选择只影响主聊天和聊天室的按住说话语音消息，通话、语音唤醒等其他功能沿用原有配置。普通浏览器或没有本地识别桥的旧客户端仍走云端方案。

## 7. 日常使用与限制

- 模型随 APK 保存在手机上。重启手机后无需启动其他 App，直接打开小家使用。
- 第一次按住会加载模型；识别使用单线程 CPU，按住期间才持续处理音频。
- 松手后完成最后的转写；取消、退后台或麦克风中断会停止录音。会话结束后空闲 30 秒释放模型，单条录音上限为 2 分钟。
- **30 秒指模型空闲释放，不是“静音 30 秒自动结束录音”。**
- 中文或英文单独成句时表现较好，中英混杂可能出现更多错字。识别失败不会自动把这次录音送去云端转写，可手动切换云端后重新录制。
- 本地转写不调用付费云端 ASR；聊天模型等其他服务仍按原有配置运行。

## 8. 常见问题

**下载失败、连接超时**

先确认电脑能访问上面的 GitHub 下载链接，再重新运行脚本；也可按第 4 节手动下载并放置文件。已校验通过的文件会复用。准备脚本不会替你配置网络代理。

**出现 SHA256 mismatch**

文件可能下载不完整，或下载了不同版本/不同量化文件。按照上面的准确文件名重新下载，再运行脚本验证；不要通过修改脚本哈希绕过校验。

**构建缺少 sherpa-onnx AAR，或者手机提示没有本地识别**

确认已经运行准备脚本，AAR 和四个模型文件位于第 4 节所示目录，并且使用的是包含本地 ASR 桥的新版源码。补齐文件后重新打包、安装；不要只把模型复制到电脑而继续安装旧 APK。

**设置里已经有入口，但聊天界面仍未实时出字**

确认选择了“手机本地”，服务器上的 `aion-chat` 也已更新，并让手机重新加载最新前端资源；同时检查麦克风权限及是否有通话占用麦克风。

**覆盖安装提示签名不一致**

不同电脑的 debug 签名可能不同。优先使用与已安装版本相同的签名打包。不要为了消除错误直接卸载旧应用；卸载会删除应用本地数据，若确实需要换签名，应先处理好数据备份。

**提交代码时为什么看不到模型文件**

这是预期行为：`.gitignore` 排除了 AAR 和模型目录。提交时应包含下载脚本、Android/网页端接入代码以及许可证文件、教程。其他人首次拉取代码后运行准备脚本即可获取固定版本资源；下载脚本和本教程都不要遗漏。
