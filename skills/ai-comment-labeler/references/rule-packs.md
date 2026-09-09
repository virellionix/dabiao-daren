# v2 项目规则包与数据契约

## 规则包

通用执行器不内置客户情绪码。每个项目提供以下完整 JSON；品牌、别名和方面沿用 v1 字段名，目标可以是品牌及明确归属产品。

```json
{
  "schema_version": 2,
  "project_id": "fictional-device",
  "rule_version": "1.0",
  "target_brand": "星舟",
  "aliases": ["星舟耳机"],
  "aspects": ["quality", "service", "price"],
  "unit": "segment",
  "dimensions": {
    "emotion": {
      "title": "主要情绪",
      "mode": "single",
      "labels": {
        "worried": {"name": "担忧", "definition": "作者明确担忧产品使用风险；不据此推断品牌态度。"},
        "relieved": {"name": "释然", "definition": "作者明确表达担忧减轻或解除。"},
        "unknown": {"name": "无法判断", "definition": "没有足够证据判断上述情绪。"}
      },
      "unknown": "unknown",
      "aggregate": "final",
      "review_labels": ["unknown"]
    }
  },
  "actions": {
    "return_request": {"name": "申请退货", "definition": "提出退货申请；不等于已收到退款。"}
  },
  "denominator": null,
  "rules": ["连续状态实质改变才拆段；情绪不能代替品牌态度。"]
}
```

- 所示顶层字段全部必填，无凭证字段。维度 1–24 个，每维最多 256 个带定义的标签；项目码可自定义，无需改 Python。rules 保存边界、主次口径、正反例等判断规则。
- unit：item 每篇一条；segment 每篇多个连续状态段。单条评论也使用 document_id，其值可与 id 相同。
- mode：single 恰好一个；multi 可零个或多个。零个表示没有该维证据，不等于明确无法判断；需要表达后者时选本维 unknown。
- unknown 必须是已配置标签，不能与具体标签同条共存。review_labels 指定本维必须复核的标签。
- aggregate：final 取末段作为篇级最终状态；union 取全篇出现集合。每维另保留 ever_seen 辅助集合。single+union 的篇级结果可多值；单选约束作用在段级。
- denominator：null 表示全部篇；或 `{"dimension":"validity","include":["valid"]}`，该维须 single+final。实际分母按本批重算，不复制示例的固定篇数。
- actions 可为空。有行为需求时配置每种行动的定义；“退货诉求”和“退款到账”应分开。自责、无力等表达可作为细分标签，不能自动当已执行行为。
- 顶层可选 `require_ai_review: true`：交付前必须提交完整的逐字段 AI 复核记录。旧项目省略时保持兼容。
- 标签/动作可选 `validation_status`：unverified 表示尚缺有效试标，命中时必复核；trial_passed 只表示在指定范围完成试标，不是人工真值或业务准确率。省略保留旧行为。无样本类别保留 unverified，不因清空 review_labels 或模型自称 high 而放行。

更换分类时，保留原项目与原始标签，创建版本化规则包和旧码映射。拆为多个新标签的旧码必须重新看原文，不一键转换历史结果。新维度的分母、final/union 仍按项目定义；不可把心理表达计作实际行为。

## 输入 JSONL

```json
{"id":"n1-s1","document_id":"n1","segment_index":1,"is_last_segment":true,"source_kind":"full_text","text":"星舟耳机有杂音，准备明天申请退货。","context":{"title":"耳机使用体验"}}
```

全部所示字段除 context 外必填。ID 唯一；同篇 segment_index 从 1 连续、唯一，且只最后一段 is_last_segment=true。context 只接受 title/post_summary/parent_comment；不填不存在的内容。source_kind 是 full_text 或 excerpt，摘录必送复核。脚本检查序号，不验证是否漏拆或截掉了正文，AI/人工负责覆盖检查。

容量：输入最多 10,000 行、总计 5,000,000 字符、单条 text 12,000 字符。默认每批 20 条、16,000 字符；v2 批次预算不包含单独加载的规则包，不能据此承诺 token 用量。超限明确失败，不截断；预测 JSONL 同样有 50,000 字符单行和 5,000,000 字符总量限制，大规则包需分次运行并分别报告覆盖。

## 候选 JSONL

