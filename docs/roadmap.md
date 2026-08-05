# 结果导向路线图

## B2-0：共享合同门禁

- [x] 区分 `gold_record.schema.json` 与 `prediction_record.schema.json`。
- [x] 将 `route`、`render`、`validate` 接入统一 CLI。
- [x] 增加最终 `.doc` 文件集合的 `tar.gz` 打包与清单校验。
- [x] 增加最小 GitHub Actions。
- [x] 在真实86份可配对报告上输出章节路由覆盖报告。
- [ ] 固定4个模糊配对的人工映射。

## B2：三个高权重抽取器并行（已完成首轮真实验证）

### 病害抽取（30分，最高优先级）

- [x] 定位病害表和跨页续表。
- [x] 恢复合并单元格、重复表头、同序号多行。
- [x] 按“一条具体病害观察”输出 `DefectObservation`。
- [x] 每条记录绑定 `SourceAnchor`。
- [x] 优先保证行召回与原文完整，后做类型归一化。

### 概要/评分抽取（20分）

- [x] 桥梁名称、报告日期候选。
- [x] 总体、上部、下部、桥面系评分与等级。
- [x] 建立来源优先级和候选冲突记录。
- [x] 禁止用 LLM 猜测数字。

### 建议抽取（20分）

- [x] 表格和处理建议章节双路抽取。
- [x] 建议内容、部位、类别、序号。
- [x] 类别不确定时输出质量标记；主预测链的 Gold-derived 类别推断显式记录质量标记。

### 评测与错题本

- [x] 保留严格内部 scorer 代理分。
- [x] 增加字段级诊断分、病害规模分桶、模板簇分桶。
- [x] 每次合并记录 commit、配置、分项得分和失败样本。

### B2 首轮门禁结果

- 以 `0000961` 为主线基线，161/161 份 Word-first 转换后的 DOCX 成功完成 `predict-batch`。
- 评测 manifest 的 `source_docx` 是唯一对齐依据：161 条原始预测自动选出并排序为 86 条 Gold 评测记录，排除 75 条非交集预测；缺失或歧义匹配 fail closed。
- benchmark 默认验证必须返回 `0` 并输出 `verify : OK`；`alignment.json`、`score.json`、`diagnostics.json`、`summaries.json`、`errorbook.md`、`leaderboard.csv` 和 `aligned-predictions.jsonl` 是验收产物。
- 当前 Round A 内部 scorer：全量 44.82606、fit 40.593175、holdout 46.820635；这些结果只用于固定 scorer/manifest/权重下的回归比较，不是比赛平台最终分。
- B2 仍保留显式未实现字段：`causes`、`treatments`、`safety_impact`。

### 后续提分项（保持 Word-first）

- [ ] `recommendations`：减少段落误召回，提高建议部位与建议内容的边界精度。
- [ ] 文本嵌套病害：识别嵌在结论/建议等长文本中的病害观察，按具体观察拆分并保留 `SourceAnchor`。
- [ ] 继续处理 8 个 Gold 失败样例、跨年度病害对齐和证据约束长文本生成。

## B3：端到端交付

```text
原始 DOC
→ DOCX
→ DocumentModel
→ route_sections
→ 三个抽取器
→ prediction.json
→ DOCX
→ 最终 DOC
→ 文件名/数量校验
→ tar.gz
```

当前已完成：DOCX → `prediction-v1` 和批量失败 sidecar；仍待完成：DOCX → 最终 `.doc` 导出、测试集全量交付、输入输出数量与包级联合验收。最终门禁仍为输入输出数量一致、失败显式记录、Gold 渲染回环100、最终包根目录只含对应 `.doc`。

## B4：后续能力

- 规则生成详细结论、病害成因、处置建议和安全影响。
- 桥梁身份统一与跨年度病害对齐。
- 轻量网页读取/分析接口。
- Docling/OCR/RAG 仅在明确验证收益或 Word 主链失败时启用。
