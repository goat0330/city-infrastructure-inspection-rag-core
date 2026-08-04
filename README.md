# 城市基础设施定检报告：Word-first 核心仓库

本仓库服务于“城市基础设施定检报告问答分析”赛题。当前直接评测链路是：

```text
原始定检报告 → DOCX → Word 结构化预测 → 固定 Word 信息提取报告 → tar.gz
```

在线 RAG、向量库和知识图谱属于后续展示和问答能力，不是当前 benchmark 的输入或提分主线。

## 交付边界与当前基线

Word-first 是硬边界：`.doc` 先经 LibreOffice 转换为 `.docx`，再以 `python-docx/OOXML` 读取段落、标题、表格和合并单元格。Word 结构是当前预测链的事实来源；PDF/OCR/RAG 不会在本 benchmark 路径中替代 Word 输入。

当前主线基线为 `0000961`：

- 94 个标签、161 份报告完成审计；161/161 份 DOCX 转换成功且可用。
- `predict-batch` 对 161 份 DOCX 成功产出 161 条预测；评测 manifest 通过 `source_docx` 自动对齐到 86 条 Gold 记录。
- 已具备原生结构解析、章节路由、评分器、DOCX 渲染、校验、Gate 0 错题本，以及概要/评分、病害、建议三个高权重抽取器。
- 当前 Round A 内部 scorer 结果为：全量 `44.82606`、fit `40.593175`、holdout `46.820635`。这些数值用于同一 manifest、权重和 scorer 下的回归比较，不等同于官方平台最终分数。

当前仍有明确的后续提分项：`recommendations` 的误召回及部位/内容边界，以及嵌套在长文本中的病害识别与拆分。

详见：[当前状态](docs/status.md)｜[路线图](docs/roadmap.md)｜[范围边界](docs/current_scope.md)。

## 核心合同

- `schema/gold_record.schema.json`：标签/Gold数据合同，包含 split、provenance 和质量标记。
- `schema/prediction_record.schema.json`：运行时预测合同，不包含训练标签来源字段。
- `schema/inspection_record.schema.json`：早期兼容Schema，暂保留，不作为B2新代码入口。

## 可复制的 Word-first 与 benchmark 路径

以下命令均从仓库根目录执行，路径全部是仓库相对路径。公开仓库不携带官方原始报告和 Gold；在本地数据已按这些相对路径准备好后执行即可。

先完成 `.doc → .docx`（若 `runs/p0-converted` 已由受控转换步骤生成，可直接从批量预测开始）：

```bash
python -m inspection convert --input-dir runs/source-doc --output-dir runs/p0-converted --state-path runs/p0-convert.state.json
```

对全部转换后的 DOCX 运行预测：

```bash
python -m inspection predict-batch --input-dir runs/p0-converted --output runs/b2-night/round-a-merged/raw-predictions.jsonl --report runs/b2-night/round-a-merged/prediction-report.json
```

也可以使用一键入口完成预测、对齐和 benchmark：

```bash
python scripts/run_b2_word_pipeline.py --input-dir runs/p0-converted --gold runs/p0-gold/gold.json --manifest runs/b2-night/eval-manifest.json --output-dir runs/b2-night/round-a-merged --commit 0000961 --config round-a-word-first
```

批量报告必须显示 `input_count=161`、`prediction_count=161`、`failed_count=0`。随后使用 manifest 的 `source_docx` 作为唯一对齐依据运行 benchmark；不要直接把 161 条原始预测按文件顺序截取为 86 条：

```bash
python scripts/run_b2_benchmark.py --gold runs/p0-gold/gold.json --predictions runs/b2-night/round-a-merged/raw-predictions.jsonl --manifest runs/b2-night/eval-manifest.json --weights data/core/score_weights.json --output-dir runs/b2-night/round-a-merged/benchmark --commit 0000961 --config round-a-word-first
```

该命令默认执行验证，不要使用 `--skip-verify` 作为交付门禁。成功条件是进程返回码为 `0` 且输出 `verify : OK`。

真实门禁至少检查：

- `alignment.json`：`mode=manifest-source-docx`，`manifest_count=86`，`input_prediction_count=161`，`aligned_prediction_count=86`，`excluded_prediction_count=75`；缺失或歧义匹配必须使命令失败。
- manifest 的 86 条记录按 `fit=75`、`holdout=11` 分组；拆分汇总只从 manifest 读取，不按原始预测文件顺序猜测。
- `score.json`：`record_count=86` 且 `missing_sample_ids`、`extra_sample_ids` 均为空；`total_score` 是当前内部 scorer 的全量结果来源。
- `diagnostics.json`、`summaries.json`、`errorbook.md` 和 `leaderboard.csv`：分别保存汇总诊断、fit/holdout/stress 视图、无 Gold 原文的错题摘要和可排序实验记录。

benchmark 还会写出 `aligned-predictions.jsonl` 作为 manifest 顺序下的评测视图。原始预测不被原地改写，manifest 对齐只在输出评测视图中重写 `sample_id`。

## CLI

```bash
python -m inspection audit --labels-dir ... --reports-dir ... --output-dir ...
python -m inspection build-gold --labels-dir ... --reports-dir ... --output-dir ...
python -m inspection convert --input-dir ... --output-dir ... --state-path ...
python -m inspection parse --input report.docx --output parsed.json
python -m inspection route --input report.docx --output routes.json
python -m inspection predict --input report.docx --output prediction.json
python -m inspection predict-batch --input-dir converted-docx --output predictions.jsonl --report batch-report.json
python -m inspection score --gold gold.json --predictions predictions.json
python -m inspection render --input prediction.json --output result.docx
python -m inspection validate --input result.docx --output validation.json
python -m inspection package --input-dir final-doc --output submission.tar.gz --manifest expected.csv
python -m inspection validate-package --input submission.tar.gz --manifest expected.csv
```

`predict` 和 `predict-batch` 使用 Word 结构、章节路由和确定性规则生成 `prediction-v1`。单文件失败会显式写入 sidecar 报告；批量模式不会用伪记录掩盖失败。当前 `causes`、`treatments`、`safety_impact` 仍保持显式未实现并记录质量标记。

## 最终提交包约束

`package` 默认要求输入目录根部只包含 `.doc` 文件，并生成确定性的 `tar.gz`。可选 manifest 用于严格检查测试集输出文件名和数量。它校验压缩格式、根目录结构、重复/临时文件、扩展名、缺失和多余文件；不尝试解析旧版二进制 `.doc` 内容。

## 公开边界

仓库不包含官方原始 `.doc/.docx`、测试集、图片、账号、密钥、绝对路径或本地 `runs/` 产物。公开文档只记录仓库相对路径、计数和汇总分，不写入 Gold 原文或隐私路径。代码采用 MIT License；派生数据的来源说明优先于代码许可证，不能据此推断官方原始数据获得再分发许可。
