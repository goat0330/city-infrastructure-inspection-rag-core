# P1 全量章节路由审计基线

审计输入为当前本地 `runs/p0-converted` 中的 161 份转换 DOCX。审计只记录结构统计、类别、来源锚点和质量标记，不发布报告正文或原始文件。

## 总体结果

| 指标 | 数值 |
| --- | ---: |
| DOCX 数量 | 161 |
| 解析成功 | 161 |
| 解析失败 | 0 |
| 核心路由同时命中（病害表 + 建议章节） | 55 |
| 重复来源命中报告 | 0 |
| 路由块存在结构重叠的报告 | 148 |

## 类别覆盖

| 类别 | 命中报告数 | 覆盖率 |
| --- | ---: | ---: |
| `scoring` | 76 | 47.2% |
| `defect_table` | 132 | 82.0% |
| `recommendations` | 71 | 44.1% |
| `inspection_conclusion` | 148 | 91.9% |
| `safety_assessment` | 137 | 85.1% |
| `treatment_recommendations` | 146 | 90.7% |

这里的 `recommendations` 与 `treatment_recommendations` 是两个独立路由类别；建议抽取器必须同时消费两者，不能把 `recommendations` 的 44.1% 当作建议信息总覆盖率。

## Gold 交集检查

将 86 条 Gold 的 `source_report_relative_path` 规范化为对应 `.docx` 后，全部找到路由记录：

| 指标 | 数值 |
| --- | ---: |
| Gold 报告匹配 | 86/86 |
| Gold 报告命中 `defect_table` | 74/86 |
| Gold 报告命中 `recommendations` | 17/86 |
| Gold 报告同时命中两个核心类别 | 17/86 |

Gold 中 86 份报告均有病害记录（共 2,761 行）和建议记录（共 503 行），因此路由缺失必须输出质量标记并进入后续抽取器的结构化回退路径，不得静默判定为空。

## B2 约束

- 病害抽取器需要对 12 份 Gold 报告的缺失 `defect_table` 做表头/表格结构回退，并保留来源锚点。
- 建议抽取器需要同时扫描建议表、处理/处置/处治建议章节和跨段落文本；不能只依赖 `recommendations` 类别。
- 148 份存在路由块重叠的报告只作为候选重叠记录，不等同于同一表格重复命中；同一来源重复命中本轮为 0。
- 规则必须基于结构、表头和章节证据，不得写入具体桥梁名称或文件名特判。

审计脚本：`scripts/audit_routes.py`。完整逐报告 JSON/Markdown 仅保留在本地 `runs/`，不进入 Git。
