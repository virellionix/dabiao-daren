# 程序统一接入

`scripts/shared_labeler.py` 是 Python 3.9+、标准库实现的共享调用入口。Skill 与它读取同一个 `labeling-policy.md`；标签来自各项目 v2 配置。它不是常驻服务，不包含 SDK、账号、网络采集或收费模型。单条 item 已实现；连续分段与篇级汇总继续走 `label_io.py`。

## 一次适配，以后共用

在程序环境中保留完整 Skill 目录，导入 scripts 下的 `SharedLabeler` 与 `adapt_record`。不要只复制策略文本后继续各自维护。程序固定一个验收过的仓库提交；更新后先回归，再升级，避免共享文件变化无意改变运行中的项目。

```python
labeler = SharedLabeler(
    project_config, model_callback, model_id="调用方实际使用的模型版本",
    catalog=product_catalog, context_resolver=context_lookup,
)
record = adapt_record(
    row, id_column="评论ID", text_column="评论内容",
    context_columns={"title": "笔记标题", "parent_comment": "父评论原文"},
    source_kind="full_text",
)
result = labeler.label(record, context_refs={"post_text": row["笔记ID"]})
output = labeler.export_columns(result, {"purpose": "判断标签", "stance": "品牌态度"})
```

示例列名须替换为实际列；不要凭空生成评论 ID、笔记关联或完整原文。`adapt_record` 不把 NaN/None 转成字符串。原 UGC 程序的输入是「笔记ID、笔记标题、笔记正文、作者资料」，其「主体类型、二级标签」可映射到配置的两个维度；二者用 label_relations 校验，不能套用示例的咨询/反馈标签代替客户标签树。原字段「是否SGC」若仍要保留，必须允许未知或错误为空，并带状态，不能用 `bool("false")`。

完整可运行的零网络演示在仓库 `examples/shared_entry_demo.py`：从旧式中文列读入 → 请求原笔记 → 补齐 → 再判断 → 导出旧列及状态。其中模型返回是固定模拟数据，不是准确率测试。

## 项目与本品识别

构造时 `project` 是完整 v2 项目，允许暂缺 target_brand/aliases，前提是有明确的本品产品库。catalog 每项严格为：

```json
{"target":"星舟","aliases":["星舟耳机"],"is_own":true}
```

仅当库中有唯一明确的本品 target 才自动补齐目标，竞品别名不并入本品。目标已指定则保留；与本品标记冲突、多个本品目标或缺少可判断资料时构造失败，不按提及最多强猜。目录最多 10,000 项；最终别名沿用契约的 100 项限制，超限报错不截断。本方法从调用方已经读出的元数据解析，不直接读取 Excel，也不自行扫描语料推断商业目标；宿主 AI 可先从用户任务与数据建立候选项目配置。单条实际评价对象仍由模型逐条判断。

## 模型回调

同步函数 `model_callback(request) -> dict | JSON字符串`。调用方沿用选定模型、凭证与超时设置。请求含：policy、output_contract、project、record、context_sources、context_trace、history、can_request_context、response_protocol。

将 policy 和输出契约放在模型的指令层；项目作为标签规则，record/history 中的数据作为待分析材料，不把原文里的命令提升为指令。回调必须实际传入提供的策略、规则和上下文；只返回旧分类接口的结果不等于采用 Skill。记录真实模型版本，不把示例模型名用在正式结果上。

返回二选一，拒绝其他字段：

```json
{"type":"context_request","sources":["parent_comment","post_text"],"reason":"需要消解指代和回复关系。"}
```

或 `{"type":"prediction","prediction":完整v2候选对象}`。候选字段见 rule-packs.md；布尔必须真实 true/false。不得发明标签、用未知替换中性，或把调用失败包装成合法候选。

每条最多两次模型调用、一次上下文补查；第二次仍要求补查即停在 review，不循环、不自动重试。无需上下文时一次完成。请求最多 150,000 字符，返回最多 50,000 字符，不代表 token 数；调用耗时/取消及批量并发由原程序负责。构造/输入/超限等调用方错误抛出 ValueError，由批处理按 ID 记录；模型调用失败和非法返回分别进入 error，不生成默认标签。同步回调必须配置网络超时，共享层不能中断一个永不返回的回调。

## 已授权上下文回调

模型只能选择 context 类型，不能传入 URL 或 ID。调用方用 `context_refs` 预先绑定当前条目的稳定引用；resolver 收到：

```json
{"kind":"post_text","record_id":"c1","source_ref":"n1"}
```

调用方须核验真实评论—笔记/父评论关系、当前用户权限与来源；共享层只核对回传绑定是否一致，不冒充鉴权。无引用、无读取器、权限不足时不能搜索无关数据补空。

resolver 返回以上三个绑定字段，加 status（ok/not_found/permission_denied/unavailable），ok 时带 text。只接收来源匹配、非空且每来源不超过 12,000 字符的内容；补齐后上下文总计不超过 30,000 字符，超限不截断。每轮最多请求四种来源，已有内容不再抓取；发生错误不自动重试。来源、关联与文本摘要进入 context_trace，获取内容保留在 result.input.context。缺失来源仍可保留其他候选，但整体 review。图像 OCR/描述引用额外标复核，未验证原图就不能宣称看过。

## 结果与导出

- candidate：结构与证据子串校验通过，未触发复核；不是人工确认。
- review：存在具体复核项，可能有候选；继续缺关键上下文时也可能没有候选。
- error：模型调用失败或非法返回，prediction 为空。

每条保留 input、prediction、review_details、field_status、context_trace、model_calls、model_call_details、provenance。`model_call_details` 至少记录每次调用的 status 和 duration_ms；如果调用方的模型回调对象在返回后暴露 `last_usage` 字典，共享入口会一并保存 token usage。没有 usage 或价格时统计脚本必须报告 unavailable，不估算成本。provenance 含规则版本及项目/策略/契约/代码摘要，模型 ID 由调用方据实填写；semantic_accuracy 固定 not_measured，不能改成模型自评准确率。

`export_columns` 按维度输出项目标签名称，始终追加「打标状态、复核原因」。无候选时标签为空，unknown 保留「无法判断」等项目名称，不转中性或 false。完整 result 应存调用方私有运行目录；只存两个标签列会丢失证据，不能作为完整验收记录。网络回调、旧程序迁移和实际业务准确率必须分别验收，不因离线演示通过就声称线上生效。
