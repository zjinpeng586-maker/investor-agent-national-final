# 财报智问 V2.0 最终验收记录

> 验收日期：2026-09-21  
> 验收基线源码 tree：`55e4b10f99fd5d2c170ad433f7ffda2469b894c7`

## 自动化与静态检查

- `python -m compileall app core scripts tests`：通过。
- `python -m pytest -q`：50 passed。
- PDF 下载防护脚本：通过。
- 在线披露解析脚本：通过。
- `python -m pip check`：未发现依赖冲突。
- `git diff --check`：通过。
- Git 跟踪内容静态扫描：未发现 API Key、Bearer token、`.env` 或 `secrets.toml` 被提交。

## Benchmark

内置确定性回归 Benchmark 连续运行三次，结果均为 `29 / 29`；suite 为
`financial_qa_deterministic_v1`，版本 `1.0.0`，三次耗时分别为 300.440 ms、
248.076 ms、245.027 ms。三次的 case ID、通过状态和核心 actual 一致。

分类样本为：会话 5、Planner 5、Text-to-SQL 5、SQL 安全 9、RAG 引用 5。
该结果仅代表项目内置确定性回归集，不代表外部生产数据集的泛化能力。

## 核心 Demo smoke

- 连续问数：比亚迪 2024 营收 `7771.02` → 2023 营收 `6023.15` → 宁德时代
  2023 营收 `4009.17` → 净利润 `441.21`，四轮均由 SQL 成功返回。
- 企业对比：有效执行意图为 `company_compare`，SQL 同时返回比亚迪和宁德时代。
- 年报数字问题：路由为 `hybrid`，有效执行意图为 `finance_query`，SQL 返回比亚迪
  2024 净利润 `402.54`；当前基线无对应真实年报，RAG 如实返回 `no_evidence`。
- 新会话直接问“那2023年呢？”进入 company clarification，没有继承旧会话条件。
- 临时多页 PDF 回归覆盖研发第 1 页、海外风险第 2 页及净利润原因解释页；纯数字页、
  毛利率页和无关内容不会被用作净利润原因证据。

## UI 与本地运行

- AppTest 覆盖财报问数、企业分析、研究资料库、数据中心、评测中心五个入口。
- 评测中心初始不展示虚构百分比；运行后展示真实总数、分类、明细和 JSON 下载。
- Streamlit 使用全新进程连续启动两次；两次 `/_stcore/health` 均为 `ok`，根路径均为
  HTTP 200。
- 当前解释器为 Python 3.14.4；推荐的 Python 3.11 未在本 Codex 环境成功完成二次执行。

## Cloud readiness 与已知限制

代码具备 Streamlit Cloud 启动所需入口与依赖，但本轮没有获得可公开访问的 Cloud URL，
因此云端部署仍为人工待确认。Cloud 实例上的 SQLite 修改、上传 PDF、在线缓存与 RAG
sidecar 均属于运行时本地状态，实例重启后不保证持久化。复杂 PDF 排版和扫描件 OCR、
公网披露源稳定性、生产并发与多用户隔离不在本版保证范围内。

PyMuPDF 当前仍输出 `fitz` API 弃用提示，但页级抽取测试正常；封板阶段未更换 PDF 核心
实现。依赖使用兼容范围，未来仍需通过锁定环境或定期回归控制版本漂移。

## 人工待确认

- 在最终 Streamlit Cloud 环境完成部署、重启与持久化行为复核。
- 在比赛网络下验证公开披露检索和可选云端模型服务。
- 使用最终演示设备与真实年报 PDF 连续完整演练至少三次。
- 检查投影分辨率、中文字体、下载文件与演示账号/API Key 的现场配置。
