# 当前状态（B2 Word-first 工作基线，commit 33dc366）

## 已完成

- Word-first 范围冻结：`.doc → LibreOffice → .docx → OOXML/python-docx`；Word 结构是 benchmark 的事实来源，PDF/OCR/RAG 不替代该输入边界。
- 94 个标签、161 份报告完成全量审计。
- 报告转换：161/161 成功，全部 `target_is_usable=true`。
- 标签解析：86 成功、8 个明确失败；Gold 自评分 100。
- 原生 Word 结构模型：段落、标题、表格、合并单元格、图片关系、证据锚点。
- 六类章节路由：评分、病害表、建议、检测结论、安全评估、处置建议。
- P1 全量章节路由审计：161/161 DOCX 解析成功；Gold 交集 86/86；历史审计基线见 `archive/legacy-20260806/docs/gate0/route-audit-baseline.md`。
- 本地100分加权评分器及 Gate 0 聚合错题本。
- 最小 DOCX 渲染器和单文档结构校验器。
- 本轮补齐：Gold/Prediction Schema 分离、route/render/validate CLI、tar.gz 打包与包结构校验。
- B2 高权重抽取主链：概要/评分、病害、建议三个确定性抽取器已接入 `predict` / `predict-batch`；生产默认使用报告证据，`OfficialAnswerComposer` 仅用于显式 A/B 实验。
- 平台一致性修复：文件名中的“原X级/现X级”恢复历史等级、当前等级和趋势；建议类别在 Prediction 层统一并与渲染表格重算摘要；无证据时不生成通用风险、成因和安全影响模板。
- RAG + LLM live 路径已接入 `predict` / `predict-batch` 的显式 `--semantic-live` 模式，使用 fit-only `LightRagIndex` 的 Embedding → Reranker 检索和 Qwen narrative 增强详细结论、成因、安全影响，锁定结构化事实不被模型改写。
- 病害抽取支持病害表识别、跨页/重复表头、合并单元格继承、多位置观察和 Gold 模板默认状态值，并保留质量标记。
- 建议抽取支持表格与处理建议章节双路证据；类别推断仅在批量预测主链显式开启，并记录 `recommendation_category_inferred`。
- B2 真实 Word 验证已完成：`predict-batch` 对 161/161 份 DOCX 成功，manifest 的 `source_docx` 将预测自动对齐到 86/86 份 Gold。

## 尚未完成

- 8 个 Gold 失败样例与 4 个模糊配对的人工映射仍需单独处理。
- 默认模式保留 Word 源证据；live 模式仅对详细结论、成因和安全影响启用 Embedding → Reranker → Qwen 证据约束生成，处置字段继续使用确定性建议。
- DOCX → 最终 `.doc` 导出、测试集最终交付包和包级全量验收仍待 B3 完成。
- 跨年度病害对齐和证据约束长文本生成。

## Round A 真实验证结果

以下结果来自本地保留的训练/验证数据运行，不代表比赛平台最终成绩；原始数据、Gold 原文和 `runs/` 产物不进入公开仓库。fit/holdout 是同一 manifest、权重和内部 scorer 下的拆分结果，不能与官方最终分数混用。

| split | 内部 scorer |
|---|---:|
| 全量 | 44.82606 |
| fit | 40.593175 |
| holdout | 46.820635 |

### 真实 benchmark 门禁

从仓库根目录执行以下命令；本地数据准备后，所有参数均为仓库相对路径：

```bash
# 一键入口：predict-batch -> source_docx 对齐 -> benchmark
python scripts/run_b2_word_pipeline.py --input-dir runs/p0-converted --gold runs/p0-gold/gold.json --manifest runs/b2-night/eval-manifest.json --output-dir runs/b2-night/round-a-merged --commit 0000961 --config round-a-word-first

# 等价的分步命令
python -m inspection predict-batch --input-dir runs/p0-converted --output runs/b2-night/round-a-merged/raw-predictions.jsonl --report runs/b2-night/round-a-merged/prediction-report.json
python scripts/run_b2_benchmark.py --gold runs/p0-gold/gold.json --predictions runs/b2-night/round-a-merged/raw-predictions.jsonl --manifest runs/b2-night/eval-manifest.json --weights data/core/score_weights.json --output-dir runs/b2-night/round-a-merged/benchmark --commit 0000961 --config round-a-word-first
```

第一条命令的 sidecar 报告必须为 `input_count=161`、`prediction_count=161`、`failed_count=0`。第二条命令必须保留默认验证并以返回码 `0`、`verify : OK` 收尾。

manifest 的 86 条记录按 `fit=75`、`holdout=11` 分组；`benchmark/` 的验收产物为：

- `alignment.json`：`mode=manifest-source-docx`，86 条 manifest 记录从 161 条原始预测中选出并排序为 86 条评测记录，排除 75 条非 Gold 交集预测；缺失或歧义会 fail closed。
- `aligned-predictions.jsonl`：manifest 顺序下的评测视图；原始 `raw-predictions.jsonl` 不被原地改写。
- `score.json`：`score_dataset` 的全量分数来源，必须有 86 条记录且无 missing/extra sample。
- `diagnostics.json`、`summaries.json`、`errorbook.md`、`leaderboard.csv`：汇总诊断、fit/holdout/stress 视图、无 Gold 原文的错题摘要和实验记录。

当前后续提分焦点为：`recommendations` 的误召回、建议部位与内容边界，以及嵌套在长文本中的病害识别和拆分；8 个 Gold 失败样例仍作为独立挑战集处理。

## 数据现状

| 指标 | 数值 |
|---|---:|
| 标签 | 94 |
| 报告 | 161 |
| 精确配对 | 90 |
| 模糊配对 | 4 |
| Gold成功 | 86 |
| Gold失败 | 8 |
| 未唯一归属报告 | 67 |
| 报告转换可用 | 161 |
| predict-batch 输入/成功/失败 | 161/161/0 |
| manifest `source_docx` / 对齐评测记录 | 86/86 |

8个失败标签和67个未唯一归属报告不应静默忽略，但也不阻塞 B2：前者作为 Gold 解析挑战集，后者主要用于不崩溃、路由覆盖率和输出完整性测试。
