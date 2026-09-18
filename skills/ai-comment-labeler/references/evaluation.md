# 可复现的准确率与对照试验

区分三个结果：固定模拟返回测代码；新 AI 预测对照开发预期测开发边界；新预测对照独立人工参考才可支持限定样本上的业务质量。重用旧预测只叫重校验，不描述为模型重跑。即使十二条全对，也不能证明细标签、另一个平台或真实客户全对。

## 公平对比 Coze 与 Skill

两边固定同一批 ID、同一份原文/上下文、同一标签定义和独立参考；在看参考答案之前保存各自新预测。记录平台、实际模型版本、规则版本、运行时间和上下文读取差异。比较“是否接入 Skill”的效果尽量保持模型与输入一致；若模型不同，只能比较两套完整方案，不能把差异全部归给 Skill。

原文只出现话题与明确实际行动、普通更换与事件归因、考虑与计划与完成、转回、他人经历、否定、指代缺上下文均应覆盖。实际抽样不只挑容易或高置信样本；开发/改规则用例与最终评测样本分开，不反复对着测试答案调规则。

没有人工参考时继续做有标记的开发试验，不要求用户凭空给所有标签真值。多个 AI 一致不能冒充人工确认。用户已提供的旧 AI 标签不自动升级为真值。保持看不到答案的预测与参考分离，评测后再解释争议。

## v2 评测器

```bash
python3 <skill>/scripts/evaluate_v2.py \
  --project project.json --input input.jsonl \
  --gold reference.json --labels checked/labels.jsonl
```

接受 label_io.validate 的完整 labels.jsonl 或 SharedLabeler.label 的完整结果 JSONL，不能只传标签列。评测不调用模型、不改文件。输出 JSON 可由运行方保存到私有目录。

reference.json 格式（示意；摘要需从实际冻结数据计算）：

```json
{
  "reference_kind": "development",
  "reference_note": "开发者预设，只验证边界；非人工业务金标准。",
  "project_sha256": "项目JSON的规范化摘要",
  "input_sha256": "输入行数组的规范化摘要",
  "rows": [
    {
      "id": "c1",
      "labels": {"event_relevance": ["related"], "brand_concern": ["expressed"]},
      "behaviors": [{"action":"switch_away","actor":"self","stage":"planned","time_scope":"after_event","event_relation":"caused_by"}]
    }
  ]
}
```

- 摘要为 `label_io.digest(label_io.encode(project))` 和 `label_io.digest(label_io.encode(input_rows))`，不是原文件字节哈希。输入行顺序也是冻结内容；不匹配拒绝比较。新增上下文属于不同实验条件，须另冻输入并说明差异。
- reference_kind 只能 human 或 development；human 由文件提供者声明，程序无法认证标注者。reference_note 记录谁/何时/按什么口径确认，以及是否独立于预测；评测器始终标注限定参考范围，不输出无条件“业务准确率”。
- 每条 labels 覆盖全部项目维度，单选恰一项，多选可空；unknown 与具体标签不共存。reference rows 必须覆盖所有输入 ID，不得只标容易样本。没有完整参考的批次先另外固定有参考的子集及抽样范围，不伪造缺失标签。
- behaviors 为动作/行为人/阶段/时间组合；启用 event 时追加 event_relation。转回和转走用不同 action。无事件项目不要添加 event_relation。此参考格式不含证据，证据质量需另行人工抽查。
- 预测 ID 重复或多出、输入或规则版本不匹配会拒绝比较；漏行、error、非法返回计入原分母并报告，不能删掉后提高正确率。结果未满足当前校验也计 error，不能作为自动候选。

输出逐维 exact_match_accuracy（单选即准确率，多选为整组完全一致率）、各类 precision/recall/F1、micro-F1、实际出现类别的 macro-F1。单选另列混淆矩阵及漏行/错误计数；unknown 作为显式类别参与比较。零分母指标为 null，不伪造 0 或 100%。

另报全部维度同时一致率、行为组合完全一致率/F1、预测覆盖率、整条自动候选覆盖率、自动候选子集逐维一致率，以及带 ID/字段的错例。自动候选按结果状态和当前规则重新检查；待复核项仍在整体分母，不能拿低覆盖的高准确率掩盖无法处理量。行为组合错一个阶段/方向/因果就不算该组合匹配，不以仅动作名称一致放行。

当前粒度是**输入条目**（段项目就是段），不自动证明篇级汇总正确；对象、方面、证据语义、来源真实性和全文覆盖仍需额外抽查。空多选/空行为可完全一致，但没有正例时 F1 为 null。小样本结果仅描述该样本，不承诺泛化、生产吞吐或自动改标。
