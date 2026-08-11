# V15 Concise Overlay 实验方案

## 目标

打开平台长期停留的简要信息正确率 31%，同时保持 V15 的 48.63 基线能力。所有实验都以现有 V15 的 92 条 `merged/prediction.jsonl` 为固定底稿，不重新调用 Qwen，不重新生成详细叙述。

## 当前判断

- V15 是当前可追溯的最高稳定平台基线；
- V18–V23 对总体结论、病害位置、建议位置和叙述字段进行了大范围重写，但没有增加简要硬字段覆盖；
- Gold 中历史字段非常保守：训练 Gold 的 `previous_overall_score` 全部为“无”，`previous_overall_grade` 全部为“无”，`trend` 绝大多数为“无”；
- 测试集不是训练 Gold 的同一批报告，因此 Gold 分布只作为“历史字段门控可能过宽”的假设证据，不能直接把测试集全部改成“无”；
- Oracle-10 人工答案表目前为空，不能宣称人工答案已经进入生产。

## 固定不变的内容

每个 concise overlay 版本都必须保持以下内容与 V15 完全相同：

- 病害明细及其数量；
- 建议明细、位置和类别；
- 总体结论、风险点、成因、详细结论和安全影响；
- 未被当前实验明确指定的全部 summary 字段；
- 92 份样本中的其余记录。

## Overlay 只允许改的字段

```text
bridge_name
report_date
overall_score
overall_grade
superstructure_score
superstructure_grade
substructure_score
substructure_grade
deck_score
deck_grade
previous_overall_score
previous_overall_grade
trend
```

每个变更值必须附带原始报告证据。文件名只能作为定位线索，不能单独决定字段值；不能从等级反推分数，也不能从分数推导等级。

## 实验顺序

### S0：V15 原样基线

直接复用 V15 的 92 条 prediction 和现有提交包，记录平台分数，不重新跑模型。

### S1：人工确认的 10 份简要字段

待人工答案表有真实答案和原文证据后，只覆盖这 10 份的简要字段，其余 82 份逐字保留 V15。第一轮不修改总体结论、风险点或详细字段。

### S2：历史字段单变量

只比较 `previous_overall_score`、`previous_overall_grade`、`trend`。每次只改变一个字段族，并且只有正文存在明确历史评定或病害对比证据时才填值；“有历次检测章节”本身不够。

### S3：评分语义单变量

只验证明确最终评定值、BCI 值和 BSI 候选值的选择，不同时修改等级、总体结论或其他文本。界石Ⅵ号桥的分项权重修复作为已确认正向样例单独保留。

### S4：叙述字段

只有 S1–S3 中某个字段族让平台正确率发生变化后，才继续处理总体结论、风险点和 RAG 叙述。RAG/LLM 只能增强成因、详细结论和安全影响；失败或证据不足时保留 V15 原文。

### S5：location 修复

V20 的叠词折叠、编号保护、区间剥除和描述回填另做详细一致性实验，不与 concise overlay、Composer 或 RAG 同轮提交。

## 发布规则

1. 不启用 V23 的 `gold_schema_mode=v18` Composer；
2. 不进行全量 location/category 重映射；
3. 不把 RAG 结果写入简要硬字段；
4. 不用“全部填无”作为默认候选；
5. 一次只改一个字段族；
6. 平台分数没有变化，就不推广该规则到 92 份；
7. 只有平台结果确认有效的规则，才进入正式 extractor。

## 当前最小下一步

先补齐 Oracle-10 中真实人工答案和原文证据，然后从 V15 prediction 复制出 S1 overlay。没有人工答案时，不修改历史字段，也不重跑 V23 全量管线。
