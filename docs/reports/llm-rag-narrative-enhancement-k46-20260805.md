# LLM + RAG Narrative Enhancement 校准报告

日期：2026-08-05

本报告记录 Word-first 确定性抽取主线之上的第一轮 LLM + RAG Narrative 校准。目标只覆盖四个文本字段：`detailed_conclusion`、`causes`、`treatments`、`safety_impact`；桥梁名称、日期、评分、等级、病害、建议明细和历史字段仍由 Baseline 锁定。

本轮没有批跑 92 份初赛测试集，也没有修改 Gold、评分器、确定性抽取器或提交打包器。当前仓库 HEAD 已包含此前 Word-first/模板集成；本报告只评价 Narrative 校准和本轮审计修正。

## 1. K46 单样本校准

样本：

```text
source = runs/p0-converted/2012年/上界路K46+500上跨车行桥.docx
sample_id = 2012年-上界路K46+500上跨车行桥
split = fit
artifact = runs/narrative-k46-20260805/real-run-p0-v4/
```

本轮实际成功运行了 4 个独立任务 Query。D 组最终召回 6 条：

```text
report_evidence  × 3
domain_knowledge × 2   (raw kind = knowledge_card)
label_example    × 1   (raw kind = gold_label)
```

6 条均为 `split=fit`；当前样本的 Gold 标签没有进入召回结果。D 组 11 次模型调用中，8 次为检索调用，1 次为 Narrative 生成；Prompt 为 7,295 tokens，生成 1,131 tokens，耗时约 180.3 秒。

| 组别 | Prompt tokens | Completion tokens | 新事实旗标 | 引用审计 | 锁定字段 |
|---|---:|---:|---|---|---|
| A 规则 Baseline | 0 | 0 | false | true | true |
| B 无 RAG | 1,570 | 834 | true | false（无 RAG 消融的预期结果） | true |
| C 当前报告证据 | 3,644 | 1,150 | true | true | true |
| D 当前报告 + 专业知识 + 标签范例 | 7,295 | 1,131 | true | true | true |

`has_new_facts=true` 现在是实际审计结果，不再被错误的 Mapping 遍历逻辑掩盖。D 的 4 条旗标主要来自没有逐段 `evidence_ids` 的详细结论综合改写；它们是人工复核信号，不等价于已经确认的错误事实。详细结论引用 Sidecar 仍是下一步最小补强项。

## 2. 五样本真实验证

样本选择和实际数据约束如下：

| 样本 | split | 选择理由 | D Prompt |
|---|---|---|---:|
| 杨公桥 A 叉口人行通道 | fit | 简单/人行通道 | 5,429 |
| 桂花新村大桥 | fit | 病害多、建议多 | 10,881 |
| 梨子湾大桥 | fit | 病害多、建议多 | 10,236 |
| 凤中主线桥 | fit | 主线桥复杂样本 | 7,635 |
| 12-035 杨公桥立交 EC 匝道桥 | holdout | 长文本、病害多 | 10,558 |

当前 86 份 Baseline 中没有非空的 `previous_overall_score/grade` 或可用历史对比字段，因此没有虚构“有历史对比”样本；凤中主线桥作为复杂主线桥替代样本，并保留这一限制。

每个样本使用独立索引：公共部分为 1,211 条 fit Gold 标签范例 + 7 条专业知识卡，另加本样本 22–24 条当前报告证据。holdout 样本只检索 fit 标签，未使用 holdout Gold。

五次运行均满足：

```text
status = succeeded             5/5
retrieval.status = retrieved   5/5
D hits = 6                     5/5
来源配额 = 3/2/1               5/5
fallback = false               5/5
locked_fields_unchanged = true 5/5
引用审计有效                   5/5
```

B 组由现有单样本 runner 一并产出，但本轮评分决策只使用 A/C/D；B 不进入五样本比较。

### 2.1 本地确定性评分结果

使用仓库现有 scorer，对每个样本的完整预测与 Gold 对齐。`text_30` 为四个目标文本字段的 30 分权重子集，不是在线比赛最终成绩。

| 配置 | Macro total score | Macro text / 30 | 相对 A 的 text 增量 |
|---|---:|---:|---:|
| A 规则 Baseline | 31.980711 | 0.298329 | — |
| C 当前报告证据 | 37.362213 | 5.679831 | +5.381502 |
| D 完整 RAG | 39.056288 | 7.373906 | +7.075577 |

D 相对 C 的 Macro text 增量为 `+1.694075`，5/5 样本的 D text score 均高于 A，且 5/5 高于 C。D 的四项 Macro text 分数如下：

| 配置 | detailed_conclusion / 15 | causes / 5 | treatments / 5 | safety_impact / 5 |
|---|---:|---:|---:|---:|
| A | 0.086706 | 0.000000 | 0.000000 | 0.211623 |
| C | 2.731356 | 0.831558 | 1.348443 | 0.768474 |
| D | 2.987103 | 1.218836 | 2.599405 | 0.568562 |

D 在详细结论、成因、处置建议上高于 C；安全影响低于 C，不能宣称四项均提升。当前小样本结论是“D 有整体文本提升信号”，不是全量比赛得分保证。

## 3. 代码与审计修正

- 四类任务使用独立检索 Query。
- RAG 采用 `report_evidence=3`、`domain_knowledge=2`、`label_example=1` 的最小来源配额。
- Prompt 保留紧凑 Baseline、报告证据和 RAG 证据；K46 及五样本复杂报告的 D Prompt 均不超过 12,000 tokens，普通样本不超过 8,000 tokens。
- `_text_values()` 和证据递归遍历支持任意嵌套 Mapping/Sequence，并防止循环引用。
- 汇总审计同时接受报告事实的 `evidence_id` 与 RAG 条目的 `id`。这修正了知识卡合法引用被误报为无效的问题，不改变模型输出。
- 现有测试和编译检查已通过：

```text
python -m pytest -q tests/experiment/test_runner.py  -> 11 passed
python -m pytest -q tests/agent tests/experiment tests/rag -> 29 passed
python -m compileall -q inspection src scripts tests  -> passed
git diff --check -> passed
```

## 4. 交付产物

```text
runs/narrative-k46-20260805/real-run-p0-v4/
  baseline_prediction.json
  enhanced_prediction.json
  retrieval_trace.json
  ab_results.json
  experiment_summary.json

runs/narrative-k46-20260805/calibration-5/
  calibration_summary.json
  selection.json
  indexes/01..05/
  results/01..05/
```

当前最佳实验配置为 D，但只允许在这 5 个样本的校准范围内使用。尚未运行 92 份初赛测试集，也没有用 D 结果替换正式提交包。

## 5. 尚未解决的问题与合并判断

1. `detailed_conclusion` 仍没有公开合同中的逐段证据 ID；其 `has_new_facts` 旗标在 5/5 为 true，需要加实验 Sidecar 或人工复核后再批跑。
2. 外部 Embedding 接口单次调用约 30 秒，偶发连续超时；K46 首次重跑曾出现 `safety_impact` 检索失败，随后重试成功。批量运行前仍需保留失败/回退统计。
3. 五样本 D 的安全影响分数低于 C，说明完整 RAG 并非四项单调提升，需要继续优化提示或证据选择，但不应扩大为通用 Agent/RAG 平台。

本轮已证明架构和 D 组来源配额真实生效，并出现可重复的小样本整体提升信号；由于 `has_new_facts` 仍需逐段证据化，建议继续保持“先校准、后全量”的门禁，不把当前结果直接作为 92 份最终提交。
