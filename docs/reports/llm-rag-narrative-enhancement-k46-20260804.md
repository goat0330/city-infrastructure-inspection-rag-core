# LLM + RAG 专业文稿增强第一版报告

## 1. 报告信息

| 项目 | 内容 |
|---|---|
| 赛题 | 城市基础设施定检报告问答分析 |
| 当前方向 | Word-first 定检报告处理 |
| 实验样本 | 上界路 K46+500 上跨车行桥 |
| 样本 ID | `2012年-上界路K46+500上跨车行桥` |
| 实现基线 | `913122b` |
| 实验日期 | 2026-08-04 |
| 实验范围 | 单样本 A/B/C/D，不批跑 92 份测试集 |

本报告记录第一版 LLM + RAG 专业文稿增强骨架、真实运行结果和合同验收结果。它不代表官方平台最终分数，也不把单样本结果等同于全量提分结论。

## 2. 目标与边界

本次 Narrative Enhancement 的目标是在不改变已有 Word-first 确定性抽取主线的前提下，打通。这里的“保持不变”仅描述本次增强运行相对 Baseline 的约束，不是 PR #1 总体提交范围的声明；PR #1 的整体范围见第 10 节。

```text
Word 定检报告
  → 确定性 Baseline
  → 当前报告证据
  → 轻量 RAG
  → Qwen3.6-27B
  → Narrative Enhancement
  → 合同校验
  → enhanced_prediction.json
```

模型只能增强以下四个字段：

- `detailed_conclusion`
- `causes`
- `treatments`
- `safety_impact`

在本次 Narrative Enhancement 运行中，以下内容必须保持不变：

- 桥梁名称、日期和年份
- 评分和等级
- 病害列表
- 建议明细的既有结构
- 历史字段
- Gold、评分器和确定性抽取器

本轮没有引入 MinerU、PaddleOCR、PDF OCR 或通用问答系统；Word 仍然是当前 benchmark 的事实来源。

## 3. 当前架构

```text
确定性预测 Baseline
        │
        ├── 当前报告检测结论、安全评估、建议、病害段落和表格
        ├── fit 标签范例
        └── 专业知识卡
                │
                ▼
      JSONL + NumPy 轻量 RAG
                │
       Embedding Top 30
                │
       Reranker Top 8
                │
          最终 Top 6
                │
                ▼
       LangGraph Narrative 子图
                │
                ▼
          Qwen3.6-27B
                │
                ▼
      四类叙事字段与证据 ID
```

没有引入 Milvus、Qdrant、LlamaIndex、Haystack、LangChain 检索链、通用 Agent 平台或复杂模型 Registry。

### 3.1 模型客户端

文件：`src/llm/client.py`

使用官方 OpenAI Python SDK，提供：

- `chat_json()`
- `embed_texts()`
- `rerank()`
- 自定义 `base_url`
- 超时和最多一次短重试
- 模型名、耗时和 token 统计
- JSON 输出解析和截断错误诊断
- API Key 脱敏

模型通过以下环境变量配置，不写入代码、日志或 Git：

```text
IAIC_API_BASE
IAIC_API_KEY
IAIC_CHAT_MODEL
IAIC_EMBED_MODEL
IAIC_RERANK_MODEL
```

实际使用模型为：

```text
qwen3.6-27b
Qwen3-VL-Embedding-8B
Qwen3-VL-Reranker-8B
```

### 3.2 轻量 RAG

文件：`src/rag/index.py`

索引由 `metadata.jsonl` 和 `vectors.npy` 构成。真实实验索引统计如下：

| 类型 | 数量 |
|---|---:|
| 当前报告证据 | 455 |
| fit Gold 标签范例 | 1,211 |
| 专业知识卡 | 7 |
| 总计 | 1,673 |

向量矩阵形状为 `[1673, 4096]`。

索引构建和查询有两条保护：

1. holdout 查询只允许使用 fit 标签。
2. 当前样本的 Gold 标签被排除，但当前报告证据保留。

### 3.3 Narrative Enhancement Subgraph

文件：`src/agent/narrative.py`

图包含五个节点：

```text
prepare_context
      → retrieve_knowledge
      → generate_narrative
      → validate_output
      → finalize
```

如果第一次生成失败，带验证错误重试一次；再次失败则返回确定性 Baseline。

### 3.4 实验入口

文件：`scripts/run_narrative_enhancement.py`

实验脚本一次处理一个 DOCX，并生成：

- `baseline_prediction.json`
- `enhanced_prediction.json`
- `retrieval_trace.json`
- `ab_results.json`
- `experiment_summary.json`

## 4. 真实实验设置

输入文件为：

