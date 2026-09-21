"""validate_spec.py 的反例测试。

策略：把真实仓库的产物拷进临时目录作为"合规基准"，再逐项注入**单一**缺陷，
断言校验器以对应的失败码报错。这样测的是真实产物，而不是测试里另造一份理想输入。

先确认基准通过（否则后面的反例测试没有意义），再逐条破坏。
"""
import pathlib
import shutil
import subprocess
import sys

import pytest
import yaml

REPO = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "validate_spec.py"
SPEC = pathlib.Path(".doc/specs/qa-agents")
BASELINE = pathlib.Path("qa/metrics/pilot-baseline.yaml")

COPY_ENTRIES = [
    ".doc",
    "qa",
    "templates",
    "CODEOWNERS",
]


def run_validator(root):
    """返回 (exit_code, failure_codes)。"""
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(root), "--json"],
        capture_output=True,
        text=True,
    )
    import json

    payload = json.loads(proc.stdout)
    codes = [f["code"] for f in payload["failures"]]
    return proc.returncode, codes


@pytest.fixture()
def repo(tmp_path):
    """合规基准副本。"""
    for entry in COPY_ENTRIES:
        src = REPO / entry
        dst = tmp_path / entry
        if src.is_dir():
            shutil.copytree(src, dst)
        else:
            shutil.copy2(src, dst)
    return tmp_path


# --------------------------------------------------------------------------
# 基准必须先通过，否则所有反例测试都不可信
# --------------------------------------------------------------------------


def test_baseline_repo_passes(repo):
    code, codes = run_validator(repo)
    assert code == 0, "合规基准应通过，实际失败: {}".format(codes)
    assert codes == []


# --------------------------------------------------------------------------
# spec 三件套缺失
# --------------------------------------------------------------------------


@pytest.mark.parametrize("filename", ["requirements.md", "design.md", "tasks.md"])
def test_missing_spec_file_fails(repo, filename):
    (repo / SPEC / filename).unlink()
    code, codes = run_validator(repo)
    assert code == 1
    assert "SPEC_MISSING" in codes


# --------------------------------------------------------------------------
# requirements.md
# --------------------------------------------------------------------------


def test_missing_requirement_id_fails(repo):
    path = repo / SPEC / "requirements.md"
    text = path.read_text(encoding="utf-8")
    # 抹掉 R5 的标题，模拟漏写一条需求
    text = text.replace("## R5 失败必须归因", "## 失败必须归因")
    path.write_text(text, encoding="utf-8")
    code, codes = run_validator(repo)
    assert code == 1
    assert "REQ_ID_MISSING" in codes


def test_missing_shall_not_fails(repo):
    """只有正向义务、没有禁止性标准 —— 缺少反例约束。"""
    path = repo / SPEC / "requirements.md"
    text = path.read_text(encoding="utf-8").replace("SHALL NOT", "SHALL")
    path.write_text(text, encoding="utf-8")
    code, codes = run_validator(repo)
    assert code == 1
    assert "EARS_NEGATIVE_MISSING" in codes


def test_missing_counterexample_section_fails(repo):
    path = repo / SPEC / "requirements.md"
    text = path.read_text(encoding="utf-8").replace("反例验收", "验收补充")
    path.write_text(text, encoding="utf-8")
    code, codes = run_validator(repo)
    assert code == 1
    assert "COUNTEREXAMPLE_SECTION_MISSING" in codes


# --------------------------------------------------------------------------
# design.md
# --------------------------------------------------------------------------


def test_missing_design_section_fails(repo):
    path = repo / SPEC / "design.md"
    text = path.read_text(encoding="utf-8").replace("## 6. Testing Strategy", "## 6. 其他")
    path.write_text(text, encoding="utf-8")
    code, codes = run_validator(repo)
    assert code == 1
    assert "DESIGN_SECTION_MISSING" in codes


def test_missing_residual_risk_fails(repo):
    """残余风险被删掉 —— 会让人误以为门禁已经可信。"""
    path = repo / SPEC / "design.md"
    text = path.read_text(encoding="utf-8").replace("残余风险", "补充说明")
    path.write_text(text, encoding="utf-8")
    code, codes = run_validator(repo)
    assert code == 1
    assert "RESIDUAL_RISK_MISSING" in codes


# --------------------------------------------------------------------------
# tasks.md
# --------------------------------------------------------------------------


def test_task_without_status_marker_fails(repo):
    path = repo / SPEC / "tasks.md"
    text = path.read_text(encoding="utf-8")
    text += "\n- 这是一个没有状态标记的任务项\n"
    path.write_text(text, encoding="utf-8")
    code, codes = run_validator(repo)
    assert code == 1
    assert "TASK_MARKER_MISSING" in codes


def test_task_with_invalid_marker_fails(repo):
    path = repo / SPEC / "tasks.md"
    text = path.read_text(encoding="utf-8")
    text += "\n- [?] 状态标记非法的任务项\n"
    path.write_text(text, encoding="utf-8")
    code, codes = run_validator(repo)
    assert code == 1
    assert "TASK_MARKER_INVALID" in codes


