# cf11a39 → B2 foundation overlay

本补丁从公开仓库基线 `cf11a39` 出发，只包含可在无原始赛事数据条件下确定完成的修改：

- Gold 与 Prediction Schema 分离；
- `route`、`render`、`validate` 接入 CLI；
- 官方章节顺序 DOCX 渲染；
- 最终 `.doc` 文件集合的确定性 `tar.gz` 打包与包校验；
- `validate-package` CLI；
- Gold → DOCX → Gold 回环100测试；
- GitHub Actions；
- README、状态和路线图更新。

未实现 `predict`，因为病害、评分和建议抽取器尚未完成。本补丁不会生成伪预测，也未包含任何原始 Word、测试集、运行产物或本地路径。

覆盖后执行：

```bash
python -m pytest -q
python -m compileall inspection src scripts tests
python -m inspection --help
```

本地重建环境中新增/关联测试共42项通过；公开仓库现有完整测试集覆盖后应重新全量运行，以远端结果为最终门禁。
