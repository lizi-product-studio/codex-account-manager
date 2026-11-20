# Codex 账号管家 (Codex Account Manager)

安全管理与切换多个 OpenAI Codex 桌面端账号，实时查看当前账号剩余额度、重置倒计时与订阅到期时间（支持 Windows & macOS）。

## 启动方式

### Windows
- **直接运行 exe**：`Codex账号管家.exe`（可运行 `python build.py` 打包生成，独立可执行，不依赖 Python 环境）
- **从源码运行**：`pip install -r requirements.txt` 后执行 `python app.py`（`--debug` 打开开发者工具，`--minimized` 最小化到托盘）
- **命令行自检（不弹窗、不改文件）**：`python selftest.py`

### macOS（从 Git 源码运行，支持自动更新）
1. 克隆仓库并创建虚拟环境安装依赖：
   ```bash
   git clone https://github.com/lizi-product-studio/dingxinglizi.git codex-account-manager
   cd codex-account-manager
   python3 -m venv .venv
   .venv/bin/python -m pip install --upgrade pip wheel
   .venv/bin/python -m pip install pywebview
   .venv/bin/python app.py
   ```
   > 若 Mac 使用系统自带 Python 3.9，安装时先锁定 `pyobjc-*==11.1`（预编译包），或使用 Homebrew 安装新版 Python（`brew install python@3.12`）。

## 核心特性
1. **安全的额度读取机制**
   - 账号邮箱、订阅套餐（Plus / Pro Lite / Pro / Team 等）与订阅到期时间离线解码自 `auth.json` 的 JWT 载荷；
   - **仅对当前正在使用的账号**通过本机官方 `codex app-server`（`account/rateLimits/read`）同步实时额度，绝不后台探测其他未登录存档账号；
   - 其余账号从本机 Codex 会话日志（`~/.codex/sessions/**/*.jsonl`）增量读取历史记录，并结合重置时间标注「实时 / 上次已知 / 应已重置」。
2. **多账号总览与快捷切换**
   - 顶部总览对比所有账号剩余额度与恢复时间，支持按「自定义顺序」或「剩余最多」排序，点击总览行快速定位卡片；
   - 支持卡片双击、总览一键切换、右键系统托盘（Windows）或 `Ctrl+1..9` 快捷切换，分步展示「关闭 Codex → 同步并备份 → 写入凭证 → 启动 Codex」；
   - 每次切换前自动回写当前账号最新 token 并备份到 `~/.codex-accounts/_backup/`（保留最近 20 份）。
3. **主动提醒与开机自启**
   - Windows 支持右下角托盘百分比图标与右键切号；macOS 通过原生系统通知中心推送提醒；
   - 支持当前号剩余不足 15%、用尽账号恢复、订阅 3 天内到期、新登录未保存及代码仓库新版本提醒；
   - 「＋ 登录新账号」后会在后台等待并在登录完成后自动唤起保存窗口。
4. **Git 自动更新与一键重启（源码运行模式）**
   - 启动时与后台自动运行 `git fetch` 检查远程仓库更新，发现新提交时发送系统通知并在顶部展示更新横幅；
   - 点击「立即更新并重启」自动拉取最新代码并重启程序（若本地有跟踪文件冲突，可选择先自动 `git stash` 暂存后再同步），**全程只更新代码，不触碰任何账号凭证**。

## 安全与隐私说明
- 所有账号存档、时间线与设置均保存在各自电脑的用户目录 `~/.codex-accounts/` 下，**绝不会保存在代码仓库内**。
- Windows 与 macOS 请分别登录并保存各自凭证，**切勿直接跨电脑拷贝 `.codex-accounts` 或 `auth.json`**，以免互相顶号或刷新令牌冲突。
- 添加或切换账号请使用本工具，不要在 Codex 内直接点“退出登录”（那会令已保存的凭证失效）。
