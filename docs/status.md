# 当前状态：Round2 v7 平台证据严格版

- 生产默认：确定性 Word-first，OfficialAnswerComposer 关闭。
- 文件名：只作缺失兜底和冲突提示，不覆盖报告明确值。
- 长文本：无当前报告证据不生成。
- RAG/LLM：仅保留为可选实验，不随本轮确定性正式链默认开启。
- 已验证：329 项测试，323 通过、6 项因 Review 包不含 live run 数据而跳过，0 失败；compileall 通过。
- 尚未验证：原始 92 份 DOCX 的全量新预测、平台新分数。
- 下一 Gate：重新预测 92 份并要求 `check_platform_consistency.py` 返回 `valid=true`，之后才渲染和打包。
