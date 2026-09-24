# 擎梦数智｜财报智问

上市公司财务智能分析平台 · 参赛作品说明

财报智问面向上市公司财务信息查询与研究场景，将自然语言问答、结构化财务数据、报告原文与分析结果连接起来，形成“提出问题—核验数据—解释变化—追溯来源”的研究流程。

作品包含财报问数、企业分析、研究资料库、数据中心、评测中心五个业务页面。默认进入财报问数，无需登录即可使用本地分析。本地问数不依赖模型 API Key，云端语言增强为可选能力。

## 核心能力

| 功能 | 评审观察重点 |
| --- | --- |
| 财报问数 | 企业、年度与指标识别，连续追问，受限只读 SQL 查询与校验 |
| 数因融合财务归因 | 净利润变化、经营指标与可核验报告解释的结构化呈现 |
| 企业分析 | 财务趋势、企业对比、风险提示与报告下载 |
| 研究资料库 | 企业及年度匹配，原文片段和 PDF 实际页序号 |
| 数据中心 | CSV、Excel、PDF 与公开披露接入，解析复核后确认入库 |
| 评测中心 | 隔离数据库中的固定回归题集，真实运行结果与逐项明细 |

结构化数字与报告证据按企业、年度和指标核验。缺失数据明确提示，不以其他企业、其他期间或默认分值替代。

## 评审体验路径

1. 在“财报问数”输入“比亚迪2024年营业收入是多少？”，查看答案、数据来源与查询过程。
2. 连续追问“那2023年呢？”，观察条件继承与年度切换。
3. 新建会话，输入“比亚迪2024年净利润为什么变化？”，查看摘要下方默认展开的“数因融合财务归因”。
4. 进入“企业分析”，查看趋势、风险提示和报告下载。
5. 在“研究资料库”核查报告，在“数据中心”查看文件解析、复核与来源记录。
6. 在“评测中心”运行 Benchmark，查看本次执行的通过数量和逐项结果。

### 归因树问句与范围

以下问句均支持归因分析：

- 比亚迪2024年净利润为什么变化？
- 比亚迪2024年净利润增长的主要原因是什么？
- 比亚迪2024年净利润为何增长？
- 比亚迪2024年净利润归因分析

归因树以净利润年度变化为根节点，营业收入和毛利率为分项线索，经营现金流为经营质量辅助信号。数值独立查询相邻两年；毛利率变化采用百分点，零基数不计算比例，负基数明确采用上年绝对值为分母。

当前支持单家企业的年度净利润变化。单一目标年度与上一年度比较，也可指定连续两个年度。未指定年度时披露采用的最新可用连续两年。普通“净利润是多少”不触发归因树；多企业、季度、半年或超过两个年度的归因不在当前支持范围内。

报告解释须符合企业、目标年度、指标及变化方向。没有充分的真实年报证据时显示“仅量化线索”。归因结果用于辅助分析，不代表严格因果贡献率。

## 双系统运行指南

完整解压源码包，在包含 app、scripts、requirements.txt 的项目根目录操作。安装脚本要求 Python 3.11，创建独立 .venv，安装精确版本的直接依赖并执行 pip check。首次安装需要网络，日常启动无需重复安装。

代码验收环境为 macOS / Python 3.12；与安装脚本要求的 Python 3.11 不同。Windows 与 macOS 的 Python 3.11 安装流程应在评审设备上提前完成自检，代码测试结果不替代目标设备验证。

## Windows 安装、运行与数据管理

### 1. 准备

