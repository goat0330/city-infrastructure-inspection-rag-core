# 使用方式

## 1. 覆盖

在项目根目录解压本 ZIP，允许覆盖同名文件。本包不删除其他文件。

不要继续使用旧的：

- `prediction.jsonl`
- 已生成的 92 份 DOCX/DOC
- 旧 `submission.tar.gz`

## 2. 代码验证

```powershell
python -m pytest -q
python -m compileall -q src scripts
```

Review 包不携带 live semantic/calibration 运行目录，因此相应 6 项数据型测试会 skip；核心代码测试必须全部通过。

## 3. 从原始转换 DOCX 重新预测

```powershell
python -m inspection predict-batch `
  --input-dir runs/p0-converted `
  --output runs/round2-v7/prediction.jsonl `
  --report runs/round2-v7/prediction-report.json
```

本轮先不要加 `--semantic-live`，先得到严格证据确定性版本。

## 4. 必须通过平台一致性 Gate

```powershell
python scripts/check_platform_consistency.py `
  --input runs/round2-v7/prediction.jsonl `
  --output runs/round2-v7/platform-consistency.json
```

必须满足：

```text
valid = true
issue_record_count = 0
```

`filename_*_conflict` 是人工复核提示，不会自动覆盖报告事实。

若出现 `dominant_report_date_requires_source_check`，抽查原报告封面、签发日期和检测信息表；不要凭分布批量改日期。

## 5. 通过 Gate 后再渲染与打包

```powershell
python -m inspection render-batch `
  --input runs/round2-v7/prediction.jsonl `
  --manifest <官方manifest> `
  --output-dir runs/round2-v7/docx `
  --report runs/round2-v7/render-report.json

python -m inspection convert-doc `
  --input-dir runs/round2-v7/docx `
  --output-dir runs/round2-v7/doc `
  --manifest <官方manifest> `
  --report runs/round2-v7/convert-report.json

python -m inspection package `
  --input-dir runs/round2-v7/doc `
  --code-dir . `
  --design-dir design `
  --manifest <官方manifest> `
  --output runs/round2-v7/submission.tar.gz

python -m inspection validate-package `
  --input runs/round2-v7/submission.tar.gz `
  --manifest <官方manifest> `
  --output runs/round2-v7/package-validation.json
```

## 6. 上传前人工抽查

至少检查：

- 文件名等级与报告最终综合评定冲突的样本；
- 日期高度集中的样本；
- 人行天桥、人行通道、桥式通道；
- 总体结论、风险点、成因、安全影响是否均能在原报告找到依据；
- 建议摘要与建议表计数是否一致。
