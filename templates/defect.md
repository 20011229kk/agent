---
# TEMPLATE — qa/defects/BUG-<id>.md
# 写入者：qa-executor（草稿）。提单与任何远端写入需显式确认。
# frontmatter 由 trace_matrix.py 解析，字段名不可改。

id: BUG-1

# ---- 关联（至少填一个，否则该缺陷进不了任何范围的阻断判定）----
# tc  : 直接关联的测试用例
# req : 关联的需求条目。**REQ 级关联会传播到本次覆盖该 REQ 的所有用例** ——
#       所以"这条需求坏了但还没定位到具体用例"也能正确阻断。
tc: [TC-1]
req: []

# ---- 阻断判定契约（三个字段各管一件事）----
# closed              已关闭 → 不再阻断，但仍记录在案
# confirmed_blocking  人已确认它阻断 → 阻断
# severity            落在 required-scope 的 blocking_defect_severities 内 → 阻断
#
# 未关闭 且（confirmed_blocking 为 true 或 severity 在阻断清单内）= 阻断。
# 已确认的阻断缺陷**不会**被一次重试通过抹掉。
severity: S1
confirmed_blocking: true
closed: false

# ---- 两轴归因 ----
# root_cause : UNKNOWN | ENV | DATA | SCRIPT | PRODUCT | DEPENDENCY
#              证据不足就记 UNKNOWN，不要猜。
# stability  : 稳定失败 | 间歇失败 | 未确认
#
# FLAKY 不是 root_cause 的合法取值 —— 把观察当根因是范畴错误，
# 也正是并发缺陷被重跑掉的通道。间歇失败只说明稳定性，不能排除 PRODUCT。
root_cause: PRODUCT
stability: 间歇失败
---

# BUG-1 并发注册时唯一约束失效

## 环境与版本

| 项 | 值 |
|---|---|
| 环境 | qa-isolated |
| candidate_sha | abc1234 |
| 基线版本 | 1.0.0 |
| traceId | t-20260920-0001 |

> 配置标识必须脱敏。凭据、token、cookie 一律不进这份文档；
> 诊断需要的 `traceId` 等字段要保留。

## 最小复现步骤

1. 并发发起 2 次 `POST /api/register`，body 均为 `{"username":"abc"}`
2. 查询 `users` 表中 `username='abc'` 的行数

## 预期与实际

| | |
|---|---|
| 预期 | 一次返回 201、一次返回 409；库中恰好 1 行 |
| 实际 | 两次均返回 201；库中 2 行 |

## 证据

- 请求/响应：`qa/runs/r1/raw/concurrent.har`
- 日志：`qa/runs/r1/raw/app.log`（搜 traceId）
- attempt 序列：attempt-1 failed，attempt-2 passed（间歇失败）

## 根因假设

**这是假设，不是结论。** 唯一索引可能只在应用层校验，未落到数据库约束。
需开发确认 DDL 是否存在 `UNIQUE(username)`。

## 影响面与建议回归范围

- 影响：所有依赖用户名唯一性的下游逻辑
- 建议回归：TC-1、TC-2、TC-3（注册全链路）+ 并发专项
