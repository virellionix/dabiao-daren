# 打标达人开发边界

- 本仓库是独立的 AI 评论打标 Skill，不修改邻近跑数平台或转办达人。
- 主入口 `skills/ai-comment-labeler/SKILL.md`；v1 默认口径见 `references/codebook.md`，v2 多维标签以用户确认的项目规则包为准，格式见 `references/rule-packs.md`。保持配置、契约、脚本和测试一致。
- 源码与示例可以提交。真实评论、用户标注、凭证和运行结果放 `.runs/` 或其他忽略目录，不上传 GitHub。提交前检查暂存文件。
- 用 `python3 -m unittest discover -s tests -v` 做本地测试；`.github/workflows/ci.yml` 只运行标准库测试、离线演示和发布安全扫描，不调用收费 API，也不改变宿主模型设置。
- 校验原文子串不等于验证语义。模拟测试不等于人工标注业务集的准确率。未知数据不能填成中性，不按文本盲目去重。
