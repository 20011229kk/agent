# 受保护路径清单与强制机制

**权威来源：** 本文件说明**为什么**这样保护；`CODEOWNERS` 是**实际生效**的审批归属。
两者不一致时以 `CODEOWNERS` 为准，并应修正本文件。

---

## 核心结论（基于实测，非文档推断）

Task 0 item 5 已验证：**shell 子进程的文件写入完全绕过 Kiro 的能力层**，包括
"配置不可更改"的 kiro-scope 硬拒绝。证据见 `docs/capability-matrix.md` item 5。

由此推出三条不可绕开的结论：

1. **`permissions` 的 `fs_write` deny 只约束 write 类工具，不约束测试进程。**
   只要 `qa-executor` 有 `shell`，它启动的 `pytest` 就能改写基线、门禁脚本、CI 配置。
2. **CI 内的哈希校验只是检测辅助，不是强制点。** 而且如果文件与期望哈希由**同一次候选变更**
   提供，哈希什么也证明不了——参照值必须来自受保护分支上的已审批版本。
3. **唯一可靠的强制点是受保护分支 + CODEOWNERS 审批。**

---

## 分层防护

| 层 | 机制 | 能拦住什么 | 拦不住什么 |
|---|---|---|---|
| L1 工具层 | `permissions.rules` `fs_write` deny | agent 直接用 write 工具越权 | **子进程写入**（已实测绕过） |
| L2 执行层 | 执行环境隔离（容器 / 只读挂载 / 独立用户） | 子进程写入 | 尚未实施（Task 3 必做项） |
| L3 仓库层 | 受保护分支 + CODEOWNERS | 未经审批的变更被合并 | 本地工作副本被改（不影响门禁结论） |
| L4 证据层 | CI 从可信执行的原始产物重新生成门禁输入 | 伪造的门禁输入 | **弱化断言**（残余风险，见下） |

L1 单独不足；L2 未落地前，本体系**不得**宣称"权限已自保护"。

---

## 受保护路径与理由

| 路径 | 为什么保护 | 参照版本来源 |
|---|---|---|
| `qa/baseline/**` | 覆盖率的分母。被改小即可造出"假全覆盖" | `target_sha` 上已审批版本（方案 B） |
| `qa/plan/**/required-scope.yaml` | 必测范围。被改小即可让必测项消失 | `target_sha` |
| `qa/plan/**` 中的 `retry_policy` | 重试策略放宽即可让间歇失败自动通过 | `target_sha` |
| `qa/plan/**` 中的 `review_mode` | 从 `automated_required` 改 `manual` 即可绕过自动评审 | `target_sha` |
| `scripts/gate_check.py` | 改判定逻辑直接拿绿灯 | `target_sha` |
| `scripts/guard_scan.py` | 删规则即可让门槛下降不被提示 | `target_sha` |
| `scripts/trace_matrix.py` | 改统计口径即可虚报覆盖 | `target_sha` |
| `scripts/validate_*.py` | 校验器被弱化则配置约束失效 | `target_sha` |
| CI 配置、`Makefile` | 改 required check 即可跳过门禁 | `target_sha` + 平台保护设置 |
| `CODEOWNERS` | 改归属即可自我授权 | `target_sha` + 平台保护设置 |
| `.kiro/agents/**` | 实测"永久询问"退化为静默允许，平台不保护 | `target_sha` |
| `steering/**` | 实测无任何硬保护 | `target_sha` |
| `.doc/specs/**` | 验收口径与任务状态来源 | `target_sha` |
| `docs/capability-matrix.md` | 决定哪些设计前提成立 | `target_sha` |
| `scripts/rebuild_run_json.py` | 把原始报告转成门禁输入。改它等于改证据来源 | `target_sha` |
| `evals/rubrics/**` | 改 rubric 等于改验收口径 | `target_sha` |
| `evals/fixtures/**` | 改反例 fixture 可让"知道自己做不了"这条检查失效 | `target_sha` |

### 明确不在保护范围

| 路径 | 原因 |
|---|---|
| `qa/cases/**` | `qa-design` 的正常产出；其正确性由覆盖校验与评审保证，不靠路径保护 |
| `qa/runs/**/interpretation.*` | agent 的解释性产出，**不参与通过计数**，改它不影响门禁结论 |
| `qa/defects/**` | 缺陷草稿，提单需显式确认 |
| `qa/runs/**/raw/*` | 由框架/CI 写入；门禁只认 CI 重新生成的版本，本地副本被改不影响判定 |

---

## 版本取值规则（防"用自带新规则给自己放行"）

| 取哪个版本 | 内容 |
|---|---|
| `target_sha` 上已批准版本 | 门禁执行规则、重试策略、`review_mode`、必测范围校验逻辑 |
| 针对本次变更已确认的版本 | 需求基线、本次必测范围 |

- merge-base **仅**用于计算差异范围，**不**作为规则来源（它是共同祖先，可能早于目标分支上
  最新的已审批规则变更）。
- 规则变更在**合并进目标分支后**对后续候选生效，**不对提出该变更的候选自身生效**。
  这样新规则能启用，候选也无法自我放行。
- `target_sha` 变化可能带来 `policy_version` 变化 → 需重新评估。

---

## 残余风险

**溯源分离能防伪造结果，防不住弱化断言。** 一个什么都不断言的测试在框架原生报告里同样是
passed，而测试代码可能正是 agent 写的。因此：

- `guard_scan` 是**风险提示器**，不是判定器；能识别显式 skip/xfail、断言删除、阈值数字变化、
  忽略范围扩大、重试次数/策略变化、测试文件删除；
- **不能**判断断言语义是否被削弱、数据清理是否有效；
- 对测试代码变更的人工评审**不因**溯源分离而取消。

同理，子代理评审只提供上下文隔离，同模型同盲点。权限/安全边界、持久化数据变更、并发控制、
跨模块协议变更仍需人工独立评审。