```text
runs/p0-converted/2012年/上界路K46+500上跨车行桥.docx
```

运行使用 `fit` 检索范围，报告证据先按章节和查询相关性压缩到有限上下文，再进入模型生成。真实 RAG 查询返回 6 条结果，全部为：

```text
kind = report_evidence
split = fit
```

没有命中当前样本 Gold。

## 5. A/B/C/D 真实运行结果

| 组别 | 配置 | 调用次数 | Prompt Tokens | Completion Tokens | Total Tokens | 耗时 |
|---|---|---:|---:|---:|---:|---:|
| A | 规则 Baseline | 0 | 0 | 0 | 0 | 0 |
| B | LLM，无 RAG | 1 | 1,570 | 930 | 2,500 | 13.58 秒 |
| C | LLM + 当前报告证据 | 1 | 10,432 | 1,249 | 11,681 | 25.59 秒 |
| D | LLM + 当前报告证据 + RAG + 相似标签范例 | 1 | 21,311 | 1,446 | 22,757 | 25.15 秒 |

全流程耗时约 72.91 秒，共 5 次模型调用：3 次 Chat、1 次 Embedding、1 次 Reranker。

Embedding 接口没有返回可累计的完整 token 用量，因此总汇总中的 `token_usage_known` 为 `false`；B/C/D 三次 Chat 的 token 用量均已记录。

### 5.1 A 组：规则 Baseline

| 字段 | 数量 |
|---|---:|
| 详细结论 | 4 |
| 病害成因 | 6 |
| 处置建议 | 7 |
| 安全影响 | 4 |

A 组作为确定性安全回退结果，内容完整但存在原文较长、重复较多的问题。

### 5.2 B 组：LLM，无 RAG

| 字段 | 数量 |
|---|---:|
| 详细结论 | 4 |
| 病害成因 | 4 |
| 处置建议 | 5 |
| 安全影响 | 4 |

B 组文本更短，但由于刻意不提供证据，模型生成了类似 `causes[0]`、`treatments[0]` 的伪证据引用。因此 B 组的 `evidence_id_valid=false`，只作为无 RAG 对照，不作为最终输出。

### 5.3 C 组：LLM + 当前报告证据

| 字段 | 数量 |
|---|---:|
| 详细结论 | 4 |
| 病害成因 | 4 |
| 处置建议 | 6 |
| 安全影响 | 4 |

C 组能够使用真实 DOCX 证据生成有效 `docx:*` 证据 ID，未触发 fallback，且没有检测到新增事实。

### 5.4 D 组：LLM + 证据 + RAG + 相似标签范例

| 字段 | 数量 |
|---|---:|
| 详细结论 | 4 |
| 病害成因 | 4 |
| 处置建议 | 7 |
| 安全影响 | 3 |

D 组结果：

```text
available = true
used_fallback = false
evidence_id_valid = true
has_new_facts = false
```

本次单样本实验中，D 是当前最佳配置。但这只是合同有效和叙事质量的单样本判断，不等同于已经证明官方评分提升。

## 6. D 组生成内容概括

### 6.1 详细结论

1. 全桥技术状况为 A 级，评分 94.99 分，安全性评估为合格等级，承载能力满足汽-超20、挂-120 荷载要求。
2. 桥面系为 B 级，评分 87.60 分，存在伸缩缝保护带破损、防撞栏杆锈蚀露筋、栏杆拆除、泥土堆积和泥沙覆盖。
3. 下部结构为 A 级，评分 93.00 分，桥台存在渗水、泛碱和局部开裂破损，主要影响结构耐久性。
4. 上部结构整体状况良好，应及时处理桥面及桥台病害，并加强超重车辆管理。

### 6.2 病害成因

- 伸缩缝保护带破损：长期车辆碾压、冲击。
- 桥台渗水泛碱：伸缩缝止水带局部缺失。
- 防撞栏杆锈蚀、破损、露筋：环境侵蚀及周边施工。
- 泥土堆积和泥沙覆盖：日常清理维护不及时。

### 6.3 处置建议

D 组生成 7 条建议，并与 Baseline 建议数量一致，分别对应：伸缩缝修复、栏杆修补、泥沙清理、桥台渗水泛碱处理、桥台开裂修复、超重车辆管理、日常检查和技术档案建立。

### 6.4 安全影响

D 组保留了以下安全关系：桥面病害影响行车舒适性并增大冲击；雨水下渗可能影响梁体耐久性；桥台渗水泛碱当前不显著影响承载力但影响耐久性；主体结构承载能力满足要求，安全评估为合格等级。

## 7. 合同验收

### 7.1 锁定字段

本次单样本 Narrative Enhancement 运行的增强前后非目标字段差异为：