1. 完整解压 ZIP，确认存在 `app`、`scripts`、`requirements.txt`。
2. 安装 Python 3.11，包含 pip 和 Python Launcher。可参考 [Python 官方安装说明](https://docs.python.org/3.11/using/windows.html)。
3. 右键项目文件夹空白处 → 在终端中打开 → PowerShell。不要在 ZIP 内或 `app` 子目录运行。

```powershell
py -3.11 --version
Get-Item .\requirements.txt, .\app\main.py
```

### 2. 首次安装（只做一次）

双击 `install_windows.bat`，或执行：

```powershell
py -3.11 scripts/install_local.py
```

脚本创建项目独立 `.venv`，按 `requirements.txt` 的精确版本安装直接依赖，并执行 `pip check`。首次安装需要联网；不会修改系统 Python 的依赖。已有 `.venv` 会校验 Python 版本，不会自动删除。失败时先处理终端错误，再重新执行安装。

若没有 `py`，但 `python --version` 确认为 Python 3.11，可执行 `python scripts/install_local.py`。不用激活虚拟环境，也不用修改 PowerShell 执行策略。

### 3. 日常启动

双击 `run_streamlit.bat`，或在 PowerShell 执行：

```powershell
.\run_streamlit.bat
```

打开 **http://127.0.0.1:8501**。脚本固定绑定当前电脑的 `127.0.0.1`，并设置 `FINANCIAL_DEPLOYMENT=local`。终端保持打开；停止时返回终端按 **Ctrl+C**。再使用时重复本节，不必重新安装。

浏览器标题为 **擎梦数智｜财报智问**。默认使用 **统一资料库**，内置企业与接入企业共享问答和分析入口，无需切换。在数据中心的“本地文件接入”或“公开披露接入”完成解析后，复核企业、年份、单位、数值及来源，再点击“确认入库”；成功提交后新企业立即可选。在“数据资产与来源”核查入库记录。“检索公开披露”、下载解析、确认入库分别显示真实状态。评测在内部隔离数据库执行，不读写业务资料。

### 4. 数据保存与自定义目录

默认持久目录为：

```text
%LOCALAPPDATA%\FinancialReportQA\data\
  main\investor_agent.db
  main\uploads\...
```

在线缓存及检索附属文件也位于对应资料库中。可在资源管理器地址栏输入 `%LOCALAPPDATA%\FinancialReportQA\data` 查看。项目升级时保留此目录；业务资料独立保存，不随公开源码包分发。

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

### 5. 备份、验证与恢复

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

### 6. 迁移上一版资料

若持久数据目录中有旧 `demo`、`personal` 两个目录，升级启动自动将它们依次合并到 `main`，用户库冲突指标优先；关联与文件路径重建，成功标记防重复，旧目录保留备份。先停止旧版并备份，详见 [迁移规则](docs/统一资料库与公开披露说明.md)。

更早的旧版可能保存为 `data\app.db` 或 `data\investor_agent.db`。**先停止旧版，再明确指定实际数据库文件和旧资料目录**；迁移路径须与实际资料位置一致。以下显式迁移命令目标下不能已经存在 `main`：已有业务库时请选择新的 `--data-dir`。

```powershell
.\.venv\Scripts\python.exe scripts/migrate_legacy_data.py --source-db "D:\旧项目\data\investor_agent.db" --source-files "D:\旧项目\data" --data-dir "D:\FinancialReportQA\migrated-data"
$env:FINANCIAL_DATA_DIR = "D:\FinancialReportQA\migrated-data"
.\run_streamlit.bat
```

显式迁移仅复制，不改旧库；原指标保留原值，不用内置值覆盖。旧版没有记录的单位、页码和单元格标记为 **来源待复核**，核验状态保持明确标识。新库的 `migration_manifest.json` 记录迁移数量、未核验来源及未匹配附件路径。进入统一资料库后先复核旧数据。出现 `MIGRATION_INCOMPLETE.json` 时不要启动该目标库。

### 7. 常见问题

| 现象 | 处理 |
| --- | --- |
| `.venv\Scripts\python.exe` 不存在 | 先运行 `install_windows.bat`；确认项目完整解压。 |
| `ModuleNotFoundError`、依赖冲突 | 使用安装脚本修复项目 `.venv`；不要混用系统 Python。 |
| 端口 8501 被占用 | 停止已运行的服务，或按本系统第 4 节手动启动并把端口改为 8502。 |
| 数据中心只读 | 用本地启动脚本，确认 `FINANCIAL_DEPLOYMENT=local`；公共部署仍只读。 |
| 换目录后数据不见了 | 检查 `FINANCIAL_DATA_DIR`，切回原值；不要先删除旧目录。 |
| 缺公司、报告期、指标 | 按提示补充资料；系统不会用其他公司、全年或最新年度替代。 |
| PDF 没有解析出指标 | 扫描件/复杂排版需人工核对；可上传带企业、年度、单位的 CSV/Excel。 |
| 在线下载失败 | 只接受白名单官方 HTTPS 直达 PDF；访问校验、非 PDF、超时或超 30 MB 会停止。请从官方网站下载后手动上传。 |
| 模型增强失败 | 检查网络和配置的 API Key；本地问数不依赖模型，事实校验失败会保留本地结果。 |
| 移动项目后环境失效 | 在新位置重新运行安装脚本建立环境，继续使用原持久数据目录。 |

`.xls` 由 `xlrd` 读取，`.xlsx` 由 `openpyxl` 读取。旧 `reset_db.py` 已禁用自动删除；需要新的资料位置时使用新数据目录。`run_local_network.bat` 是额外的局域网 **只读访问** 入口，使用 `public/main`，不提供上传和修改权限；不要把可写本地模式直接绑定公网。


## macOS 安装、运行与数据管理

### 1. 准备

完整解压项目，打开“终端”，进入包含 `app`、`scripts`、`requirements.txt` 的目录。路径有空格时保留引号：

```bash
cd "/项目的实际解压路径"
python3.11 --version
ls requirements.txt app/main.py
```

将第一行替换为实际项目目录；也可在终端输入 `cd `，把解压后的文件夹拖入终端，再按回车。需要 Python 3.11；可使用 [Python 官方 macOS 安装包说明](https://docs.python.org/3.11/using/mac.html)。不要使用系统自带的旧 Python，也不要使用 `sudo pip`。首次安装需要联网；本地分析不需要模型 API Key。

### 2. 首次安装（只做一次）

```bash
bash install_mac.sh
```

脚本优先调用 `python3.11`，其次检查 `python3` 是否恰为 3.11。创建项目专用 `.venv`，按 `requirements.txt` 安装精确版本的直接依赖，并运行 `pip check`。安装成功后方可启动服务。无需激活虚拟环境。

如需手动执行：

```bash
python3.11 scripts/install_local.py
```

安装程序不会删除已有 `.venv`。更换机器或移动项目后，需要在新位置重新建立虚拟环境；虚拟环境不可跨操作系统复制。

### 3. 日常启动与停止

```bash
bash run_streamlit_mac.sh
```

浏览器打开 **http://127.0.0.1:8501**。启动脚本只运行本地服务，**不会再次安装依赖**；固定 `FINANCIAL_DEPLOYMENT=local`，绑定 `127.0.0.1`。终端保持打开，按 **Control+C** 停止。使用 `bash` 运行脚本无需额外执行 `chmod`。

浏览器标题为 **擎梦数智｜财报智问**。默认使用 **统一资料库**，内置企业与接入企业共享问答和分析入口，无需切换。在数据中心的“本地文件接入”或“公开披露接入”完成解析后，复核企业、年度、单位、数值和来源，再点击“确认入库”；成功提交后新企业立即可选。在“数据资产与来源”核查入库记录。“检索公开披露”、下载解析和确认入库分别显示真实状态。评测在内部隔离数据库执行，不读写业务资料。

### 4. 持久数据目录

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

`FINANCIAL_DATA_DIR` 是 `main` 的上级数据目录。更换该值只会改变数据位置，不会搬迁原资料。可在终端配置中保存该环境变量；保留包含空格路径的双引号。

若直接用 Python 启动，必须同时设置本地模式和数据位置：

```bash
export FINANCIAL_DEPLOYMENT=local
export FINANCIAL_WORKSPACE=main
export FINANCIAL_DATA_DIR="$HOME/Library/Application Support/FinancialReportQA/data"
./.venv/bin/python -m streamlit run app/main.py --server.address 127.0.0.1 --server.port 8501
```

### 5. 备份、验证与恢复

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

### 6. 迁移旧版或从 Windows 搬来旧资料

若原数据目录中有旧 `demo`、`personal` 两个目录，升级启动自动将它们依次合并到 `main`，用户库冲突指标优先；关系与文件路径重建，成功标记防重复，旧目录保留备份。先停止旧版并备份，详见 [迁移规则](docs/统一资料库与公开披露说明.md)。

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

显式迁移只复制、不改旧数据，不以内置值覆盖原指标。旧版未保存的单位、页码和单元格标记 **来源待复核**。查看 `main/migration_manifest.json` 中的未核验记录和未匹配文件路径。出现 `MIGRATION_INCOMPLETE.json` 时不要启动该目标库。

若迁移的是本版创建的完整备份，优先用第 5 节恢复脚本，自动根据清单处理跨系统附件路径。

### 7. 常见问题与验证

| 现象 | 处理 |
| --- | --- |
| `python3.11: command not found` | 安装 Python 3.11 后重新打开终端；`python3` 也必须确认为 3.11。 |
| `.venv/bin/python` 不存在 | 在项目根目录先执行 `bash install_mac.sh`。 |
| 找不到包或依赖冲突 | 用安装脚本修复 `.venv`，不要混用系统 Python。 |
| 8501 端口被占用 | 停止已运行的服务，或将本系统第 4 节命令端口改为 8502。 |
| PDF 解析失败 | 扫描件和复杂表格需人工复核；可改用有明确企业、年度、单位的 Excel/CSV。 |
| 在线下载被拒绝 | 仅允许官方白名单 HTTPS 直达 PDF，限30 MB和30秒；网页访问校验不自动绕过。请手动从官网保存后上传。 |
| 导入数据不见 | 核对 `FINANCIAL_DATA_DIR` 是否与导入时相同，不要先删除目录。 |
| 上传按钮只读 | 使用本地启动脚本，确认 `FINANCIAL_DEPLOYMENT=local`；公共部署仍只读。 |
| CPU 架构或安装包不兼容 | 使用与当前 Apple Silicon/Intel 架构匹配的 Python 3.11，保留数据并重新创建环境；不要复制其他系统的虚拟环境。 |

安装完成后检查：基础问数 → 多轮追问 → 数据中心上传预览 → 明确确认入库 → 新企业问答与分析 → 报告下载 → 备份并恢复到新目录。依赖固定的是直接版本；目标 Mac 的轮子包兼容性与实际运行需要实机确认。旧 `reset_db.py` 不再自动删除数据库。

## 验证结果与适用边界

2026-09-25 本地代码验收：Python 3.12 全量测试 **448 项通过**，固定 Benchmark **55/55**，语法检查通过，Streamlit 健康接口和根页面均返回 HTTP 200。详细记录见 [技术验收记录](docs/20260925归因问答修复与验收.md)，实现原理见 [技术说明书](docs/技术说明书.md)。固定评测结果不代表任意财报或全市场数据的泛化准确率。

内置数据用于功能体验，实际研究应核验原始披露与数据口径。扫描 PDF、复杂排版和模糊单位需人工核验。真实云端模型接口、公开披露网络和评审设备兼容性需按实际环境验证。

云端实例的临时磁盘不保证重启后保留资料，长期保存需持久存储与备份。最近会话保存在服务器会话中，重启后不保证保留。公共部署默认只读，本地启动脚本允许接入与复核入库。评测使用隔离数据库，不读写业务资料。

系统用于财务研究与信息整理，不预测股价，不提供买卖指令或收益承诺。docs 中设计与历史验收材料按各自日期留存，当前运行方式以本 README 为准。
