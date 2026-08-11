# 赛道一方案设计说明

## 1. 任务目标

本方案面向城市基础设施定期检测报告的信息提取评测。系统将原始 Word 定检报告转换为结构化预测，再按赛事固定模板生成最终 Word 结果文件。当前主线以原生 Word 结构为事实来源，不把在线问答、RAG 或 OCR 作为提交链路的必要环节。

## 2. 处理链路

```text
原始 DOC/DOCX
→ Word/LibreOffice（按环境选择，必要时 DOC 转 DOCX）
→ python-docx + OOXML 结构读取
→ 章节路由
→ 概要/评分、病害、建议抽取
→ prediction.jsonl
→ 固定模板 DOCX 渲染
→ Word COM 优先转最终 DOC；无 Word 时 LibreOffice 逐份转换
→ code/design/result 官方提交包
```

## 3. 抽取与证据

- 概要/评分抽取桥梁名称、报告日期、评分和技术状况等级。
- 病害抽取识别病害部位、类型、描述、是否新增、上一次定检状态和发展程度，并处理跨页续表、重复表头及合并单元格。
- 建议抽取同时读取建议表和处置建议章节，保留建议内容、病害部位、类别和来源标记。
- 每个结构化字段尽量绑定原文来源锚点；无法从当前 Word 证据确认的字段不凭空生成。

## 4. 交付约束

最终压缩包遵循官方目录：

```text
submission.tar.gz
├── code/       # 可复现的项目代码与配置
├── design/     # 本方案及相关设计材料
└── result/     # 与测试清单一一对应的 .doc 结果文件
```

`result/` 只直接包含结果 `.doc` 文件。打包前后检查 gzip/tar 可读性、结果文件名、数量、扩展名、临时文件和目录层级，确保输入测试报告、预测记录、最终 DOC 和提交结果数量一致。

### 转换器运行约束

Windows 下 `convert-doc` 默认使用已安装的 Microsoft Word COM，以兼容大表格 DOCX；没有 Word 时才使用 LibreOffice。LibreOffice 路径逐份启动，临时目录使用公开 ASCII 名称，并等待输出文件稳定后再移动到 `final-doc/`，不再把整批报告一次性交给同一个进程。

## 5. 当前边界

当前交付优先保证 Word-first 主链稳定。MinerU、PaddleOCR、PDF/OCR、向量库、图数据库、通用 Agent 和在线 RAG 问答不作为本轮比赛提交包的必要运行时依赖；只有在 Word 主链无法覆盖明确样例时再单独评估。
