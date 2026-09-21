---
# TEMPLATE — qa/cases/<feature>/<module>.md
# 写入者：qa-design（唯一）。frontmatter 由 trace_matrix.py 解析，字段名不可改。
feature: example-login
baseline_version: "1.0.0"     # 必须与所依据的基线版本一致，否则判证据版本不匹配
cases:
  - id: TC-1
    covers: [REQ-1]           # 关联的 REQ，必填且必须存在于基线中（否则错标）
    manual: false             # true 表示人工执行：不进可自动化分母，但仍进必测范围校验
    layer: api                # unit | api | ui | e2e
    priority: P0              # 按风险判定，不按"每条 REQ 都要有 P0"
    method: 等价类            # 等价类 | 边界值 | 判定表 | 状态迁移 | 场景法 | 错误推测 | 并发 | 权限 | 幂等 | 兼容
  - id: TC-2
    covers: [REQ-2]
    manual: false
    layer: api
    priority: P0
    method: 边界值
  - id: TC-10
    covers: [REQ-1]
    manual: true
    layer: ui
    priority: P1
    method: 兼容
---

# example-login 测试用例

> 预期结果必须可机器校验。禁止"正常显示""功能可用""体验流畅"一类描述。

| 用例编号 | 模块 | 前置条件 | 测试步骤 | 预期结果 | 优先级 | 层级 | 人工 |
|---|---|---|---|---|---|---|---|
| TC-1 | 注册 | 用户名 `abc` 未被占用 | 1. POST `/api/register`，body `{"username":"abc"}` | HTTP 201；响应体含非空 `userId`；库中 `users` 表新增 1 行且 `username='abc'` | P0 | api | 否 |
| TC-2 | 注册 | 无 | 1. POST `/api/register`，body `{"username":"ab"}` | HTTP 400；`error_code == "USERNAME_LENGTH_INVALID"`；库中无新增行 | P0 | api | 否 |
| TC-10 | 注册 | Safari 17 | 1. 打开注册页<br>2. 输入 `abc` 并提交 | 页面跳转到 `/welcome`；`userId` 出现在页面 DOM 的 `#uid` 元素内 | P1 | ui | 是 |

## 边界值覆盖自查（REQ-1 范围 [3, 32]）

| 边界 | 用例 |
|---|---|
| min-1 = 2 | TC-2 |
| min = 3 | TC-1 |
| max = 32 | TC-3（待补） |
| max+1 = 33 | TC-4（待补） |

> 自查表只证明设计者考虑过边界，**不**替代 `trace_matrix.py` 的覆盖校验。
