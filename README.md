# 城市基础设施定检报告：Word-first 核心仓库

本仓库服务于“城市基础设施定检报告问答分析”赛题。当前直接评测链路是：

```text
原始定检报告 → 结构化预测 → 固定 Word 信息提取报告 → tar.gz
```

在线 RAG、向量库和知识图谱属于后续展示和问答能力，不是当前提分主线。

## 当前基线

- 主线：`.doc → LibreOffice → .docx → python-docx/OOXML`。
- 94个标签、161份报告完成审计。
- 161/161报告转换成功且可用。
- Gold：86成功、8个明确失败，自评分100。
- 已具备：原生结构解析、章节路由、评分器、DOCX渲染、校验、Gate 0错题本。
- B2 已进入真实 Word 验证：病害、概要/评分、建议三个高权重抽取器和批量预测主链已接通。
- 最新本地验证：161/161份 DOCX 成功抽取，86/86份 Gold 可按来源报告对齐；内部确定性代理分为 36.22594（不是比赛平台最终分）。

详见：[当前状态](docs/status.md)｜[路线图](docs/roadmap.md)｜[范围边界](docs/current_scope.md)。

## 核心合同

- `schema/gold_record.schema.json`：标签/Gold数据合同，包含 split、provenance 和质量标记。
- `schema/prediction_record.schema.json`：运行时预测合同，不包含训练标签来源字段。
- `schema/inspection_record.schema.json`：早期兼容Schema，暂保留，不作为B2新代码入口。

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

仓库不包含官方原始 `.doc/.docx`、测试集、图片、账号、密钥、绝对路径或本地 `runs/` 产物。代码采用 MIT License；派生数据的来源说明优先于代码许可证，不能据此推断官方原始数据获得再分发许可。
