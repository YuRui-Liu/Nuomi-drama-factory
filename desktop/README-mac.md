# NuomiDrama macOS

首版目标：Apple Silicon arm64；当前构建主机 macOS 26.4。配置声明的最低系统版本不等于已经验证了旧系统兼容性，分发前需在目标系统验收。

## 构建

使用 uv 管理的 Python 3.12 环境安装项目依赖；默认读取仓库相邻的 `.venv/bin/python`，也可设置 `NUOMI_BUILD_PYTHON`。构建机器需要 Xcode Command Line Tools、FFmpeg、FFprobe、前端与根目录 Node 依赖。

```sh
pnpm desktop:build:mac
```

输出：`dist-desktop/NuomiDrama-1.1.4-mac-arm64.dmg`。
运行环境准备脚本只会替换 `desktop-runtime`。产物和运行环境不纳入 Git。macOS 配置独立于原 Windows 安装器。

应用通过随机回环端口启动内置 API，数据位于 Electron 的用户 Application Support 目录。不会自动迁移开发环境项目。AI 服务仍需联网及用户配置；内置运行环境不代表离线生成模型。

## 对外发布

- 安装 Developer ID Application 签名证书，使用 electron-builder 26 支持的签名环境变量。
- 设置公证凭据和 `NUOMI_NOTARIZE=1`，重新构建。凭据不得写入仓库。
- 正式包需检查签名、公证票据、首次启动、录音、文件导入与媒体导出。
- 本地无证书产物仅供试用，不应标注为已签名公证。不要通过禁用 Gatekeeper 作为安装说明。
- 媒体工具保留其许可证；发布者需履行所用 FFmpeg 构建及动态库的源代码分发义务。当前脚本随包保留本机 Homebrew 库的许可文件，对外发布前应归档对应源码与构建信息。
- 官网识别 `NuomiDrama-*.dmg`。只有准备发布授权版本时才上传 GitHub Release；本地构建命令使用 `--publish never`。

签名配置参考：https://www.electron.build/v26/docs/notarization/
