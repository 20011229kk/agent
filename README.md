# QA Agent Suite

面向测试团队的 Kiro 代理体系。目标不是"让 AI 写测试"，而是补上流程里的四处断裂：
需求验收标准缺失、追溯断裂、失败归因断裂、门禁断裂。

**当前状态：v0.1.0，轨 A（平台无关部分）已完成，轨 B 待解阻。**

## 快速开始

```bash
make check          # 结构校验 + 全部单测（提交前跑这个）
make gate           # 本地门禁自检（非门禁）
make gate DIFF=/tmp/candidate.diff    # 含门槛变化扫描
```

环境要求：`/usr/bin/python3`（3.9.6，已自带 PyYAML 与 pytest），**零额外依赖**。

## 权威文件

| 文件 | 作用 |
|---|---|
| `.doc/specs/qa-agents/requirements.md` | R1–R9 需求与六条反例验收 |
| `.doc/specs/qa-agents/design.md` | 架构、数据模型、判定口径 |
| `.doc/specs/qa-agents/tasks.md` | **唯一任务状态来源** |
| `docs/capability-matrix.md` | **Kiro 平台能力唯一事实来源**（实测优先于官方文档） |
| `docs/protected-paths.md` | 受保护路径与为什么不能靠权限规则 |
| `CODEOWNERS` | 可信计算基的实际强制点 |

## 脚本

确定性的事交给脚本，判断的事交给人和模型。

| 脚本 | 作用 | 定位 |
|---|---|---|
| `scripts/validate_spec.py` | spec 三件套 + 基线 + 模板 + CODEOWNERS 结构校验 | 只证明格式合规 |
| `scripts/trace_matrix.py` | 派生 `REQ ↔ TC ↔ AT ↔ attempt` 矩阵与四状态覆盖 | 分母来自基线，不来自矩阵 |
| `scripts/gate_check.py` | 合并门禁三态判定 `PASS/FAIL/INCOMPLETE` | **合并门禁，不是发布准出** |
| `scripts/guard_scan.py` | 质量门槛变化扫描 | **提示器，不是判定器** |

## 数据流

```
REQ  (qa/baseline/<f>/requirements.yaml   人确认 + 审批)
 └─ TC  (qa/cases/<f>/*.md frontmatter    qa-design 写)
     └─ AT  (collect 清单                  测试框架产出)
         └─ attempt (qa/runs/<id>/run.json 框架/CI 产出)
```

`matrix.csv` 与 `merge-gate-report.json` 是**派生产物，无 agent 写入者**。
详见 `design.md` 的产物所有权表。

## 几条不打折的口径

- **四状态分开统计**：已设计 / 已自动化 / 已执行 / 已通过，不合并成"测到了"。
- **证据缺失 ≠ 已确认未自动化**。没有 collect 清单时状态是 unknown，不是 0%。
- **归因两轴**：根因 `UNKNOWN/ENV/DATA/SCRIPT/PRODUCT/DEPENDENCY` × 稳定性
  `稳定失败/间歇失败/未确认`。`FLAKY` 不是根因取值 —— 把观察当根因是范畴错误，
  也正是并发缺陷被重跑掉的通道。
- **`last_result` 与 `gate_accepted` 分离**。末次通过是观察，不等于门禁接受；
  已确认的阻断产品缺陷不能被一次重试抹掉。
- **三态门禁**：`FAIL` 是测出了问题，`INCOMPLETE` 是没测够或证据不成立，二者不混同。
- **必测范围不得由运行结果反推**。先定范围，再执行。
- **v1 不发阈值数字**。没有项目依据的阈值是编造；只发完整性规则。

## 已知边界

两条必须说在前面，不然容易把这套东西当成比实际更可靠的东西：

1. **shell 子进程写入绕过 Kiro 能力层。** 已实测确认（`docs/capability-matrix.md`
   item 5）：子进程成功写入了 `fs_write` 工具被 kiro-scope 硬拒绝的同一路径。
   因此**不能**说"写入范围由 permissions 保证"；子进程边界必须靠执行环境隔离。
   Task 3 必须补隔离并重跑该探针。
2. **溯源分离防不住弱化断言。** 不断言的测试在框架原生报告里同样是 passed，
   而测试代码可能由 agent 编写。`guard_scan` 能发现显式 skip、断言行减少、阈值变化，
   **不能**判断断言语义是否被削弱、数据清理是否有效。对测试代码变更的人工评审不取消。

另外：子代理评审只提供上下文隔离，同模型同盲点。权限/安全边界、持久化数据变更、
并发控制、跨模块协议变更仍需人工独立评审。

## 三层执行，只有一层是门禁

| 层 | 作用 | 是否门禁 |
|---|---|---|
| Kiro hook | 即时反馈（仅对 agent 改动；File 类仅 IDE） | 否 |
| git hook | 本地自检 | **否**（可被 `--no-verify` 绕过） |
| 受保护分支 CI required check | 合并门禁 | **是，唯一** |

## 待补输入

见 `tasks.md` 末尾。简要：v1 真实接口场景仓库、阻断缺陷等级定义位置、
CI 能否跑 agent（决定 `review_mode`）。
