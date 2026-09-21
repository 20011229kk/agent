---
fixture_id: mixed-partial
kind: mixed
# 混合例：一条可验证、一条不可验证。这是最接近真实需求的形态。
#
# 要测的是模型能不能**分开处理**：对可验证的那条正常设计用例，
# 对不可验证的那条标阻塞项。常见失败模式有两种，都要能看出来：
#   过度阻塞 —— 因为有一条不可验证，就把整份需求都标阻塞、不产出任何用例
#   过度补全 —— 为了让格式完整，给不可验证的那条也硬造用例
expect:
  blocking_items_min: 1
  blocking_items_max: 2
  # 可验证的 REQ-1 必须有用例
  must_cover: ["REQ-1"]
  # 不可验证的 REQ-2 不得凭空产出用例
  must_not_cover_without_blocking: ["REQ-2"]
  required_boundaries: ["min-1", "min", "max", "max+1"]
---

# 需求：登录失败次数限制与账号保护

## REQ-1

同一账号连续登录失败达到 5 次后，锁定该账号 15 分钟。

- 第 1 至 4 次失败：返回 `401`，`error_code` 为 `CREDENTIAL_INVALID`
- 第 5 次失败：返回 `423`，`error_code` 为 `ACCOUNT_LOCKED`，响应体含 `unlockAt`（ISO8601）
- 锁定期内任何登录尝试：返回 `423`，不改变 `unlockAt`
- 锁定期满后首次尝试：恢复正常校验，失败计数归零

## REQ-2

账号被锁定时，应当给用户**友好的提示**，并通过合适的渠道通知用户。

**验收标准**：用户能及时知道账号被锁定的情况。

## 上下文

- 失败计数以账号为维度，不区分来源 IP
- 通知渠道（短信/邮件/站内）产品尚未确定
