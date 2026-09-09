# 打标达人

一个把社媒评论转成**有原文依据、能复核、可按项目复用**的品牌态度打标 Skill。

首版面向做品牌舆情、消费者洞察、媒介和数据分析的同事。AI 负责语义判断，Python 脚本负责分批、查漏行、核对原文依据和分流复核。不是关键词正负面分类器，也不是已经训练好的独立模型。

## 怎么用

将 `skills/ai-comment-labeler` 整个目录放到宿主支持的 Skills 目录，或让 AI 直接读取其 `SKILL.md`。脚本需要 Python 3.9+，不依赖外部包，不调用网络。

可以这样对 AI 说：

> 用打标达人帮我标这批评论，目标品牌是澄露。先给我看几条典型结果，拿不准的单独列出来；我确认标准后再跑剩下的。

用户旅程：

```text
给评论 + 目标品牌
       ↓
确认标签口径与少量示例 ← 有分歧则修订规则
       ↓
AI 按对象、方面、态度逐条判断，并检查原文依据
       ↓
脚本检查 ID 全覆盖、字段及证据，输出结果 + 复核清单
       ↓
人工确认难例与抽查候选 → 经确认的纠错进入下个版本示例
```

### 开发演示

仓库 `examples/` 只有虚构品牌与虚构评论，不包含客户数据。以下在仓库根目录运行：

```bash
python3 skills/ai-comment-labeler/scripts/label_io.py prepare \
  --input examples/comments.jsonl --project examples/project.json --out .runs/demo
```

随后让加载了 Skill 的 AI 读取 `.runs/demo/batches/`，把逐条结果保存到 `.runs/demo/predictions/`。**prepare 不会调用 AI，也不会自动生成标签。**

```bash
python3 skills/ai-comment-labeler/scripts/label_io.py validate \
  --run .runs/demo --predictions .runs/demo/predictions \
  --out .runs/demo/checked --model current-session-unspecified
python3 -m unittest discover -s tests -v
```

输出：逐条结果 `labels.jsonl`、人工复核清单 `review.jsonl`、处理数量 `summary.json`。输入输出只支持 JSONL；Excel、飞书、网页和 API 服务不在首版范围内。运行目录不会自动上传 GitHub。

## 首版能力与边界

- 明确区分 positive、negative、neutral、mixed、no_attitude、uncertain；不把骂博主默认算成骂品牌。
- 读取用户提供的标题/摘要/父评论，区分当前作者与被引用者；不抓取或虚构缺失上下文。
- 按品牌、别名和方面配置复用；保留规则与输入摘要，可分批保存、接着处理未完成批次。
- 校验完整 ID、合法标签、证据原文子串，保留输入顺序；异常明确失败，不拿漏行充当完成。
- 对反讽、比较、混合态度、上下文不足及非 high 自评信号默认送人工复核；高置信候选仍需抽查。
- 有参考标签时评测各类 precision/recall/F1 和混淆矩阵。结构校验、虚构样例测试均不证明真实业务准确率。
- 人工纠错目前经人工确认更新规则/示例，不自动训练。多模型融合、Snorkel、Argilla 集成与概率校准留待真实误差证据支持后决定。

## 按用户资料做出的设计

起点是用户提供的[AI 评论打标参考对话](https://chatgpt.com/share/6aa0bf6a-7e2c-83ee-bcd4-73af0e5c4368)，不是通用情绪总结模板。对应关系：

| 资料中的重点 | 首版落点 |
|---|---|
| 先设计 Codebook | 明确六类的定义、排除条件、正反边界示例，先小样确认 |
| Target → Aspect → Stance，先候选再检查 | 对象与方面分别记录，原文证据与简短依据并存 |
| 有限上下文、品牌/产品消歧 | title/post_summary/parent_comment + 显式品牌别名 |
| 允许拒绝判断 | no_attitude 与 uncertain 分开，不强制正中负 |
| 人工纠错形成可用规则 | 保存纠错记录，确认后递增规则版本，测试集不回流 |
| 不只看总体准确率 | 各类 F1、混淆矩阵、复核比例与候选覆盖率分开呈现 |

2026-09-09 核对的外部参考：

- [open-science-skills / text-classification](https://github.com/scdenney/open-science-skills/blob/main/plugin/skills/text-classification/SKILL.md)：参考其标签规范、试标和人工验证的通用方法。仓库标示 CC BY-NC 4.0，**本项目不复制/分发它的正文、脚本或示例**，独立实现。其针对重复登记文本的去重建议不适用于本项目带上下文的评论，故不沿用。
- 用户资料推荐的 [onatcipli/skills](https://github.com/onatcipli/skills)：本次实时 GitHub API 与页面访问返回 404（搜索索引仍有旧记录），未核实实现，不作为依赖或声称已复用。
- 参考对话中的论文指标、模型集成收益未在本项目复现，不能用作本产品准确率或省时承诺。首版不部署小模型，不集成完整标注平台。

私有原型仓库；目前未授予开源许可证。发布或上传真实数据前须另行确认。

## 当前验证状态（2026-09-09）

- 28 项本地自动化测试通过；Skill 元数据验证通过。
- 独立 Agent 仅读取 Skill 和 12 条虚构输入，完成了真实模型标注；主线检查并生成逐条结果及 4 条复核记录。原文中的指令未被执行。
- 总体标签与 `tests/fixtures/expected_stances.jsonl` 中预先设定的 12 个开发预期一致。预期由开发 AI 编写，不是人工金标准，也不是独立业务测试集；不能称为“业务准确率 100%”。
- 原始运行与模型预测留在本地 `.runs/`，不提交仓库。此次精确宿主模型 ID 未确认，标记为 `current-session-unspecified`；后续正式评测需记录精确模型版本。
- 尚未验证：真实评论质量、人工标注一致性、跨项目泛化和大批量耗时。下一步是用户给一份去敏评论及现有标签规则，先试标再确认。
