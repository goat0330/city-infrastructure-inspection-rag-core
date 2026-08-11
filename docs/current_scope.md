# 当前实现范围：Word-first

## 当前已完成

- `.doc` 可经 LibreOffice 逐份转换为 `.docx`；Windows 有 Word 时优先使用 Word COM，避免大表格和批量进程崩溃。
- `python-docx + OOXML` 保留段落、标题、表格、合并单元格、行延续和图片关系。
- 标签 DOCX 形成 Gold JSON；当前86份成功、8份明确失败。
- 本地加权评分、章节路由、最小 DOCX 渲染、单文档校验和 Gate 0 错题本已完成。

## 当前主任务

- 从原始 Word 报告抽取评分、等级、病害行和建议行。
- 三个抽取器统一输出 `prediction_record.schema.json`。
- 用真实Gold进行分项评测和错误分析。
- 打通 `Word → prediction.json → DOCX → DOC → tar.gz`。

## 当前不做

- 不全量运行 MinerU、PaddleOCR、PDF版面恢复。
- 不引入 Milvus、Neo4j、RAGFlow、Dify 或通用 Agent 作为主运行时。
- 不在没有验证集收益前增加端到端微调和复杂 Prompt。

当前验收不是“能聊天”，而是高权重字段抽取稳定、证据可回溯、最终Word与压缩包可交付。
