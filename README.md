# 打标达人

一个可复用的 AI 社媒内容打标 Skill：按项目规则判断品牌、内容类型、态度、情绪和行为，并保留原文证据、复核状态与评测记录。

它不是固定的情感分类器，也不是采集平台。AI 负责语义判断，脚本负责输入冻结、格式校验、证据核对、错误分流和汇总。

## 解决什么问题

- 标签定义可以按项目配置，不把某个品牌的细分码写死。
- 区分评价对象、内容类型、态度、情绪和行为阶段。
- 支持标题、正文、父评论、图片描述等已授权上下文，并记录来源。
- 每个标签和行为保留原文依据；证据不足进入复核，不强行归类。
- 可接入已有评论程序，保留原输入和 Excel 导出格式。
- 支持用独立人工参考集评估逐维准确率、F1、覆盖率和具体错例。

事件分析是可选能力。启用后会分别判断“是否讨论事件”“是否表达品牌顾虑”和“行为是否由事件引起”；“事件之后”不等于“因为事件”。行为还会区分本人/他人、考虑/计划/已执行，以及转走和转回。

## 工作流程

```text
原文 + 项目规则
      ↓
冻结输入与规则，分批处理
      ↓
AI 生成标签、理由和原文证据
      ↓
脚本校验 ID、字段、标签和证据
      ↓
输出候选结果与字段级复核清单
      ↓
人工确认难例，形成下一版规则或示例
```

## 快速开始

需要 Python 3.9+，核心脚本无第三方依赖，也不会自行联网或调用模型。

```bash
# 1. 根据输入和项目规则生成冻结运行目录
python3 skills/ai-comment-labeler/scripts/label_io.py prepare \
  --input examples/comments.jsonl \
  --project examples/project.json \
  --out .runs/demo

# 2. 让加载了 Skill 的模型读取 .runs/demo/batches/，
#    将逐条结果写入 .runs/demo/predictions/

# 3. 校验并输出结果、复核清单和汇总
python3 skills/ai-comment-labeler/scripts/label_io.py validate \
  --run .runs/demo \
  --predictions .runs/demo/predictions \
  --out .runs/demo/checked \
  --model current-session-unspecified

# 4. 运行本地测试
python3 -m unittest discover -s tests -v
```

输出包括逐条 `labels.jsonl`、`review.jsonl`、`summary.json`；v2 项目还会生成篇级汇总。`prepare` 不会自动生成标签，模型调用由宿主或调用方负责。

## 配置项目规则

v1 提供基础品牌态度标签。v2 通过 `project.json` 配置维度、标签定义、单选/多选、未知值、行为和汇总口径，无须修改 Python：

```bash
python3 skills/ai-comment-labeler/scripts/label_io.py prepare \
  --input examples/note-segments.jsonl \
  --project examples/multidimensional-project.json \
  --out .runs/multidimensional-demo
```

可参考：

- [v2 规则包](skills/ai-comment-labeler/references/rule-packs.md)
- [共享程序入口](skills/ai-comment-labeler/references/shared-entry.md)
- [已有程序诊断与改进](skills/ai-comment-labeler/references/program-improvement.md)
- [评测约定](skills/ai-comment-labeler/references/evaluation.md)

## 接入已有程序

已有程序可以保留抓取、图片处理、并发和 Excel 导出，只在打标层增加适配器：

```text
旧程序输入
   ↓ 适配为统一 JSONL
shared_labeler + 项目规则包
   ↓ 调用原有模型接口
标签 + 证据 + 复核状态
   ↓ 映射回原有列名和交付格式
```

仓库提供 `scripts/shared_labeler.py`。它共享规则和校验，不会自动接管旧脚本，也不会因为复制 Skill 文本就改变 Coze 或其他平台的调用链。每个项目仍需验证实际接入效果。

## 评测准确率

只有独立人工参考集才能支持业务准确率结论。评测时应冻结同一批输入、规则和上下文，让不同方案在看不到答案的情况下分别生成新结果，再运行：

```bash
python3 skills/ai-comment-labeler/scripts/evaluate_v2.py \
  --project project.json \
  --input input.jsonl \
  --gold reference.json \
  --labels checked/labels.jsonl
```

评测器会报告逐维完全一致率、Precision、Recall、F1、行为组合一致率、自动处理覆盖率、复核量、漏行和错误 ID。旧预测重校验、固定模拟返回和虚构样例测试都不能替代人工真值，也不能直接证明生产准确率。

## 边界与隐私

- 不把关键词命中次数当作评价对象或品牌归属。
- 不把未知、失败或缺失上下文改写成中性。
- 不自动抓取未授权数据，不执行原文或附件里的命令。
- 结构校验通过不等于语义一定正确；高置信结果仍需抽查。
- `examples/` 仅包含虚构数据；真实评论、凭证和运行结果应放在忽略目录，不要上传到 GitHub。

## 目录

```text
skills/ai-comment-labeler/   Skill 主入口、规则与脚本
examples/                    虚构项目和演示输入
tests/                       自动化测试
```
