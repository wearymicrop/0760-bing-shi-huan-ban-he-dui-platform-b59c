# 冰时换班核对台深度登记

规模为中型。结账员按交班截点恢复有效订场，核对每单分钟去向及权益余额。

## 功能模块

| 模块 | 使用者动作 | 入口 | 核心文件 | 测试 |
|---|---|---|---|---|
| M1 冰场日历 | 筛选场地并校对营业窗 | `/desk/calendar` | `app/modules/calendar/rules.py` `app/modules/calendar/service.py` | `tests/test_calendar.py` |
| M2 包场权益 | 查看各时券的有效段和可用余额 | `/desk/passes` | `app/modules/passes/rules.py` `app/modules/passes/service.py` | `tests/test_passes.py` |
| M3 冰面占用 | 检查上报动作、重传和无效时刻 | `/desk/bookings` | `app/modules/bookings/events.py` `app/modules/bookings/service.py` | `tests/test_bookings.py` |
| M4 换班结转 | 按截点重放，区分兑现、撤销与待裁决 | `/desk/changes` | `app/modules/changes/rules.py` `app/modules/changes/service.py` | `tests/test_changes.py` |
| M5 有效折抵 | 点击跨日片段检查扣券和自付分钟 | `/desk/debit` | `app/modules/debit/partition.py` `app/modules/debit/service.py` | `tests/test_debit.py` |
| M6 时券对账 | 复核分钟等式和差额，展开争议证据 | `/desk/balance` | `app/modules/balance/rules.py` `app/modules/balance/service.py` | `tests/test_balance.py` |
| M7 值班归档 | 复算原输入、切换历史批次和下载凭证 | `/desk/archive` | `app/modules/archive/handler.py` `app/modules/archive/service.py` | `tests/test_archive.py` |
| M8 换班复核单 | 按预订查看本班结论、下班待办与原记录，导出同一复核单 | `/desk/handover` | `app/modules/handover/rules.py` `app/modules/handover/service.py` | `tests/test_handover.py` |

## 陷阱登记

| TRAP | 现象 | 朴素实现会怎样 | 本项目处理 | 处理位置 | 测试 |
|---|---|---|---|---|---|
| TRAP-02 | 输入以 BOM 起头 | JSON 解码拒绝正确数据 | `night.json` 前置 BOM 的同结构倒入 | `app/source.py` | `tests/test_source.py::test_bom_reads_without_affecting_facts` |
| TRAP-08 | 权益号 `00041` | 前导零被转换丢失 | 保留字符串 ID | `app/modules/passes/rules.py` | `tests/test_passes.py::test_initial_zero_preserved` |
| TRAP-11 | 夜场同用 Z 与 +08 | 混算输入时间导致夜班错归日 | 统一为上海时间 | `app/clock.py` | `tests/test_calendar.py::test_explicit_offset_maps_to_same_minute` |
| TRAP-13 | 夜场 `0042` 改单先到 | 源行顺序决定最终版本 | 排序重放完整修订链 | `app/modules/changes/rules.py` | `tests/test_changes.py::test_unordered_replay_uses_revision_time` |
| TRAP-14 | 夜场 `0051` 同版异内容 | 静默后写覆盖 | 隔离订单并保留冲突双方源行 | `app/modules/changes/rules.py` | `tests/test_changes.py::test_same_revision_disagreement_cannot_win_by_last_write` |
| TRAP-16 | 夜场文件中途断开 | 罗列部分输入却出具完整封存 | 解析后原子生成，错误不新增批次 | `app/source.py` | `tests/test_archive.py::test_error_does_not_publish` |
| TRAP-19 | 夜场北冰两类开场窗重合 | 按窗口先后取一类 | 交叠片段进入争议 | `app/modules/calendar/rules.py` | `tests/test_calendar.py::test_contradictory_windows_numbered` |
| TRAP-23 | 夜场西冰 22:15 恰好换类 | 两个窗口重复收权 | 半开区间 | `app/modules/calendar/rules.py` | `tests/test_calendar.py::test_adjacent_boundary_has_single_owner` |
| TRAP-25 | 夜场飞雪两张券期间重叠 | 随意扣券责任 | 停扣重叠片段并给出双方权益行 | `app/modules/passes/rules.py` | `tests/test_passes.py::test_overlapping_entitlements_hold_minutes` |
| TRAP-26 | 夜场 `0042` 撤销跨过当班截点 | 改写已交接预订 | 延后事件待下班，保持本班有效订单 | `app/modules/changes/rules.py` | `tests/test_changes.py::test_late_cancellation_cannot_rewrite_published_view` |
| TRAP-27 | 21:55 改单到 22:20 才出现在输入 | 按源行顺序漏记截点前占用 | 按发生时刻入本班，晚投递只作注记 | `app/modules/changes/rules.py` | `tests/test_handover.py::test_change_stamped_before_cutoff_counts_even_when_row_arrives_late` |
| TRAP-28 | 23:02 撤销在交接前已到达 | 直接改写已交财务的本班账 | 保留本班结论，撤销列入下班待办 | `app/modules/changes/rules.py` `app/modules/handover/rules.py` | `tests/test_handover.py::test_cancellation_after_cutoff_does_not_rewrite_this_shift` |
| TRAP-29 | 同批重跑 | 重复累加下班待办 | 待办按源行派生固定编号，重跑同批不新增 | `app/modules/handover/rules.py` | `tests/test_handover.py::test_rerunning_same_batch_does_not_add_todos` |
| TRAP-30 | 源批次不完整或未核对 | 出具正式复核单 | 区分源行与核对两类原因，未达标只出临时单 | `app/modules/handover/rules.py` | `tests/test_handover.py::test_conflict_is_retained_and_blocks_formal_sheet` |

## 对账规则

- 夜场有效预订分钟 340 = 扣券 70 + 自付 155 + 豁免 30 + 争议 85。
- 券的各版本核发量逐项等于已用与剩余之和。有效累计扣券 = 当班权益已用量。
- 换班复核单按预订给出本班结论、下班待办和原始源行；争议行保留，正式单须源批次完整且分钟、权益对平。
