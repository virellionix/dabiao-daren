# v1 默认品牌态度规则 v0.1

仅用于未声明 schema_version 的 v1 项目。多维 v2 项目使用自己的标签定义，见 [规则包契约](rule-packs.md)，不强制套用下方六类总体态度。对象归属、作者与引用者区分、证据检查仍然适用。

## 评价范围

评价单元是“当前评论作者对项目目标品牌及已确认归属产品的态度”。标题和父评论只用于消歧，不能把其他人的观点当成当前作者的观点。其他品牌不并入目标品牌。产品体验可计入目标品牌范围，但记录 `product` 对象及实际方面，不能声称评论直接评价了整个品牌。

项目 `aliases` 必须由用户确认，不能根据名字相似推断产品归属；没有出现品牌词时，只有已给上下文足以确认指代才能归因。产品与品牌不属同一目标或归属不明时，不强行聚合。

## 六种互斥的总体标签

| 标签 | 含义与纳入范围 | 排除与边界 |
|---|---|---|
| positive / 正面 | 当前作者明确认可目标品牌或其产品、营销、服务等 | 询问购买渠道、提到品牌、点赞博主不直接算正面 |
| negative / 负面 | 当前作者明确批评或反对目标范围内的表现 | 只骂博主/平台、引用别人差评但未表态不算品牌负面 |
| neutral / 中性陈述 | 针对目标品牌的客观陈述，未带褒贬 | 没有目标态度的提问与无关讨论归 no_attitude；拿不准不是中性 |
| mixed / 褒贬混合 | 对目标范围存在实质正反评价，不能有依据地消除其中一侧 | 负面对象是博主、正面对象是产品时不跨对象凑混合；前面引用观点被作者明确否定也不算混合 |
| no_attitude / 无品牌态度 | 可以确认作者未表达目标品牌态度，例如询问、仅评价博主、未认同的转述 | 若缺上下文导致无法判断是否有态度，归 uncertain |
| uncertain / 无法判断 | 无法可靠判定对象、作者立场或反讽含义，关键上下文缺失 | 不替代清楚的混合态度或清楚的无品牌态度 |

以上是首版项目口径，不宣称所有客户都使用同一套中性定义。项目希望把“询问”并入中性时，先讨论修改契约与评测，不能运行中暗改。

## 分对象、分方面

每条 `targets` 可以记录 brand、product、creator、advertisement、merchant、platform、competitor、other、none 多个类型；必须体现实际对象，不能仅因项目指定了品牌就自动填 brand。

`aspects` 只记录目标范围内的方面观点，可多选。每项为 `name`（来自项目 aspects）、`stance`（positive/negative/neutral）。比如“澄露面霜保湿不错，就是贵”是 product，总体 mixed，effect positive、price negative。仅有一句无方面的总体认可可留空，不硬补功效。

默认方面可配置为 effect、quality、price、service、marketing、packaging、other。换项目允许调整方面名称，但不自动发明未配置标签。

## 边界示例（全部虚构，仅作开发用）

- “这博主就会接广告” → no_attitude，creator。不能把对博主的不满转嫁给品牌。
- “澄露找这种博主做广告，营销真让人反感” → negative，brand/advertisement，marketing negative。
- “博主我不喜欢，澄露面霜倒是保湿很好” → positive，creator/product，effect positive；博主观点不进入目标方面。
- “澄露面霜很好吸收，但这个价格不值” → mixed，product，effect positive + price negative。
- “澄露哪里买？” → no_attitude；有询问信号不等于已经认可。
- “澄露这款是50毫升” → neutral；没有对容量好坏作判断。
- “有人说澄露很差，我还没试过” → no_attitude + quoted_opinion；不能把转述当作作者认同。
- “澄露真贴心，坏了也不给退呢” → negative + sarcasm，必须人工复核。
- 单独“真行啊”且无上下文 → uncertain + missing_context。

## 候选后复核

检查：对象是当前作者实际评价的吗？标签由哪句原文支持？是否把问题当认可、把引用当认同、把反讽看成字面赞美？上下文只消歧还是被当作评论态度？

保存简短 `reason` 和具体 `evidence`，不要输出长推理。每条至少保留一段当前评论原文；额外上下文证据须标明来源。脚本只能确认这段字确实存在，仍需 AI/人工检查是否支持结论。

`flags` 可选 sarcasm、comparison、quoted_opinion、missing_context、mixed。默认这些类型和中/低自评置信都送复核。high 也不是自动确认为真；用户应抽查高置信候选，发现错误再调整规则。
