# 文件契约 v1

本文只描述 v1 兼容模式。需要自定义维度、细分情绪、行为意图/阶段或篇级汇总时使用 [v2 规则包与数据契约](rule-packs.md)，不要混用两种候选格式。

脚本使用 UTF-8 JSON / JSONL。JSONL 每行一个对象，禁止重复 ID、空数据集、未知字段及非有限数。保留原始文本，不修辞、不虚构上下文；本离线脚本不主动检索。首版单次最多 10,000 行、总计 5,000,000 字符；这是保护限制，不是已验证吞吐能力。默认每批 20 条、16,000 字符，超长单条报错而非截断。

## 项目 JSON

```json
{
  "project_id": "demo-brand",
  "rule_version": "0.1",
  "target_brand": "澄露",
  "aliases": ["澄露面霜"],
  "aspects": ["effect", "quality", "price", "service", "marketing", "packaging", "other"]
}
```

这些字段全部必填；aliases 可为空，aspects 不能为空。配置不包含任何凭证。首版固定六种总体标签，仅品牌、别名与方面可配置；任意标签体系尚未支持。

## 评论 JSONL

```json
{"id":"c001","text":"这款保湿很好","context":{"title":"澄露面霜使用体验","post_summary":"作者在分享澄露面霜的使用感受","parent_comment":""}}
```

id/text 必填非空字符串；context 可省略，接受 title、post_summary、post_text、parent_comment、author_profile、image_ocr、image_description 字符串；正文、摘要、图片转写和描述分开保存，后两者不冒充作者文字。所有行都保留，包括不同 ID 的相同文本。缺失上下文保留为空，不生成虚构内容。

## AI 候选 JSONL

每条只能有以下字段，全部必填：

```json
{"id":"c001","stance":"positive","targets":["product"],"aspects":[{"name":"effect","stance":"positive"}],"evidence":[{"source":"text","quote":"保湿很好"},{"source":"title","quote":"澄露面霜使用体验"}],"reason":"评论肯定保湿效果，标题明确产品归属。","confidence":"high","flags":[],"needs_review":false}
```

- stance：positive / negative / neutral / mixed / no_attitude / uncertain。
- targets：非空、不重复，从 brand/product/creator/advertisement/merchant/platform/competitor/other/none 选择；none 不与其他对象并存。
- aspects：数组，可以为空；每项 name 来自项目配置，stance 为 positive/negative/neutral。不同观点允许同一方面多个相反 stance，但不重复相同的 name/stance。
- evidence：非空数组，每项 source 为 text 或上述 context 类型，quote 为该字段的非空子串；至少一项来自 text。子串匹配不验证图片转写或摘要的真实性，使用派生内容须由 AI 保留相应复核问题。
- reason：不超过 400 字的简短依据，不是推理过程。
- confidence：high / medium / low，未校准自评信号，不能转写为百分比。
- flags：数组，取自 sarcasm/comparison/quoted_opinion/missing_context/mixed，不重复。
- needs_review：布尔值。脚本会追加复核条件，false 不能覆盖不确定标签、风险标志或非 high 置信。

`validate --predictions` 可接一个完整 JSONL，也可接只包含分批 JSONL 的目录。缺行、多行、非法标签、伪造原文会直接拒绝交付；不会静默丢弃。输出按原始输入顺序排列。

## 校验结果

`labels.jsonl` 每行包含 input（原始评论）、prediction、review_required、review_reasons 及 provenance（模型 ID、时间、输入/项目/Skill 摘要）。`review.jsonl` 是其中需要人工复核的完整记录。`summary.json` 给数量、分布和候选覆盖率，不包含“准确率”。

所谓候选是“通过结构校验且未触发默认复核条件的 AI 结果”，不是人工验收结果。原文子串检查也不是语义正确性验证。

## 独立参考标签与评测

人工评测文件每行 `{"id":"c001","stance":"positive"}`，需与结果 ID 完全一致。运行 `evaluate --gold gold.jsonl --labels checked/labels.jsonl`，只打印评测 JSON，不覆盖文件。指标只评总体 stance，不代表对象、方面、反讽识别都准确。

人工纠错记录建议格式：`id / previous_stance / confirmed_stance / evidence / correction_reason / rule_version / confirmed_by`。由用户确认后保存在私有位置；提取去敏的示例和规则修改建议，确认新版本后再用于后续批次。首版不自动改规则、不训练模型，不把最终测试集回填提示词。
