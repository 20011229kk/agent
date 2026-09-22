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
    '${UNSET:-fallback}', '${UNSET:?msg}', '${UNSET:=x}', '${UNSET#prefix}',
])
def test_default_value_forms_are_safe(tmp_path, expr):
    """带默认值的引用在 set -u 下不会炸，不该报。"""
    wf = write_wf(tmp_path, {"j": {"steps": [{"run": 'echo "%s"' % expr}]}})
    assert cwe.check_workflow(wf) == []


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
