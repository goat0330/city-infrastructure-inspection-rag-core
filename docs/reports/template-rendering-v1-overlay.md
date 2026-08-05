# 城市基础设施定检报告标准模板渲染覆盖包

适用仓库：`goat0330/city-infrastructure-inspection-rag-core`

基准提交：`9fbd122`

本包直接解决当前空白 `Document()` 渲染导致的字体、列宽、分页、序号和模板结构不一致问题。它不修改概要、病害、建议抽取器，也不接入 LLM/RAG。

## 已完成

- 生产模板改为真正的动态原型模板；
- 概要表使用三列固定结构；
- 建议表仅保留“表头 + 1 行原型”；
- 病害表仅保留“表头 + 1 行原型”；
- 病害成因、处置建议、安全影响各保留 1 个原型段落；
- 所有占位符与 `template_fields.json` 完全一致；
- 新增 `SubmissionDocument` 适配层；
- 新增模板渲染器；
- 支持动态建议行、动态病害行、连续序号、病害序号纵向合并；
- 保留模板固定列宽、重复表头、禁止行跨页断裂和“（3）病害列表”自动编号；
- 渲染后检查残留占位符；
- 新增单份和批量模板渲染脚本；
- 已生成上界路 K46+500 样例 DOCX 和 DOC。

## 覆盖方式

将压缩包解压到仓库根目录并覆盖同名文件。

主要新增或替换文件：

```text
assets/templates/
├── information_extraction_v1.docx
├── information_extraction_v1.sample.docx
├── template_fields.json
└── 模板字段与渲染规则说明.md

src/rendering/
├── __init__.py
├── style_spec.py
├── submission_document.py
└── template_renderer.py

src/submission/
├── __init__.py
└── template_batch.py

scripts/
├── render_template_sample.py
└── render_template_batch.py

tests/rendering/
└── test_template_renderer.py
```

原来的 `src/rendering/docx_renderer.py` 保留不动，继续作为 legacy/fallback。

## 单份渲染

```powershell
python scripts/render_template_sample.py `
  "runs\prediction.json" `
  "runs\template-v1\result.docx"
```

## 批量渲染

```powershell
python scripts/render_template_batch.py `
  --predictions "runs\predictions.jsonl" `
  --manifest "runs\test-manifest.csv" `
  --output-dir "runs\template-v1\docx"
```

批量渲染完成后，继续使用仓库已有的 DOCX → DOC、打包和 `validate-package` 流程。

## 验收命令

```powershell
python -m pytest -q tests/rendering/test_template_renderer.py
python -m compileall src/rendering src/submission scripts
```

本包内测试结果：

```text
4 passed
compileall passed
```

## 样例

```text
examples/上界路K46+500_submission_document.json
examples/上界路K46+500_模板渲染验收.docx
examples/上界路K46+500_模板渲染验收.doc
```

该样例已验证：

- 3 张表结构正确；
- 7 条建议动态生成；
- 9 条病害动态生成；
- 4 段详细结论存在；
- 成因、处置建议和安全影响动态生成；
- DOCX 可打开；
- LibreOffice 转 DOC 成功；
- 无残留 `{{...}}` 占位符。

## 后续接入 LLM/RAG

LLM/RAG 只需修改或生成：

- `history_and_defects`
- `current_structure_state`
- `comprehensive_judgement`
- `causes[]`
- `treatments[]`
- `safety_impacts[]`

模板和渲染器无需再次修改。桥梁名称、日期、评分、建议明细和病害列表仍由现有规则 Pipeline 控制。
