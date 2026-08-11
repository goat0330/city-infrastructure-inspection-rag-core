# V15–V23 提交回落诊断与后续实验说明

更新时间：2026-08-11

## 结论

当前仓库可追溯的最高平台基线是 V15，平台总分 48.63。后续 V18/V22/V23 的主要变化集中在文本重写、Gold Schema 规范化和 RAG 叙述增强，没有改善简要信息字段覆盖，反而破坏了 V15 已经稳定的平台文本匹配。

下一轮应以 V15 的 `merged/prediction.jsonl` 作为固定底稿，只做简要信息单变量 overlay。不能继续以 V23 的全量 Composer 输出作为生产底座。

## 证据边界

| 证据 | 位置 | 级别 |
|---|---|---|
| V15 48.63、简要正确率 31% | `runs/gold-schema-baseline-20260810/gold-schema-baseline.md`、`gold-schema-baseline.json` | 仓库记录的平台结果 |
| V15 92 条预测与发布产物 | `runs/submission-v15-bci-primary-restore-qwen-rag-12fold-official92-20260808/` | 可直接复核 |
| V23 运行、RAG fallback、Composer 模式 | `runs/submission-v23-main905f0a7-goldschema-v18-qwen-rag-12fold-official92-20260811/` | 可直接复核 |
| V18/V22/V23 截图分数 | 用户提供的平台截图/记录 | 外部记录，仓库无法独立证明 |

仓库中的校准报告记录 V15 为 48.63；同一报告中的 V18 校准记录为 47.95。用户上传记录另有 V18 46.64、V22 43.52、V23 45.91。由于这些版本名称对应的运行参数并不完全相同，不将它们强行合并成一条本地评分曲线。

## V15 与 V23 的实测差异

两套结果均为 92 份，比较文件分别是：

- V15：`runs/submission-v15-bci-primary-restore-qwen-rag-12fold-official92-20260808/merged/prediction.jsonl`
- V23：`runs/submission-v23-main905f0a7-goldschema-v18-qwen-rag-12fold-official92-20260811/semantic-merged/prediction.jsonl`

| 字段/对象 | 变化 |
|---|---:|
| 总体结论 | 92/92 |
| 病害记录发生变化 | 90/92 |
| 病害位置字段变化 | 约 3474 处 |
| 建议记录发生变化 | 92/92 |
| 成因发生变化 | 91/92 |
| 详细结论发生变化 | 79/92 |
| 安全影响发生变化 | 77/92 |
| 病害总条数 | 未变化，4943 |
| 建议总条数 | 未变化，666 |

这说明 V23 主要改写了表达和位置，而不是增加了事实。对于平台的文本一致性评分，这种全量改写风险高于收益。

## 总体结论 Composer 的负影响

V23 在 `src/extraction/pipeline.py` 中启用了 `gold_schema_mode=v18`，调用 `compose_gold_overall_conclusion`。结果是：

- V15 总体结论平均约 118 字，超过 200 字 5 份；
- V23 总体结论平均约 262 字，超过 200 字 35 份；
- V23 最长总体结论达到 2564 字，变成病害明细倾倒；
- V23 平台一致性检查为 `valid=false`，25 份过长，5 份混入处置动作。

因此，Composer 应保留为实验能力，但暂时退出正式提交路径。正式路径恢复 V15 的总体结论组织方式。

## 31% 简要正确率不变的证据

V15 与 V23 的简要字段覆盖数量完全相同：

| 字段 | V15 | V23 |
|---|---:|---:|
| 上一次总体评分 | 50/92 | 50/92 |
| 上一次总体等级 | 58/92 | 58/92 |
| 趋势 | 62/92 | 62/92 |
| 报告日期 | 92/92 | 92/92 |

V23 改动的是叙述、病害位置和建议表达，并没有增加简要字段的真实覆盖。因此 31% 不动是符合当前证据的，不是 RAG 没有“足够强”造成的。

V23 对少数评分的改动也没有形成有效突破：界石Ⅵ号桥的分项评分由异常的 `0.4/0.45/0.15` 改为 `79.89/98.19/84.93`；嘉悦、大佛寺、马桑溪和高家花园的总体值改成了“主桥；引桥”复合字符串。这些变化数量太少，且复合值未必符合平台单值字段匹配。

## 人工复核是否已进入生产

当前仓库中的 Oracle-10 材料仍是校准包：

- 10 份人工答案列为空；
- 包内 README 明确说明未修改 main、抽取器、RAG、LLM 或模板；
- 该材料没有生成新的正式提交包。

所以这批人工复核没有进入 V23 正式生成路径。下一步必须先形成有原文证据的简要字段 overlay，再接入 92 份 V15 底稿。

## 推荐修改顺序

### S0：冻结 V15

直接保留 V15 的 92 条预测作为基线，不重新调用 LLM。保留 V15 的：

- 总体结论；
- 病害位置；
- 建议位置和类别；
- 成因、详细结论、安全影响；
- BCI 主值和 `grade_mode=report`。

### S1：10 份人工简要字段 overlay

只修改设施名称、日期、评分、等级、历史评分、历史等级和趋势。病害、建议和所有叙述字段不变。该轮的唯一问题是：简要正确率是否脱离 31%。

### S2：历史字段单变量实验

如果 S1 没有变化，再只测试 `previous_overall_score / previous_overall_grade / trend`，每次只动一个字段族。不能把文件名等级直接当作正文事实，也不能从等级反推分数。

### S3：评分口径单变量实验

只比较报告明确最终评定值与 BCI/BSI 候选值，保留报告原始证据。V15 已经验证 BCI 主值方向较稳定，不应再次与 Composer、RAG 一起改。

### S4：叙述和 RAG

只有简要字段实验完成后，再优化总体结论、风险点、成因、详细结论和安全影响。RAG+LLM 只允许增强叙述字段；模型失败或证据不够时保留 V15 原文。

### S5：location 修复

V20 的叠词、编号、区间和描述回填修复单独验证，不与简要字段和 Composer 同轮提交。它主要服务详细文本一致性，不是 31% 正确率的直接主因。

## Review ZIP 应包含

大文件不放入 GitHub，单独 review ZIP 包含：

- 本报告；
- V15/V23 的 dispatch、run-summary、platform-consistency、package-validation；
- V15/V23 的 prediction.jsonl；
- V15/V23 的实际 submission.tar.gz；
- `pipeline.py`、`gold_schema_normalizer.py`、概要抽取器和语义合并相关源码快照；
- Oracle-10 README、MANUAL_GUIDE 和人工答案表；
- 一个不含 API Key、缓存、临时文件和 DOC 全集的文件清单。

## 云端 worker 验收标准

1. 确认 GitHub 分支中的报告与当前 main 源码一致；
2. 确认 V15 预测有 92 条、V23 预测有 92 条；
3. 复核 V15/V23 的总体结论和简要字段统计；
4. 确认 V23 `platform-consistency.valid=false` 的 25/5 问题；
5. 确认 Oracle-10 人工答案列仍为空，不能宣称人工修复已进入生产；
6. 不修改代码、不重跑 92 份、不创建新的提交包，只返回核验报告。

## 本轮明确不纳入

当前工作区已有的 `src/conversion/converter.py`、`src/inspection/cli.py`、`src/submission/batch.py` 和 `tests/conversion/test_converter.py` 未提交改动属于转换链路，和本轮 V15/V23 分数诊断无关，不纳入本次 GitHub 发布。
