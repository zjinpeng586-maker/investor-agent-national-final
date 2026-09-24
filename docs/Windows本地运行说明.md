# Windows 本地运行说明书

适用产品：**擎梦数智｜财报智问**，上市公司财务智能分析平台。需要 Python 3.11。首次安装和日常启动分开；日常启动不会执行 pip 安装。以下命令均在完整解压后的项目根目录运行。

## 1. 准备

1. 完整解压 ZIP，确认存在 `app`、`scripts`、`requirements.txt`。
2. 安装 Python 3.11，包含 pip 和 Python Launcher。可参考 [Python 官方安装说明](https://docs.python.org/3.11/using/windows.html)。
3. 右键项目文件夹空白处 → 在终端中打开 → PowerShell。不要在 ZIP 内或 `app` 子目录运行。

```powershell
py -3.11 --version
Get-Item .\requirements.txt, .\app\main.py
```

## 2. 首次安装（只做一次）

双击 `install_windows.bat`，或执行：

```powershell
py -3.11 scripts/install_local.py
```

脚本创建项目独立 `.venv`，按 `requirements.txt` 的精确版本安装直接依赖，并执行 `pip check`。首次安装需要联网；不会修改系统 Python 的依赖。已有 `.venv` 会校验 Python 版本，不会自动删除。失败时先处理终端错误，再重新执行安装。

若没有 `py`，但 `python --version` 确认为 Python 3.11，可执行 `python scripts/install_local.py`。不用激活虚拟环境，也不用修改 PowerShell 执行策略。

## 3. 日常启动

双击 `run_streamlit.bat`，或在 PowerShell 执行：

```powershell
.\run_streamlit.bat
```

打开 **http://127.0.0.1:8501**。脚本固定绑定当前电脑的 `127.0.0.1`，并设置 `FINANCIAL_DEPLOYMENT=local`。终端保持打开；停止时返回终端按 **Ctrl+C**。再使用时重复本节，不必重新安装。

浏览器标题为 **擎梦数智｜财报智问**。默认使用 **统一资料库**，内置企业与接入企业共享问答和分析入口，无需切换。在数据中心的“本地文件接入”或“公开披露接入”完成解析后，复核企业、年份、单位、数值及来源，再点击“确认入库”；成功提交后新企业立即可选。在“数据资产与来源”核查入库记录。“检索公开披露”、下载解析、确认入库分别显示真实状态。评测在内部隔离数据库执行，不读写业务资料。

## 4. 数据保存与自定义目录

默认持久目录为：

```text
%LOCALAPPDATA%\FinancialReportQA\data\
  main\investor_agent.db
  main\uploads\...
```

在线缓存及检索附属文件也位于对应资料库中。可在资源管理器地址栏输入 `%LOCALAPPDATA%\FinancialReportQA\data` 查看。项目升级时保留此目录；不要把真实数据打进公开项目包。

自定义保存位置（路径示例请替换）：

```powershell
$env:FINANCIAL_DATA_DIR = "D:\FinancialReportQA\data"
.\run_streamlit.bat
```

此变量指定 **main 的上级数据目录**，不是某个 `.db` 文件。更换路径只会改变数据位置，不会搬迁原资料。当前 PowerShell 设置仅对此次终端有效；以后使用同一设置启动，或在 Windows 用户环境变量中保存相同值。

若要直接使用 Python 启动，必须设置同样的本地模式和持久目录：

```powershell
$env:FINANCIAL_DEPLOYMENT = "local"
$env:FINANCIAL_WORKSPACE = "main"
$env:FINANCIAL_DATA_DIR = Join-Path $env:LOCALAPPDATA "FinancialReportQA\data"
.\.venv\Scripts\python.exe -m streamlit run app/main.py --server.address 127.0.0.1 --server.port 8501
```

## 5. 备份、验证与恢复

备份不会删除或覆盖原文件。数据库使用 SQLite 在线快照 API，包含已经提交的 WAL 数据；附件逐文件复制并记录 SHA-256、大小。为得到整个资料集的一致备份，备份期间暂停上传、删除及外部编辑。备份目录必须在数据目录之外。

```powershell
.\.venv\Scripts\python.exe scripts/backup_data.py --workspace main --output "$env:USERPROFILE\Documents\FinancialReportQA-Backups"
```

若使用自定义数据目录，可传 `--data-dir "D:\FinancialReportQA\data"`；否则脚本优先读取 `FINANCIAL_DATA_DIR`，再使用本说明的默认持久目录。命令输出带日期和随机编号的备份路径。将下面 `backup-实际编号` 替换为该路径：

```powershell
.\.venv\Scripts\python.exe scripts/verify_backup.py --backup "$env:USERPROFILE\Documents\FinancialReportQA-Backups\backup-实际编号"
```

恢复前停止服务，并选择 **尚不存在的全新目录**：

```powershell
.\.venv\Scripts\python.exe scripts/restore_backup.py --backup "$env:USERPROFILE\Documents\FinancialReportQA-Backups\backup-实际编号" --data-dir "D:\FinancialReportQA\restored-data"
$env:FINANCIAL_DATA_DIR = "D:\FinancialReportQA\restored-data"
.\run_streamlit.bat
```

恢复先校验全部清单，再复制数据库和附件、更新原文文件路径。原库及备份不被覆盖。恢复目录出现 `RESTORE_INCOMPLETE.json` 表示失败，不要用于启动；查看文件中的原因并使用另一个新目录重试。

## 6. 迁移上一版资料

若持久数据目录中有旧 `demo`、`personal` 两个目录，升级启动自动将它们依次合并到 `main`，用户库冲突指标优先；关联与文件路径重建，成功标记防重复，旧目录保留备份。先停止旧版并备份，详见 [迁移规则](统一资料库与公开披露说明.md)。

更早的旧版可能保存为 `data\app.db` 或 `data\investor_agent.db`。**先停止旧版，再明确指定实际数据库文件和旧资料目录**；不能根据示例盲选文件。以下显式迁移命令目标下不能已经存在 `main`：已有业务库时请选择新的 `--data-dir`。

```powershell
.\.venv\Scripts\python.exe scripts/migrate_legacy_data.py --source-db "D:\旧项目\data\investor_agent.db" --source-files "D:\旧项目\data" --data-dir "D:\FinancialReportQA\migrated-data"
$env:FINANCIAL_DATA_DIR = "D:\FinancialReportQA\migrated-data"
.\run_streamlit.bat
```

显式迁移仅复制，不改旧库；原指标保留原值，不用内置值覆盖。旧版没有记录的单位、页码和单元格标记为 **来源待复核**，不会伪装成已验证数据。新库的 `migration_manifest.json` 记录迁移数量、未核验来源及未匹配附件路径。进入统一资料库后先复核旧数据。出现 `MIGRATION_INCOMPLETE.json` 时不要启动该目标库。

## 7. 常见问题

| 现象 | 处理 |
| --- | --- |
| `.venv\Scripts\python.exe` 不存在 | 先运行 `install_windows.bat`；确认项目完整解压。 |
| `ModuleNotFoundError`、依赖冲突 | 使用安装脚本修复项目 `.venv`；不要混用系统 Python。 |
| 端口 8501 被占用 | 停止自己旧的服务，或按第 4 节手动启动并把端口改为 8502。 |
| 数据中心只读 | 用本地启动脚本，确认 `FINANCIAL_DEPLOYMENT=local`；公共部署仍只读。 |
| 换目录后数据不见了 | 检查 `FINANCIAL_DATA_DIR`，切回原值；不要先删除旧目录。 |
| 缺公司、报告期、指标 | 按提示补充资料；系统不会用其他公司、全年或最新年度替代。 |
| PDF 没有解析出指标 | 扫描件/复杂排版需人工核对；可上传带企业、年度、单位的 CSV/Excel。 |
| 在线下载失败 | 只接受白名单官方 HTTPS 直达 PDF；访问校验、非 PDF、超时或超 30 MB 会停止。请从官方网站下载后手动上传。 |
| 模型增强失败 | 检查网络和自己的 API Key；本地问数不依赖模型，事实校验失败会保留本地结果。 |
| 移动项目后环境失效 | 在新位置重新运行安装脚本建立环境，继续使用原持久数据目录。 |

`.xls` 由 `xlrd` 读取，`.xlsx` 由 `openpyxl` 读取。旧 `reset_db.py` 已禁用自动删除；需要新的资料位置时使用新数据目录。`run_local_network.bat` 是额外的局域网 **只读访问** 入口，使用 `public/main`，不提供上传和修改权限；不要把可写本地模式直接绑定公网。

## 8. 验证范围

Windows 下已验证项目的实际 Python 3.11 运行环境和管理脚本；安装脚本为独立虚拟环境安装路径。跨平台依赖采用精确直接版本，不等同于锁定所有间接依赖。正式使用前在目标机器完成安装、问数、上传复核、报告下载及备份恢复自检。真实云端模型和官方披露网络可用性仍取决于对应服务。
