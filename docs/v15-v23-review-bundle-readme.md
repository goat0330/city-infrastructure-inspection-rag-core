# V15/V23 Review Bundle

这是当前提交诊断的离线复核包说明。GitHub 仓库只提交小体积的诊断文档和源码；运行结果、预测 JSONL、提交 tar.gz 等大文件放在同名 ZIP 中。

ZIP 目标文件：

`D:\Edge Downloads\city-infrastructure-v15-v23-review-bundle-20260811.zip`

ZIP 不包含 API Key、`.env`、缓存、lock、pyc、临时文件、原始 DOC 全集或 Gold 全集。

主要目录：

```text
analysis/       V15/V23 诊断报告与平台口径说明
v15/            V15 dispatch、预测、提交包和发布检查结果
v23/            V23 run-summary、prediction、Composer 检查和提交包
code/           当前主线中与抽取、Composer、语义合并直接相关的源码快照
oracle10/       人工简要信息校准包的说明和空白人工答案表
```

核验重点：

1. V15 是仓库可追溯的 48.63 平台最高基线；
2. V23 的 `gold_schema_mode=v18` 导致总体结论大范围改写；
3. V23 运行中存在 RAG fallback 和模型调用失败；
4. V15/V23 简要字段覆盖没有增加，因此简要正确率仍为 31%；
5. Oracle-10 人工答案尚未进入正式生产管线；
6. 下一轮应从 V15 结果做简要字段单变量 overlay。
