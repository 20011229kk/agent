"""scripts/check_workflow_env.py 的测试。

来源：外部复核原样执行 merge-gate.yml 的"准备产物目录"步骤，得到
`RAW_ROOT: unbound variable` 退出 1 —— 该变量唯一的赋值在**后面**的步骤里。
后果不是预期的"缺证据 INCOMPLETE"，而是流程中断：下载与组装步骤被跳过，
后续 always() 也补不出有效报告。

`yaml.safe_load` 通得过，所以必须有专门的判据。下面把那个精确场景固化为反例。
"""

import os
import subprocess
import sys

import pytest
import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import check_workflow_env as cwe  # noqa: E402

TOOL = os.path.join(ROOT, "scripts", "check_workflow_env.py")


def write_wf(tmp_path, jobs, env=None):
    data = {"name": "t", "on": "push", "jobs": jobs}
    if env:
        data["env"] = env
    p = tmp_path / "wf.yml"
    p.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
                 encoding="utf-8")
    return str(p)


def codes(issues):
    return [i.code for i in issues]


def names(issues):
    return [i.name for i in issues]


# --------------------------------------------------------------------------
# 复核实测的精确场景
# --------------------------------------------------------------------------

def test_R_raw_root_used_before_set(tmp_path):
    """后面的步骤才写 GITHUB_ENV，前面的步骤已经在读它。"""
    wf = write_wf(tmp_path, {"gate": {"steps": [
        {"name": "prepare", "run": 'if [ -e "$RAW_ROOT" ]; then exit 1; fi'},
        {"name": "assemble",
         "run": 'echo "RAW_ROOT=$RUNNER_TEMP/raw" >> "$GITHUB_ENV"'},
    ]}})
    issues = cwe.check_workflow(wf)
    assert codes(issues) == ["ENV_USED_BEFORE_SET"]
    assert names(issues) == ["RAW_ROOT"]


def test_job_env_makes_it_visible_to_all_steps(tmp_path):
    """修法：在 job 级 env 定义 —— 所有步骤（含 action 的 with）都能看到。"""
    wf = write_wf(tmp_path, {"gate": {
        "env": {"RAW_ROOT": "${{ runner.temp }}/raw"},
        "steps": [
            {"name": "prepare", "run": 'if [ -e "$RAW_ROOT" ]; then exit 1; fi'},
        ]}})
    assert cwe.check_workflow(wf) == []


def test_earlier_github_env_write_is_visible_later(tmp_path):
    wf = write_wf(tmp_path, {"gate": {"steps": [
        {"name": "set", "run": 'echo "X=1" >> "$GITHUB_ENV"'},
        {"name": "use", "run": 'echo "$X"'},
    ]}})
    assert cwe.check_workflow(wf) == []


def test_order_matters_reversed_is_rejected(tmp_path):
    wf = write_wf(tmp_path, {"gate": {"steps": [
        {"name": "use", "run": 'echo "$X"'},
        {"name": "set", "run": 'echo "X=1" >> "$GITHUB_ENV"'},
    ]}})
    assert names(cwe.check_workflow(wf)) == ["X"]


# --------------------------------------------------------------------------
# 可见性来源
# --------------------------------------------------------------------------

def test_workflow_level_env(tmp_path):
    wf = write_wf(tmp_path, {"j": {"steps": [{"run": 'echo "$G"'}]}},
                  env={"G": "1"})
    assert cwe.check_workflow(wf) == []


def test_step_level_env(tmp_path):
    wf = write_wf(tmp_path, {"j": {"steps": [
        {"run": 'echo "$S"', "env": {"S": "1"}}]}})
    assert cwe.check_workflow(wf) == []


def test_same_step_assignment(tmp_path):
    wf = write_wf(tmp_path, {"j": {"steps": [
        {"run": 'X=1\necho "$X"'}]}})
    assert cwe.check_workflow(wf) == []


def test_export_assignment(tmp_path):
    wf = write_wf(tmp_path, {"j": {"steps": [
        {"run": 'export X=1\necho "$X"'}]}})
    assert cwe.check_workflow(wf) == []


def test_for_loop_variable(tmp_path):
    wf = write_wf(tmp_path, {"j": {"steps": [
        {"run": 'for f in a b; do echo "$f"; done'}]}})
    assert cwe.check_workflow(wf) == []


def test_while_read_variable(tmp_path):
    """`while IFS= read -r d; do ... "$d"` 必须被识别为赋值。

    第一版正则把 `d` 当成 `-r` 的参数吃掉了，于是把它误报成未定义。
    """
    wf = write_wf(tmp_path, {"j": {"steps": [
        {"run": 'while IFS= read -r d; do echo "$d"; done < list.txt'}]}})
    assert cwe.check_workflow(wf) == []


