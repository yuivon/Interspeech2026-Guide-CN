# 架构说明

- `src/pipeline.py`：解析官方 ISCA proceedings、独立采集全量 metadata、规范化 session、调用可续跑分析、聚合统计、构建和验证静态站点。
- `data/raw/official_papers_metadata.json`：本地忽略的全量官方 metadata 快照；与公开构建输入 `data/papers.json` 的15篇开发样本隔离。
- `data/paper_analysis.json`：按 paper ID 保存分析结果；输入 hash 相同的已有结果可复用。
- `data/session_labels_zh.json`：normalized official session 到中文显示名的映射；原始 official session 保存在论文数据中。
- `site/`：可由 GitHub Pages 发布的静态网页及按需加载的数据。

全量 metadata 路径：官方 proceedings → `data/raw/official_papers_metadata.json` → `analyze-official`。当前公开构建仍读取15篇样本；全量分析完成前不切换统计输入或覆盖样本站点，避免把未分析论文误计为全会议结论。