```json
{"id":"n1-s1","targets":["product"],"aspects":[{"name":"quality","stance":"negative"}],"labels":{"emotion":[{"value":"unknown","evidence":[]}]},"behaviors":[{"action":"return_request","actor":"self","stage":"planned","time_scope":"unspecified","evidence":[{"source":"text","quote":"准备明天申请退货"}]}],"evidence":[{"source":"text","quote":"有杂音"}],"reason":"质量不满与退货计划明确，不能仅据此推断担忧情绪。","confidence":"high","flags":[],"needs_review":false}
```

示例中的基础字段全部必填，另可包含下文定义的 ai_review；拒绝其他未定义字段。没有总体 stance。需要总体态度时在 dimensions 自行定义 stance 维，不强制沿用六类。

- labels 必须覆盖所有配置维度，值均为数组。每个具体值单独提供 evidence；至少一段来自本段 text。unknown 可以无证据。每段原文不足时不能沿用前段态度填空。
- 每项证据是 `{source,quote}`：source 为 text/title/post_summary/parent_comment，quote 必须是指定来源的非空原文子串。全局 evidence 必须非空且包含 text，不能替代具体标签的证据。
- targets 非空，从 brand/product/creator/advertisement/merchant/platform/competitor/other/none 选择；none 不与其他类型共存。aspects 只记目标范围内观点，每项 name 来自项目、stance 为 positive/negative/neutral，可空。正文只评价博主就不能自动填 brand。
- behaviors 为数组，每项 action 来自项目；actor 为 self/other/unspecified，stage 为 done/ongoing/planned/considering/suggested/hypothetical/unknown，time_scope 为 before_event/after_event/unspecified。没有事件时间线的项目使用 unspecified，不推断因果。
- “建议别人去退货”是 other+suggested；“别人已经退了”是 other+done；“我明天申请”是 self+planned；“已经申请但未退款”对申请动作可 done，但不代表退款完成。以动作定义为单位判断阶段。
- reason 不超过 400 字；confidence 为 high/medium/low；flags 为 sarcasm/comparison/quoted_opinion/missing_context/mixed 的数组；needs_review 为布尔值。

可选 `ai_review`（项目 require_ai_review=true 时必填）：

```json
{"checked_fields":["targets","aspects","labels.emotion","behaviors"],"issues":[{"field":"labels.emotion","detail":"只说检测正常，缺少作者表达释然的原文。"}]}
```

checked_fields 必须包含 targets、aspects、behaviors 以及每个 `labels.维度名`，不多不少、不重复。issues 只保留未解决项，可空；每项 field 可指以上字段、实际存在的 `behaviors[序号]`（从 0 起）、source 或 record，detail 不超过 400 字。问题即使与 needs_review=false 并存也强制送复核。记录完整不证明 AI 真正理解正确，仍需抽查和独立参考。

## 汇总与边界

labels.jsonl 保留所有输入、候选、复核原因与模型/规则/输入摘要；review.jsonl 为需要复核的子集。

v2 另在每条结果中输出 review_details（具体字段、原因与说明）及 field_status（candidate/review），summary 输出 review_field_counts。全局置信度/语境风险影响整条；某个标签或动作的问题只定位该字段。source 类复核表示全文覆盖未验，其他字段的 candidate 仅限所给原文，绝非全文验收。candidate 始终不是 confirmed。

documents.jsonl 保留篇 ID、所有段 ID、末段 ID、最终/合并标签、ever_seen、行为及其来源段。同行动、同行为人、同时间范围取最强阶段：done > ongoing > planned > considering > suggested > hypothetical > unknown。时间不同的行为不互相吞掉，例如事件前做过一次、事件后又计划一次，两者都保留。

summary.json 分开报告段数、篇数、实际分母、排除篇数和复核篇数。每维 counts 按篇去重；rates 的分母只取纳入篇数，多选维的百分比之和可超过 100%。行为实际发生仅计 self+done/ongoing；意向计 self+planned/considering；after_event_actual 还要求明确事件后。不同时间范围同篇可同时进入实际和意向，两项不能相加当人数。

这些都是候选分布，含尚待人工复核的结果；不是人工确认统计，不证明语义准确率。v2 暂无内置逐维人工参考评测器，v1 evaluate 会拒绝 v2，不能偷换标签口径。