def test_tasks_authority_claim_required(repo):
    path = repo / SPEC / "tasks.md"
    text = path.read_text(encoding="utf-8").replace("唯一任务状态来源", "任务清单")
    path.write_text(text, encoding="utf-8")
    code, codes = run_validator(repo)
    assert code == 1
    assert "TASK_AUTHORITY_MISSING" in codes


# --------------------------------------------------------------------------
# 试点前基线 —— R9 的核心反例
# --------------------------------------------------------------------------


def test_missing_baseline_file_fails(repo):
    (repo / BASELINE).unlink()
    code, codes = run_validator(repo)
    assert code == 1
    assert "BASELINE_MISSING" in codes


def test_missing_metric_without_reason_fails(repo):
    """status=MISSING 但不说明为什么采不到 —— R9 明确禁止。"""
    path = repo / BASELINE
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    del data["efficiency"]["manual_case_design_minutes_per_requirement"]["reason"]
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    code, codes = run_validator(repo)
    assert code == 1
    assert "BASELINE_MISSING_WITHOUT_REASON" in codes


def test_missing_metric_filled_with_zero_fails(repo):
    """用 0 冒充未采集到的基线 —— 这会让后续效果对比凭空产生"改善"。"""
    path = repo / BASELINE
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["efficiency"]["manual_case_design_minutes_per_requirement"]["value"] = 0
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    code, codes = run_validator(repo)
    assert code == 1
    assert "BASELINE_MISSING_WITH_VALUE" in codes


def test_measured_metric_without_source_fails(repo):
    """声称实测但不说数据来自哪里 —— 无法复核。"""
    path = repo / BASELINE
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    metric = data["efficiency"]["manual_case_design_minutes_per_requirement"]
    metric["status"] = "MEASURED"
    metric["value"] = 42
    metric.pop("source", None)
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    code, codes = run_validator(repo)
    assert code == 1
    assert "BASELINE_MEASURED_WITHOUT_SOURCE" in codes


def test_status_complete_while_metrics_missing_fails(repo):
    """把整份基线标成 COMPLETE，却仍有 MISSING 项 —— 会让指标被误认为可比。"""
    path = repo / BASELINE
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["meta"]["status"] = "COMPLETE"
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    code, codes = run_validator(repo)
    assert code == 1
    assert "BASELINE_STATUS_INCONSISTENT" in codes


def test_invalid_metric_status_fails(repo):
    path = repo / BASELINE
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["efficiency"]["manual_case_design_minutes_per_requirement"]["status"] = "PROBABLY"
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    code, codes = run_validator(repo)
    assert code == 1
    assert "BASELINE_STATUS_INVALID" in codes


def test_baseline_invalid_yaml_fails(repo):
    (repo / BASELINE).write_text("meta: [unclosed\n", encoding="utf-8")
    code, codes = run_validator(repo)
    assert code == 1
    assert "BASELINE_YAML_INVALID" in codes


# --------------------------------------------------------------------------
# 模板与受保护路径
# --------------------------------------------------------------------------


def test_template_invalid_yaml_fails(repo):
    """模板拷贝后无法解析 —— 使用者会在真实基线上踩坑。"""
    (repo / "templates" / "requirements.yaml").write_text(
        "// 这不是合法的 YAML 注释\nmeta: {\n", encoding="utf-8"
    )
    code, codes = run_validator(repo)
    assert code == 1
    assert "TEMPLATE_YAML_INVALID" in codes


def test_missing_codeowners_fails(repo):
    (repo / "CODEOWNERS").unlink()
    code, codes = run_validator(repo)
    assert code == 1
    assert "CODEOWNERS_MISSING" in codes


@pytest.mark.parametrize(
    "entry",
    ["/qa/baseline/", "/qa/plan/", "/scripts/gate_check.py", "/CODEOWNERS",
     "/evals/rubrics/", "/evals/fixtures/"],
)
def test_codeowners_missing_protected_entry_fails(repo, entry):
    """受保护路径从 CODEOWNERS 里消失 = 可信计算基失去强制点。"""
    path = repo / "CODEOWNERS"
    text = path.read_text(encoding="utf-8").replace(entry, "/tmp/irrelevant")
    path.write_text(text, encoding="utf-8")
    code, codes = run_validator(repo)
    assert code == 1
    assert "CODEOWNERS_ENTRY_MISSING" in codes


# --------------------------------------------------------------------------
# 单一缺陷只应产生对应失败，不应掩盖其他检查
# --------------------------------------------------------------------------


def test_single_defect_does_not_mask_other_checks(repo):
    """注入一个缺陷后，检查总数不应骤降（否则说明校验提前退出、漏检）。"""
    import json

    def checks_run(root):
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), "--root", str(root), "--json"],
            capture_output=True,
            text=True,
        )
        return json.loads(proc.stdout)["checks_run"]

    before = checks_run(repo)
    path = repo / SPEC / "tasks.md"
    path.write_text(
        path.read_text(encoding="utf-8") + "\n- [?] bad marker\n", encoding="utf-8"
    )
    after = checks_run(repo)
    assert after >= before, "注入缺陷后检查数下降，可能存在提前退出导致的漏检"