def test_read_with_option_argument(tmp_path):
    wf = write_wf(tmp_path, {"j": {"steps": [
        {"run": "while read -d '' path; do echo \"$path\"; done"}]}})
    assert cwe.check_workflow(wf) == []


def test_builtin_runner_vars_are_visible(tmp_path):
    wf = write_wf(tmp_path, {"j": {"steps": [
        {"run": 'echo "$RUNNER_TEMP $GITHUB_ENV $HOME $GITHUB_RUN_ID"'}]}})
    assert cwe.check_workflow(wf) == []


@pytest.mark.parametrize("expr", [
    '${UNSET:-fallback}', '${UNSET-fallback}', '${UNSET:+alt}', '${UNSET+alt}',
    '${UNSET:?message}', '${UNSET?message}',
])
def test_protected_parameter_expansion_is_safe_at_that_occurrence(tmp_path, expr):
    """这些展开不触发 nounset；只保护当前这一处，不声明后续可见。"""
    wf = write_wf(tmp_path, {"j": {"steps": [{"run": 'echo "%s"' % expr}]}})
    assert cwe.check_workflow(wf) == []


@pytest.mark.parametrize("expr", ['${UNSET#prefix}', '${UNSET%suffix}',
                                  '${UNSET/pat/repl}'])
def test_non_default_parameter_operations_still_require_variable(tmp_path, expr):
    """#/%/替换不是默认值保护，set -u 下仍要求变量存在。"""
    wf = write_wf(tmp_path, {"j": {"steps": [{"run": 'echo "%s"' % expr}]}})
    assert names(cwe.check_workflow(wf)) == ["UNSET"]


def test_bare_unset_is_reported(tmp_path):
    wf = write_wf(tmp_path, {"j": {"steps": [{"run": 'echo "$NOPE"'}]}})
    assert names(cwe.check_workflow(wf)) == ["NOPE"]


def test_actions_expression_is_not_a_shell_var(tmp_path):
    """${{ github.sha }} 由 Actions 展开，不是 shell 变量。"""
    wf = write_wf(tmp_path, {"j": {"steps": [
        {"run": 'echo "${{ github.sha }}"'}]}})
    assert cwe.check_workflow(wf) == []


def test_env_expression_referencing_undefined_is_reported(tmp_path):
    """${{ env.X }} 未定义会展开成空串，静默改变行为。"""
    wf = write_wf(tmp_path, {"j": {"steps": [
        {"name": "dl", "uses": "actions/download-artifact@v4",
         "with": {"path": "${{ env.MISSING }}"}}]}})
    issues = cwe.check_workflow(wf)
    assert codes(issues) == ["ENV_EXPR_UNDEFINED"]
    assert names(issues) == ["MISSING"]


def test_env_expression_defined_at_job_level_is_ok(tmp_path):
    wf = write_wf(tmp_path, {"j": {
        "env": {"OK": "x"},
        "steps": [{"uses": "a/b@v1", "with": {"path": "${{ env.OK }}"}}]}})
    assert cwe.check_workflow(wf) == []


def test_positional_and_arithmetic_are_ignored(tmp_path):
    wf = write_wf(tmp_path, {"j": {"steps": [
        {"run": 'echo "$1"\nn=$((1+2))\necho "$n"'}]}})
    assert cwe.check_workflow(wf) == []


def test_jobs_do_not_share_github_env(tmp_path):
    """GITHUB_ENV 只在同一 job 内传递，跨 job 不可见。"""
    wf = write_wf(tmp_path, {
        "a": {"steps": [{"run": 'echo "X=1" >> "$GITHUB_ENV"'}]},
        "b": {"steps": [{"run": 'echo "$X"'}]},
    })
    assert names(cwe.check_workflow(wf)) == ["X"]


# --------------------------------------------------------------------------
# 真实 workflow 与 CLI
# --------------------------------------------------------------------------

def test_real_workflow_passes():
    issues = cwe.check_workflow(
        os.path.join(ROOT, ".github", "workflows", "merge-gate.yml"))
    assert issues == [], [str(i) for i in issues]


