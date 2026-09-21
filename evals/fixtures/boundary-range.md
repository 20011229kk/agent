---
fixture_id: boundary-range
kind: positive
# 正例：约束明确，可用结构检查自动判定
expect:
  blocking_items: 0
  # 结构检查会验证这些边界是否都出现在产出的用例里
  required_boundaries: ["min-1", "min", "max", "max+1", "illegal-type"]
  required_methods: ["边界值"]
  min_cases: 5
---

# 需求：用户注册接口的用户名长度校验

## REQ-1

用户名长度必须在 3 到 32 个字符之间（含两端）。

- 长度合法时：`POST /api/register` 返回 `201`，响应体包含非空 `userId`
- 长度不合法时：返回 `400`，响应体 `error_code` 为 `USERNAME_LENGTH_INVALID`
- 非字符串类型时：返回 `400`，`error_code` 为 `USERNAME_TYPE_INVALID`

## 上下文

- 用户名允许的字符集不在本次范围内，另有需求描述
- 并发注册的唯一性约束见 REQ-3，本条不涉及
