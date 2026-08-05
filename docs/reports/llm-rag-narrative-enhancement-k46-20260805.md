# K46 Narrative Enhancement 校准报告（2026-08-05）

## 1. 范围

本报告只记录 Word-first 基线之上的 Narrative Enhancement 校准：独立任务检索 Query、RAG 来源配额、Prompt 事实压缩、证据/新增事实审计和锁定字段检查。

本线不修改 Gold、评分逻辑、确定性抽取器、提交打包器或生产 Word 模板。模板渲染另在 `feat/template-rendering` worktree 中维护，并通过独立提交合并。

样本：

```text
runs/p0-converted/2012年/上界路K46+500上跨车行桥.docx
sample_id = 2012年-上界路K46+500上跨车行桥
split = fit
```

## 2. 本轮修正

- `_text_values()` 对嵌套 Mapping/Sequence 增加循环保护；`_text_evidence_pairs()` 同样避免自引用递归。
- `detailed_conclusion`、`causes`、`treatments`、`safety_impact` 各自生成独立检索 Query。
- D 组使用简单来源配额：`report_evidence=3`、`domain_knowledge=2`、`label_example=1`。
- 配额选择不再局限于全局 embedding 前 30 条；当知识或标签样例低于全局排名时，从同源候选中补足配额。
- Prompt 只保留证据 ID、来源锚点、章节和截断后的事实正文；病害描述也限制长度。
- 检索轨迹保留原始 `kind`，并增加面向验收的 `source_bucket`，避免把内部别名误读成实际来源。
- D 组输出增加 `locked_top_level_differences` 和 `locked_fields_unchanged`。

## 3. 真实运行结果

实际成功运行产物：

```text
runs/narrative-k46-20260805/real-run-p0-v2/
```

| 组别 | Prompt Tokens | Completion Tokens | Total Tokens |
|---|---:|---:|---:|
| A | 0 | 0 | 0 |
| B | 1,570 | 834 | 2,404 |
| C | 3,644 | 1,151 | 4,795 |
| D | 13,878 | 1,166 | 15,044 |

旧运行的 C/D Prompt 分别为 10,432/21,311；本轮 C 降低约 65.1%，D 降低约 34.9%。

检索轨迹实际核验：

```text
4 个 task_queries，内容互不相同
D hits = 6
source_bucket = report_evidence × 3
source_bucket = domain_knowledge × 2
source_bucket = label_example × 1
raw kind = report_evidence × 3, knowledge_card × 2, gold_label × 1
```

D 组实际状态：

```text
available = true
used_fallback = false
evidence_id_valid = true
locked_fields_unchanged = true
locked_top_level_differences = []
has_new_facts = true
new_facts_count = 4
```

`has_new_facts=true` 是真实运行值，不再沿用旧报告的笼统 `false`。当前 4 条被标记内容主要是 D 组详细结论中的综合性改写；它们没有全部以原文词面或逐条证据 ID 形式出现，因此按当前确定性审计规则必须进入人工复核，不能写成“无新增事实”。

## 4. 验证

```text
python -m pytest -q
通过

python -m compileall -q inspection src scripts tests
通过
```

当前会话重新执行真实命令时没有 IAIC_API_BASE、IAIC_API_KEY、IAIC_CHAT_MODEL，因此脚本安全返回 `configuration_error`；本报告引用的是此前带真实配置生成的 `real-run-p0-v2` 成功产物，不把配置错误当作成功运行。

## 5. PR 范围说明

RAG/LLM 校准线的范围是：

- `src/rag/index.py`
- `src/agent/narrative.py`
- `scripts/run_narrative_enhancement.py`
- `tests/rag/`
- `tests/agent/`
- `tests/experiment/`
- 本报告

这条线只消费稳定的 Prediction Schema，并增强四个叙事字段。它不等于 Word-first 结构化抽取线，也不包含模板文件。

模板线单独位于：

```text
worktree: D:\研究生作业\竞赛研究\wt-template-rendering
branch: feat/template-rendering
commit: 012bdc9
```

模板线负责 `python-docx`/OOXML 模板、`template_fields.json`、SubmissionDocument 适配、批量模板渲染和 DOCX/DOC 样例。两条线最后才在 `main` 合并，不在同一 worktree 混写。

## 6. 冻结判断

本轮代码合同和真实运行证据可以暂时冻结；但 `has_new_facts=true` 仍是待人工复核信号，不应表述为 D 已经证明官方分数提升，也不应直接覆盖全量提交结果。
