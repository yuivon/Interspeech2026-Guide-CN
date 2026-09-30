# Interspeech 2026 中文论文导读 · 15篇开发样本

当前公开网站仍展示15篇开发样本。ISCA Archive 的1,375篇官方论文 metadata 已在本地完成核验，但全量中文分析尚待模型凭据，因此尚未替换公开样本站。网站提供样本分类统计、Dataset/Corpus 与 Evaluation/Benchmark 专项、全站搜索、站内论文详情、六维中文导读和本地收藏。

## 数据来源和现状

- 唯一论文目录来源：[ISCA Archive · Interspeech 2026](https://www.isca-archive.org/interspeech_2026/index.html)。每篇论文链接和 PDF 地址来自 ISCA 页面 DOM 与 `citation_pdf_url` 元数据，不按文件名拼接 PDF 地址。
- 参考 [ICLR2026-Guide-CN](https://github.com/JenniferZhao0531/ICLR2026-Guide-CN) 的信息架构和简洁 GitHub 风格；其仓库有 MIT License。本项目代码独立编写。
- 结构化统计由 `paper_analysis.json` 的已提取字段程序计算。未完成分析不会出现在推测性统计里。
- 当前 15 篇样本由当前模型逐篇阅读官方摘要与已提取全文后完成人工分析；未调用外部模型 API。官方 proceedings 已核验 1,375 篇唯一论文，metadata 完整；当前有15条分析可复用，另有1,360篇需模型分析。没有为全量下载 PDF 或提取全文。全量分析需配置 `OPENAI_API_KEY`、`OPENAI_MODEL`，兼容服务可另配 `OPENAI_BASE_URL`。

## 当前 15 篇样本状态

- 已完成：15/15 官方 metadata、Abstract、六维中文导读、结构化字段与 Dataset/Evaluation 判断；分析均使用现有全文并记录 `analysis_source=full_text`。统计由程序从这 15 篇真实标签计算。
- 当前样本分类为 8 篇临床语音表示、7 篇信息提取与检索；Dataset 专项 2 篇、Evaluation 专项 2 篇。15篇样本验证通过；全量 analysis 尚未开始，详见 `data/validation_report.json` 与本地忽略文件 `data/raw/official_metadata_validation.json`。
- 页面导航按完整目录设计为 13 个宏观方向 → 162 个归一化官方 session；映射来源是 proceedings 中184个原始 session 名称，论文的 `original_session` 保留不变。当前页面所有论文数、热点和专项计数都标为“样本统计”。
- 每篇分析的原文证据仅保存在本地忽略文件 `data/internal_evidence.json`，构建时公开详情页只保留证据类型、页码/章节和来源，不发布摘录文本。站点加载轻量索引；进入 `#paper/<paper_id>` 或展开 Abstract 时再按需载入论文详情。

## 目录

```text
data/             官方元数据、论文分析、统计与校验报告
data/raw/         官方 proceedings 快照和论文详情页缓存
data/taxonomy.json 全部官方 session 的两级导航映射
cache/pdfs/       已下载 PDF（不提交）
cache/text/       PDF 可检索正文（不提交）
logs/             采集、PDF、分析失败记录（不提交）
src/pipeline.py   采集、session 合并、PDF、分析、统计、构建和验证
site/data/papers-index.json 公开论文轻量索引
site/data/paper-details/     按需加载的论文详情 JSON
site/             GitHub Pages 静态站点
```

## 全新运行

需要 Python 3.9+。在仓库根目录运行：

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

复制 `.env.example` 为 `.env`，填入 `OPENAI_API_KEY`、`OPENAI_BASE_URL` 和 `OPENAI_MODEL`，再载入环境变量（`.env` 不会自动读取）：

```sh
cp .env.example .env
set -a
source .env
set +a
```

### 当前样本重新构建与验收

现有 15 篇已经分析完成。修改导航或页面后直接重新构建并验证，无需重抓 PDF 或重做分析：

```sh
.venv/bin/python src/pipeline.py build
.venv/bin/python src/pipeline.py validate
```

`src/analyze_papers.py --limit 15` 处理当前 15 篇样本；分析输入优先为正文 + 官方摘要，无正文时为官方摘要。它以 paper ID 和输入 SHA-256 续跑，验证 JSON Schema，失败写入 `logs/analysis_errors.json`。`narratives` 只把已经计算好的 stats 交给模型组织成分类文字，不让模型生成计数。

全量官方 metadata 已由独立命令采集，输出到 `data/raw/official_papers_metadata.json`，不会覆盖当前15篇样本：

```sh
.venv/bin/python src/pipeline.py crawl-official
```

配置 `OPENAI_API_KEY` 和 `OPENAI_MODEL` 后，可从全量 metadata 开始断点续跑分析；兼容 endpoint 可设置 `OPENAI_BASE_URL`：

```sh
.venv/bin/python src/pipeline.py analyze-official
```

`analyze-official --limit 20` 只处理最多20篇尚未完成或输入 hash 已变化的论文。分析按 paper ID 与输入 SHA-256 复用已有结果，JSON Schema 校验失败会写入 `logs/analysis_errors.json`。全量分析完成后再生成全量统计和站点；当前验证和公开站点仍只针对15篇样本。PDF 下载和正文提取命令保留为可选能力，不是摘要分析的前置条件。

LLM 分析同时读取正文与官方摘要；没有正文时仍可基于摘要分析，并将 `analysis_source` 标记为 `abstract`。严格 JSON Schema 校验失败会记入 `logs/analysis_errors.json`，不会静默当作成功。

### 本地预览

```sh
.venv/bin/python -m http.server 8000 --directory site
```

打开 <http://127.0.0.1:8000>。收藏存在浏览器 `localStorage` 的 `interspeech2026_favorites_v1` 键中。

### GitHub Pages

将仓库推送到 GitHub，并在仓库 Settings → Pages 将 Source 设为 GitHub Actions。工作流会发布 `site/` 目录。重新生成 `site/data/` 后推送，即可更新网页数据。

## 质量检查

`python src/pipeline.py validate` 写入 `data/validation_report.json`，列出样本字段、分析 schema、证据摘录、两级 taxonomy 覆盖、session 数、PDF、全文与专项统计检查。采集错误写入 `logs/crawl_errors.json`，分析失败写入 `logs/analysis_errors.json`。每次官方数据更新后重新运行采集、规范化、分析、统计、构建和验证。
