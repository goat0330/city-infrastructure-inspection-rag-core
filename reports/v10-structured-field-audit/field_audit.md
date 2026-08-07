# v10 结构化字段审计与修复报告

## 审计边界

- **代码已修复**：见本包 overlay 与专项测试。
- **字段来源已确认**：仅限真实 10 份共同输入与其中 5 份完整 DOCX 渲染审计。
- **完整 92 before/after 未执行**：本次上传的 v10 task package 不包含官方 92 份原始 DOC/DOCX，不能伪造。
- **平台分数尚未验证**：本地审计不等同平台提分。

## Before → After 真实 10 样本

- 结构化 summary：10 × 13 = 130 个字段；实际值变化 **1 项**。
- 当前名称/日期/当前评分等级/组件评分在 10 份中改动 **0 项**，说明这些字段在当前 10 样本上没有审计证据支持批量改写。
- 唯一 scalar 值变化：`尹家湾桥式通道 previous_overall_score: 无 → 85.98`。原文 `1.2 上一次检测状况` 明确写 `BCI=85.98`；本次当前 BCI 也恰为 85.98，因此“previous==current 就清空”的启发式被删除，改为按来源章节隔离。

## 2019-11-20 日期核验

- 01__一处-太平水库大桥报告（原B级，现B级）.docx: `2019年11月20日`，来源 `封面中文日期`，block 21，原文 `二〇一九年十一月二十日`。
- 02__二处-界石立交主线IV号桥报告（原A级，现B级）.docx: `2019年11月20日`，来源 `封面中文日期`，block 21，原文 `二〇一九年十一月二十日`。
- 03__一处-官方院子桥式通道报告(原B级，现A级）.docx: `2019年11月20日`，来源 `封面中文日期`，block 21，原文 `二〇一九年十一月二十日`。
- 04__一处-小四沟人行天桥报告（原A级，现A级）.docx: `2019年11月20日`，来源 `封面中文日期`，block 21，原文 `二〇一九年十一月二十日`。
- 05__一处-还建人行天桥报告（原A级，现A级）.docx: `2019年11月20日`，来源 `封面中文日期`，block 21，原文 `二〇一九年十一月二十日`。

> 结论：这 5/5 份都明确来自封面真实日期，因此禁止把 2019年11月20日 当成批处理污染直接替换。剩余样本仍需完整 92 audit。

## Prediction → DOCX 映射

- 对前 5 份真实报告执行结构化字段→SubmissionDocument→官方 DOCX 表格的精确检查。
- 共 65 个 scalar 记录，**renderer mismatch = 0**。
- `deck_score→deck_system_score`、`deck_grade→deck_system_grade`、`trend→defect_development_trend`、`risk_points→major_risks` 当前映射未发现落错行证据，因此本轮没有修改 rendering 源码。

## 病害历史三列

- 真实 10 份共 237 条病害记录，数量前后不变。
- 有明确历史内容的非默认行：before 45 → after 45；本轮不靠宽泛组件句批量扩写。
- 三个历史字段全部“内部 missing”的行：before 0 → after 192。这些行最终 DOCX 显示 `无`，不再在内部伪装成有事实依据的 `否/无/无`。
- 历史表新增支持：逻辑列索引、横向合并列、多级表头、纵向合并/跨行位置继承、受限的无表头历史表；疾病级写入仍要求保守的位置+病害证据。

## 代码修复

1. `summary/extractor.py`：设施专用名称标签；1.2 历史窗口边界；previous BCI/grade 明示格式；组件评分不再假定第一列就是部位；历史表按 logical column 处理；删除 `previous==current` 值级清空。
2. `facility_context.py`：补齐 `车行地通道/车行通道` 类型识别，保留设施原称谓。
3. `defects/extractor.py`：历史表多级/合并/无表头解析；缺历史证据保持 internal missing；只在疾病+位置证据满足时填 history 三列。
4. `audit_structured_fields_v10.py`：可在完整仓库直接跑 92 份 field audit，并检查 SubmissionDocument 与最终 DOCX scalar 落位。

## 验证

- `tests/extraction + tests/rendering`: **131/131 passed**。
- `python -m compileall src scripts tests`: **passed**。
- 5 份真实最终 DOCX scalar 映射：**65/65 matched**。

## 剩余问题

- 完整 92 份 before/after 未执行：上传的 v10 task package 不含官方 92 份原始 DOC/DOCX。
- 88/92 同日期只能确认已抽查的 5 份均为真实封面日期，不能据此替代剩余 87 份来源审计。
- 平台 28% 是否提升尚未验证。
- 字段 conflict/ambiguous 需在完整 92 audit 后按 source anchor 逐项判断，不应按通用等级区间批量重算。

## 状态标签

- 代码已修复：是
- 字段来源已确认：部分（真实 10/92）
- 字段仍缺失：完整 92 audit 后确认
- 字段存在歧义：完整 92 audit 后确认
- 渲染映射已修复：现有映射无需源码修改，5 份真实样本验证通过
- 平台分数尚未验证：是