```text
locked_top_level_differences = []
```

桥梁名称、评分、等级、病害列表、建议结构和其他 Baseline 字段均未被本次增强运行改动。该结论只针对本次增强前后输出，不代表 PR #1 的历史提交未修改相应源代码路径。

### 7.2 结构约束

- `detailed_conclusion` 不超过 4 段。
- D 组处置建议为 7 条，没有超过 Baseline 的 7 条。
- D 组有效证据 ID 数量为 9，非法数量为 0。
- D 组没有触发 fallback。
- 当前启发式检查没有发现新增事实。

“新增事实”检查是确定性文本和数值约束，不替代专业人员的最终语义审查。

## 8. 验证结果

执行结果：

```text
python -m pytest -q
161 passed

python -m compileall -q inspection src scripts tests
通过

python scripts/run_narrative_enhancement.py --help
通过
```

源码和跟踪路径未发现 API Key，Git 工作区在提交前保持干净。

## 9. 交付产物

真实运行产物位于本地未跟踪的 `runs/` 目录：

```text
runs/narrative-k46-20260804/real-run/baseline_prediction.json
runs/narrative-k46-20260804/real-run/enhanced_prediction.json
runs/narrative-k46-20260804/real-run/retrieval_trace.json
runs/narrative-k46-20260804/real-run/ab_results.json
runs/narrative-k46-20260804/real-run/experiment_summary.json
```

官方原始 DOC/DOCX、Gold 原文、API Key、绝对路径和本地运行产物不进入公开仓库。本报告只公开架构、计数、汇总结果和可复核的代码路径。

## 10. 提交与范围

### 10.1 本次 LLM + RAG 实现提交

本次 Narrative Enhancement 实现提交链为：

| 提交 | 内容 |
|---|---|
| `1d37d93` | official OpenAI model client |
| `ac37ee6` | lightweight JSONL NumPy RAG index |
| `0335588` | Qwen thinking budget fix |
| `5fe85ee` | LangGraph narrative enhancement subgraph |
| `913122b` | single-sample narrative enhancement experiment |

`913122b` 是本报告使用的实现基线。报告文件为：

```text
docs/reports/llm-rag-narrative-enhancement-k46-20260804.md
```

本次 LLM + RAG 实现文件包括：

- `src/llm/client.py`
- `src/rag/index.py`
- `src/agent/narrative.py`
- `scripts/build_rag_index.py`
- `scripts/run_narrative_enhancement.py`
- `prompts/narrative_enhancement.md`
- `tests/llm/`
- `tests/rag/`
- `tests/agent/`
- `tests/experiment/`

### 10.2 PR #1 总体范围（审计）

本次审计时 PR #1 仍为 Draft。PR #1 不仅包含本次 LLM + RAG 实现和本报告，还包含此前 Word-first/确定性基线检查点 `06a9dcf`。相对 PR base `main`，该既有提交对以下路径有实际改动：

- `src/extraction/pipeline.py`
- `src/extraction/recommendations/extractor.py`
- `src/extraction/summary/extractor.py`
- `src/extraction/text_sections.py`
- `src/inspection/cli.py`
- `src/submission/package.py`

该检查点还同步改动了对应的 extraction、inspection、submission 测试，以及 `README.md` 和 `docs/submission_design.md`。

因此，范围结论必须拆成两层：

- 本次 Narrative 增强（`1d37d93` 至 `913122b`，以及本次单样本运行）未改动上述确定性抽取、检查/评分和提交打包逻辑；它读取已有 Baseline，并只增强合同允许的叙事字段。
- PR #1 总体包含 `06a9dcf` 对上述路径的既有基础改动，不能表述为整个 PR 未修改 `src/extraction`、`src/inspection/cli.py` 或 `src/submission/package.py`。

## 11. 当前未完成事项

1. 尚未进行 92 份初赛测试集的 LLM 批量增强。
2. 尚未使用官方平台完成正式评分对比。
3. 尚未证明 D 组在官方指标上优于确定性 Baseline。
4. B 组无 RAG 时的证据 ID 无效，说明无证据对照只能用于 ablation，不能直接作为生产输出。
5. 需要在少量人工审查样本上继续确认模型是否引入专业语义偏差。
6. 当前仍是 Narrative Enhancement 子图，不是完整在线问答系统。

## 12. 下一步建议

建议按以下顺序推进：

```text
单样本人工核验
  → 3～5 个 fit 样本小规模评测
  → holdout 防泄漏评测
  → 对比字段级分数和错误类型
  → 确认提示词与 RAG 配置
  → 再考虑初赛测试集批处理
```

在官方评分验证前，不建议直接用 D 组批量覆盖全部提交结果。
