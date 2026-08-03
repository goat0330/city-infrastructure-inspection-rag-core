# 当前实现范围：Word-first

## 当前必须完成

- 读取旧版 `.doc`，用 LibreOffice 转换为 `.docx`。
- 用 `python-docx` 和 OOXML 保留原生段落、表格、合并单元格、行延续和图片关系。
- 解析训练集、验证集中的标签 DOCX，形成 Gold JSON。
- 从原始 Word 报告抽取评分、等级、病害行、建议行和固定章节文本。
- 建立桥梁身份和跨年度病害对齐。
- 用结构化 JSON 校验数字、等级、行数、空值和证据来源。
- 生成可打开、命名正确、字段完整的提交 DOCX。

## 当前不做

- 不全量运行 MinerU 或 PaddleOCR。
- 不把扫描页、图片 OCR、PDF 版面恢复作为默认处理链。
- 不引入 Milvus、Neo4j、RAGFlow、Dify 或通用 Agent 平台作为主运行时。
- 不在没有验证集收益前增加端到端微调和复杂 Prompt 优化。

## 后续候选

- Docling：作为统一文档结构和章节识别的对照解析器。
- Instructor + Pydantic：作为已经定位章节后的受约束字段抽取层。
- DocETL：只吸收 Map—Resolve—Reduce 的固定 Workflow 思想。
- MinerU/PaddleOCR：仅当少数 Word 转换失败、表格实际为图片或确实出现扫描页时按质量路由启用。
- `docxtpl`：当官方模板固定并需要保留复杂排版时引入。

当前仓库的核心验收不是“能聊天”，而是：Word 结构解析正确、50 分高权重表格字段抽取稳定、长文本事实一致、最终 DOCX 可交付。
