# macOS 本地运行说明书

适用产品：**擎梦数智｜财报智问**，上市公司财务智能分析平台。需要 Python 3.11。**本次未在 Mac 实机运行**；脚本按 macOS Bash/Python 路径设计，数据管理脚本已在 Windows 的 Python 3.11 上实际验证。请在目标 Mac 完成安装和本地自检。

## 1. 准备

完整解压项目，打开“终端”，进入包含 `app`、`scripts`、`requirements.txt` 的目录。路径有空格时保留引号：

```bash
cd "/项目的实际解压路径"
python3.11 --version
ls requirements.txt app/main.py
```

将第一行替换为实际项目目录；也可在终端输入 `cd `，把解压后的文件夹拖入终端，再按回车。保留现有项目文件夹名称即可，不需要为品牌更新重命名工程。需要 Python 3.11；可使用 [Python 官方 macOS 安装包说明](https://docs.python.org/3.11/using/mac.html)。不要使用系统自带的旧 Python，也不要使用 `sudo pip`。首次安装需要联网；本地分析不需要模型 API Key。

## 2. 首次安装（只做一次）

```bash
bash install_mac.sh
```

脚本优先调用 `python3.11`，其次检查 `python3` 是否恰为 3.11。创建项目专用 `.venv`，按 `requirements.txt` 安装精确版本的直接依赖，并运行 `pip check`。只要返回错误就先处理，不要跳过错误继续启动。无需激活虚拟环境。

如需手动执行：

```bash
python3.11 scripts/install_local.py
```

安装程序不会删除已有 `.venv`。更换机器或移动项目后，需要在新位置重新建立虚拟环境；不要从 Windows 复制 `.venv` 到 Mac。

## 3. 日常启动与停止

```bash
bash run_streamlit_mac.sh
```

浏览器打开 **http://127.0.0.1:8501**。启动脚本只运行本地服务，**不会再次安装依赖**；固定 `FINANCIAL_DEPLOYMENT=local`，绑定 `127.0.0.1`。终端保持打开，按 **Control+C** 停止。使用 `bash` 运行脚本无需额外执行 `chmod`。

浏览器标题为 **擎梦数智｜财报智问**。默认使用 **统一资料库**，内置企业与接入企业共享问答和分析入口，无需切换。在数据中心的“本地文件接入”或“公开披露接入”完成解析后，复核企业、年度、单位、数值和来源，再点击“确认入库”；成功提交后新企业立即可选。在“数据资产与来源”核查入库记录。“检索公开披露”、下载解析和确认入库分别显示真实状态。评测在内部隔离数据库执行，不读写业务资料。

## 4. 持久数据目录

默认保存至：

```text
~/Library/Application Support/FinancialReportQA/data/
  main/investor_agent.db
  main/uploads/...
```

Finder 中按 **Command+Shift+G**，输入上面的 `~/Library/Application Support/FinancialReportQA/data` 即可查看。此目录在项目文件夹外，更新项目代码不应删除它。

自定义目录（每次运行使用同一值）：

```bash
export FINANCIAL_DATA_DIR="$HOME/Documents/FinancialReportQAData"
bash run_streamlit_mac.sh
```

`FINANCIAL_DATA_DIR` 是 `main` 的上级数据目录。更换该值只会改变数据位置，不会搬迁原资料。可在自己的终端配置中保存该环境变量；保留包含空格路径的双引号。

若直接用 Python 启动，必须同时设置本地模式和数据位置：

```bash
export FINANCIAL_DEPLOYMENT=local
export FINANCIAL_WORKSPACE=main
export FINANCIAL_DATA_DIR="$HOME/Library/Application Support/FinancialReportQA/data"
./.venv/bin/python -m streamlit run app/main.py --server.address 127.0.0.1 --server.port 8501
```

## 5. 备份、验证与恢复

备份使用 SQLite 在线快照 API，包含已提交 WAL 数据，同时复制附件并生成 SHA-256、大小清单；不会覆盖原文件。为获得整个资料集的一致备份，期间暂停上传、删除及外部文件编辑。输出目录必须在数据目录之外。

```bash
./.venv/bin/python scripts/backup_data.py --workspace main --output "$HOME/Documents/FinancialReportQA-Backups"
```

脚本优先读取 `FINANCIAL_DATA_DIR`，其次使用第 4 节默认位置；自定义目录也可用 `--data-dir "/实际数据目录"` 明确指定。记下输出的备份路径，并将下方 `backup-实际编号` 换成该目录名：

```bash
./.venv/bin/python scripts/verify_backup.py --backup "$HOME/Documents/FinancialReportQA-Backups/backup-实际编号"
```

恢复前停止服务，并选用 **尚不存在的全新目录**：

```bash
./.venv/bin/python scripts/restore_backup.py --backup "$HOME/Documents/FinancialReportQA-Backups/backup-实际编号" --data-dir "$HOME/Documents/FinancialReportQA-Restored"
export FINANCIAL_DATA_DIR="$HOME/Documents/FinancialReportQA-Restored"
bash run_streamlit_mac.sh
```

恢复会校验备份并更新原文附件路径。原库及备份都保留。出现 `RESTORE_INCOMPLETE.json` 表示未完成，不要用该目录启动；检查错误后选择另一新目录重试。

## 6. 迁移旧版或从 Windows 搬来旧资料

若原数据目录中有旧 `demo`、`personal` 两个目录，升级启动自动将它们依次合并到 `main`，用户库冲突指标优先；关系与文件路径重建，成功标记防重复，旧目录保留备份。先停止旧版并备份，详见 [迁移规则](统一资料库与公开披露说明.md)。

对于更早的单文件数据库，先停止旧版，并明确指定真正的旧 `app.db` 或 `investor_agent.db`。以下显式迁移命令的目标下不能已存在 `main`；已有业务库时请选一个新的目标目录。

```bash
./.venv/bin/python scripts/migrate_legacy_data.py --source-db "$HOME/Documents/旧项目/data/investor_agent.db" --source-files "$HOME/Documents/旧项目/data" --data-dir "$HOME/Documents/FinancialReportQA-Migrated"
export FINANCIAL_DATA_DIR="$HOME/Documents/FinancialReportQA-Migrated"
bash run_streamlit_mac.sh
```

从 Windows 复制旧资料到 Mac 时，如果数据库仍记录 Windows 绝对路径，可额外指定 `--original-files-root`。例如旧资料原来位于 `D:\旧项目\data`，现在已复制到 Mac 的 `~/Documents/old-data`：

```bash
./.venv/bin/python scripts/migrate_legacy_data.py --source-db "$HOME/Documents/old-data/investor_agent.db" --source-files "$HOME/Documents/old-data" --original-files-root 'D:\旧项目\data' --data-dir "$HOME/Documents/FinancialReportQA-Migrated"
```

显式迁移只复制、不改旧数据，不以内置值覆盖原指标。旧版未保存的单位、页码和单元格标记 **来源待复核**；不会伪造来源。查看 `main/migration_manifest.json` 中的未核验记录和未匹配文件路径。出现 `MIGRATION_INCOMPLETE.json` 时不要启动该目标库。

若迁移的是本版创建的完整备份，优先用第 5 节恢复脚本，自动根据清单处理跨系统附件路径。

## 7. 常见问题与验证

| 现象 | 处理 |
| --- | --- |
| `python3.11: command not found` | 安装 Python 3.11 后重新打开终端；`python3` 也必须确认为 3.11。 |
| `.venv/bin/python` 不存在 | 在项目根目录先执行 `bash install_mac.sh`。 |
| 找不到包或依赖冲突 | 用安装脚本修复 `.venv`，不要混用系统 Python。 |
| 8501 端口被占用 | 停止自己旧的服务，或将第 4 节命令端口改为 8502。 |
| PDF 解析失败 | 扫描件和复杂表格需人工复核；可改用有明确企业、年度、单位的 Excel/CSV。 |
| 在线下载被拒绝 | 仅允许官方白名单 HTTPS 直达 PDF，限30 MB和30秒；网页访问校验不自动绕过。请手动从官网保存后上传。 |
| 导入数据不见 | 核对 `FINANCIAL_DATA_DIR` 是否与导入时相同，不要先删除目录。 |
| 上传按钮只读 | 使用本地启动脚本，确认 `FINANCIAL_DEPLOYMENT=local`；公共部署仍只读。 |
| CPU 架构或安装包不兼容 | 使用与当前 Apple Silicon/Intel 架构匹配的 Python 3.11，保留数据并重新创建环境；不要复制其他系统的虚拟环境。 |

安装完成后检查：基础问数 → 多轮追问 → 数据中心上传预览 → 明确确认入库 → 新企业问答与分析 → 报告下载 → 备份并恢复到新目录。依赖固定的是直接版本；目标 Mac 的轮子包兼容性与实际运行需要实机确认。旧 `reset_db.py` 不再自动删除数据库。