def test_cli_pass_on_repo():
    r = subprocess.run([sys.executable, TOOL, "--root", ROOT],
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    assert r.returncode == 0, r.stdout.decode()


def test_cli_fail_exit_1(tmp_path):
    wf = write_wf(tmp_path, {"j": {"steps": [{"run": 'echo "$NOPE"'}]}})
    r = subprocess.run([sys.executable, TOOL, "--workflow", wf],
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    assert r.returncode == 1
    assert b"ENV_USED_BEFORE_SET" in r.stdout


def test_cli_no_workflows_exit_2(tmp_path):
    r = subprocess.run([sys.executable, TOOL, "--root", str(tmp_path)],
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    assert r.returncode == 2
    assert b"NO_WORKFLOWS" in r.stdout


# --------------------------------------------------------------------------
# 第六轮复核的两个精确反例：整段集合分析错误
# --------------------------------------------------------------------------


def run_bash(script):
    """用真实 Bash `set -eu` 执行线性片段，核对检查器的结论方向。"""
    return subprocess.run(["/bin/bash", "-c", "set -eu\n" + script],
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def test_R_future_assignment_does_not_make_earlier_use_visible(tmp_path):
    """未来赋值不能提前满足当前引用。

    旧实现先 `_assigns(script)` 收集整段赋值，所以这个反例被判无问题；
    真实 Bash 在第一行就因 X 未绑定退出 1。
    """
    script = 'printf "%s" "$X"\nX=ok'
    wf = write_wf(tmp_path, {"j": {"steps": [{"run": script}]}})
    issues = cwe.check_workflow(wf)
    assert names(issues) == ["X"]
    assert run_bash(script).returncode != 0


def test_R_default_value_only_protects_current_occurrence(tmp_path):
    """`${X:-fallback}` 不给 X 赋值，后面的裸 `$X` 必须仍被报告。

    旧实现把 safe 名字从整段 uses 集合中减掉，一处安全引用会洗掉所有后续不安全引用。
    """
    script = 'printf "%s" "${X:-fallback}"\nprintf "%s" "$X"'
    wf = write_wf(tmp_path, {"j": {"steps": [{"run": script}]}})
    issues = cwe.check_workflow(wf)
    assert names(issues) == ["X"]
    assert run_bash(script).returncode != 0


def test_assignment_before_use_matches_real_bash(tmp_path):
    """阳性对照：先赋值再读取，检查器与真实 Bash 都通过。"""
    script = 'X=ok\nprintf "%s" "$X"'
    wf = write_wf(tmp_path, {"j": {"steps": [{"run": script}]}})
    assert cwe.check_workflow(wf) == []
    proc = run_bash(script)
    assert proc.returncode == 0
    assert proc.stdout == b"ok"


def test_same_line_use_before_assignment_is_reported(tmp_path):
    script = 'printf "%s" "$X"; X=ok'
    wf = write_wf(tmp_path, {"j": {"steps": [{"run": script}]}})
    assert names(cwe.check_workflow(wf)) == ["X"]
    assert run_bash(script).returncode != 0


def test_same_line_assignment_before_use_passes(tmp_path):
    script = 'X=ok; printf "%s" "$X"'
    wf = write_wf(tmp_path, {"j": {"steps": [{"run": script}]}})
    assert cwe.check_workflow(wf) == []
    assert run_bash(script).returncode == 0


def test_rhs_use_happens_before_assignment_effect(tmp_path):
    """`X=$X` 不能靠左侧 X= 给右侧的未绑定 X 放行。"""
    script = 'X=$X\nprintf "%s" "$X"'
    wf = write_wf(tmp_path, {"j": {"steps": [{"run": script}]}})
    assert names(cwe.check_workflow(wf)) == ["X"]
    assert run_bash(script).returncode != 0


def test_colon_equals_assigns_for_later_use(tmp_path):
    """`${X:=fallback}` 当前引用安全，并在展开后给后续 `$X` 赋值。"""
    script = 'printf "%s" "${X:=fallback}"\nprintf "%s" "$X"'
    wf = write_wf(tmp_path, {"j": {"steps": [{"run": script}]}})
    assert cwe.check_workflow(wf) == []
    assert run_bash(script).returncode == 0


def test_plain_minus_does_not_assign_for_later_use(tmp_path):
    script = 'printf "%s" "${X-fallback}"\nprintf "%s" "$X"'
    wf = write_wf(tmp_path, {"j": {"steps": [{"run": script}]}})
    assert names(cwe.check_workflow(wf)) == ["X"]
    assert run_bash(script).returncode != 0


def test_issue_reports_first_unsafe_line(tmp_path):
    script = 'echo ok\nprintf "%s" "${X:-fallback}"\nprintf "%s" "$X"'
    wf = write_wf(tmp_path, {"j": {"steps": [{"name": "x", "run": script}]}})
    issues = cwe.check_workflow(wf)
    assert len(issues) == 1
    assert "第 3 行" in issues[0].detail


def test_checker_documents_lexical_not_control_flow_guarantee():
    """保证边界必须出现在用户可见输出/模块文档里，不能让 PASS 冒充真实控制流证明。"""
    assert "线性词法顺序" in cwe.__doc__
    assert "不建模" in cwe.__doc__
    r = subprocess.run([sys.executable, TOOL, "--root", ROOT],
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    assert r.returncode == 0
    out = r.stdout.decode("utf-8")
    assert "线性词法" in out
    assert "不替代真实故障路径执行" in out


def test_indented_assignment_before_use_is_recognized(tmp_path):
    """YAML block 通常去掉公共缩进，但 shell 内部缩进行仍应按赋值处理。"""
    script = '  X=ok\n  printf "%s" "$X"'
    wf = write_wf(tmp_path, {"j": {"steps": [{"run": script}]}})
    assert cwe.check_workflow(wf) == []
    assert run_bash(script).returncode == 0


# --------------------------------------------------------------------------
# 第七轮复核：默认值 RHS 的引用不能被外层保护整体屏蔽
# --------------------------------------------------------------------------


def test_R_default_rhs_plain_variable_is_checked(tmp_path):
    """`${X:-$Y}`：X 未定义时会展开右侧，Y 仍受 set -u 约束。

    旧实现把整个 `${...}` 加进 braced_ranges，位于其中的 `$Y` 被跳过；检查器零问题，
    真实 Bash 报 Y: unbound variable。
    """
    script = 'printf "%s" "${X:-$Y}"'
    wf = write_wf(tmp_path, {"j": {"steps": [{"run": script}]}})
    issues = cwe.check_workflow(wf)
    assert names(issues) == ["Y"]
    assert run_bash(script).returncode != 0


def test_R_colon_equals_rhs_self_reference_checked_before_assignment(tmp_path):
    """`${X:=$X}`：右侧 X 必须在 `:=` 给外层 X 赋值之前展开。"""
    script = 'printf "%s" "${X:=$X}"'
    wf = write_wf(tmp_path, {"j": {"steps": [{"run": script}]}})
    issues = cwe.check_workflow(wf)
    assert names(issues) == ["X"]
    assert run_bash(script).returncode != 0


def test_default_rhs_defined_variable_positive_control(tmp_path):
    """阳性对照：先定义 Y，再用 `${X:-$Y}`，检查器与真实 Bash 都通过。"""
    script = 'Y=ok\nprintf "%s" "${X:-$Y}"'
    wf = write_wf(tmp_path, {"j": {"steps": [{"run": script}]}})
    assert cwe.check_workflow(wf) == []
    proc = run_bash(script)
    assert proc.returncode == 0
    assert proc.stdout == b"ok"


def test_default_rhs_multiple_plain_references_all_checked(tmp_path):
    script = 'A=ok\nprintf "%s" "${X:-$A-$B-$C}"'
    wf = write_wf(tmp_path, {"j": {"steps": [{"run": script}]}})
    assert names(cwe.check_workflow(wf)) == ["B", "C"]
    assert run_bash(script).returncode != 0


def test_colon_equals_assignment_happens_after_rhs_then_propagates(tmp_path):
    """RHS 引用已满足后，`:=` 才给 X 赋值；后续裸 X 可见。"""
    script = 'Y=ok\nprintf "%s" "${X:=$Y}"\nprintf "%s" "$X"'
    wf = write_wf(tmp_path, {"j": {"steps": [{"run": script}]}})
    assert cwe.check_workflow(wf) == []
    proc = run_bash(script)
    assert proc.returncode == 0
    assert proc.stdout == b"okok"


def test_non_assigning_default_does_not_propagate_even_with_rhs(tmp_path):
    script = 'Y=ok\nprintf "%s" "${X:-$Y}"\nprintf "%s" "$X"'
    wf = write_wf(tmp_path, {"j": {"steps": [{"run": script}]}})
    assert names(cwe.check_workflow(wf)) == ["X"]
    assert run_bash(script).returncode != 0


def test_nested_parameter_expansion_is_explicitly_unsupported(tmp_path):
    """不能可靠配对嵌套大括号时必须报未支持，不能静默 PASS。"""
    script = 'printf "%s" "${X:-${Y:-fallback}}"'
    wf = write_wf(tmp_path, {"j": {"steps": [{"run": script}]}})
    issues = cwe.check_workflow(wf)
    assert "ENV_EXPANSION_UNSUPPORTED" in codes(issues), [str(i) for i in issues]


def test_nested_parameter_expansion_remains_unsupported_when_inner_defined(tmp_path):
    """即使当前环境恰好可运行，也不超出静态检查器保证范围。"""
    script = 'Y=ok\nprintf "%s" "${X:-${Y}}"'
    wf = write_wf(tmp_path, {"j": {"steps": [{"run": script}]}})
    issues = cwe.check_workflow(wf)
    assert "ENV_EXPANSION_UNSUPPORTED" in codes(issues)
    assert run_bash(script).returncode == 0


def test_unsupported_issue_reports_line(tmp_path):
    script = 'echo ok\nprintf "%s" "${X:-${Y:-z}}"'
    wf = write_wf(tmp_path, {"j": {"steps": [{"run": script}]}})
    issues = [i for i in cwe.check_workflow(wf)
              if i.code == "ENV_EXPANSION_UNSUPPORTED"]
    assert len(issues) == 1
    assert "第 2 行" in issues[0].detail
