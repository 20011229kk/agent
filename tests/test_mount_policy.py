"""isolation/mount_policy.py 的回归测试。

重点：外部复核在真实容器里实测出的两条绕过，必须作为**永久回归场景**留在这里：

  R1  --rw qa/baseline  → 把受保护的覆盖率分母重新挂成可写（当时写入成功、退出 0）
  R2  --rw ../outside   → 穿越到仓库外的同级目录写入（当时写入成功、退出 0）

另外覆盖：符号链接组件、仓库根重叠、rw 之间嵌套、白名单之外、目录不存在、
受保护项即便被写进白名单也要拒绝（双重否决）、镜像覆盖拒绝、CLI 退出码。
"""

import json
import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "isolation"))

import mount_policy as mp  # noqa: E402

TOOL = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "isolation", "mount_policy.py")


def make_policy(**overrides):
    policy = {
        "version": 1,
        "allowed_rw": ["qa/runs", "qa/cases"],
        "protected": ["qa/baseline", "scripts", "CODEOWNERS"],
        "image": {"reference": "localhost/qa-executor:0.1.0",
                  "allow_override": False},
        "network": {"mode": "none"},
    }
    policy.update(overrides)
    return policy


@pytest.fixture
def repo(tmp_path):
    for rel in ("qa/runs", "qa/cases", "qa/baseline", "scripts"):
        (tmp_path / rel).mkdir(parents=True)
    (tmp_path / "CODEOWNERS").write_text("* @someone\n")
    (tmp_path / "README.md").write_text("# x\n")
    return str(tmp_path)


def codes(rejections):
    return [code for _, code, _ in rejections]


# --------------------------------------------------------------------------
# 复核实测出的两条绕过 —— 必须被拒绝
# --------------------------------------------------------------------------

def test_R1_protected_baseline_remount_is_rejected(repo):
    accepted, rejections = mp.validate_rw(["qa/baseline"], make_policy(), repo)
    assert accepted == []
    assert codes(rejections) == ["PROTECTED_PATH"]


def test_R2_parent_traversal_is_rejected(repo):
    accepted, rejections = mp.validate_rw(["../outside"], make_policy(), repo)
    assert accepted == []
    assert codes(rejections) == ["OUTSIDE_REPO"]


def test_R2_variant_deep_traversal_back_into_repo_is_still_checked(repo):
    """../<repo名>/qa/baseline 规范化后回到仓库内，但仍命中受保护判定。

    只查字符串里有没有 ".." 是不够的：穿越出去再回来同样要按最终位置判定。
    """
    name = os.path.basename(repo)
    accepted, rejections = mp.validate_rw(
        ["../%s/qa/baseline" % name], make_policy(), repo)
    assert accepted == []
    assert codes(rejections) == ["PROTECTED_PATH"]


def test_R2_variant_traversal_back_to_allowed_is_accepted(repo):
    """穿越出去再回到白名单目录：位置合法就该放行，避免把判据写成"看见 .. 就拒"。"""
    name = os.path.basename(repo)
    accepted, rejections = mp.validate_rw(
        ["../%s/qa/runs" % name], make_policy(), repo)
    assert rejections == []
    assert [rel for rel, _ in accepted] == ["qa/runs"]


# --------------------------------------------------------------------------
# 其余拒绝路径
# --------------------------------------------------------------------------

def test_absolute_path_rejected(repo):
    accepted, rejections = mp.validate_rw([os.path.join(repo, "qa/runs")],
                                          make_policy(), repo)
    assert accepted == []
    assert codes(rejections) == ["ABSOLUTE_PATH"]


def test_repo_root_rejected(repo):
    accepted, rejections = mp.validate_rw(["."], make_policy(), repo)
    assert accepted == []
    assert codes(rejections) == ["REPO_ROOT"]


def test_parent_of_protected_rejected(repo):
    """把 qa 挂成可写等于把 qa/baseline 一起挂上。"""
    accepted, rejections = mp.validate_rw(["qa"], make_policy(), repo)
    assert accepted == []
    assert codes(rejections) == ["PROTECTED_PATH"]


def test_symlink_component_rejected_even_when_target_inside(repo):
    """链接目标在仓库内也拒绝：挂载跟随链接，策略匹配的是路径字符串。"""
    os.symlink(os.path.join(repo, "qa/runs"), os.path.join(repo, "link-inside"))
    policy = make_policy(allowed_rw=["qa/runs", "link-inside"])
    accepted, rejections = mp.validate_rw(["link-inside"], policy, repo)
    assert accepted == []
    assert codes(rejections) == ["SYMLINK_COMPONENT"]


def test_symlink_component_rejected_when_target_outside(repo, tmp_path):
    outside = tmp_path.parent / "outside-target"
    outside.mkdir(exist_ok=True)
    os.symlink(str(outside), os.path.join(repo, "qa/runs", "link-out"))
    accepted, rejections = mp.validate_rw(["qa/runs/link-out"],
                                          make_policy(), repo)
    assert accepted == []
    assert codes(rejections) == ["SYMLINK_COMPONENT"]


def test_not_in_allowlist_rejected(repo):
    accepted, rejections = mp.validate_rw(["README.md"], make_policy(), repo)
    assert accepted == []
    assert codes(rejections) == ["NOT_IN_ALLOWLIST"]


def test_missing_dir_rejected(repo):
    accepted, rejections = mp.validate_rw(["qa/runs/nope"], make_policy(), repo)
    assert accepted == []
    assert codes(rejections) == ["MISSING_DIR"]


def test_empty_value_rejected(repo):
    accepted, rejections = mp.validate_rw(["", None], make_policy(), repo)
    assert accepted == []
    assert codes(rejections) == ["EMPTY", "EMPTY"]


def test_overlapping_rw_rejected(repo):
    (os.path.join(repo, "qa/runs/sub"))
    os.mkdir(os.path.join(repo, "qa/runs/sub"))
    accepted, rejections = mp.validate_rw(["qa/runs", "qa/runs/sub"],
                                          make_policy(), repo)
    assert [rel for rel, _ in accepted] == ["qa/runs"]
    assert codes(rejections) == ["OVERLAPPING_RW"]


def test_duplicate_request_deduplicated_not_rejected(repo):
    accepted, rejections = mp.validate_rw(["qa/runs", "qa/runs"],
                                          make_policy(), repo)
    # 第二次与第一次重叠 → 命中 OVERLAPPING_RW；这是期望行为，重复请求不静默吞掉
    assert [rel for rel, _ in accepted] == ["qa/runs"]
    assert codes(rejections) == ["OVERLAPPING_RW"]


def test_protected_wins_over_allowlist(repo):
    """双重否决：受保护项被误写进 allowed_rw 也必须拒绝。"""
    policy = make_policy(allowed_rw=["qa/runs", "qa/baseline"])
    accepted, rejections = mp.validate_rw(["qa/baseline"], policy, repo)
    assert accepted == []
    assert codes(rejections) == ["PROTECTED_PATH"]


def test_nested_path_inside_allowed_accepted(repo):
    os.mkdir(os.path.join(repo, "qa/runs/2026-09-21"))
    accepted, rejections = mp.validate_rw(["qa/runs/2026-09-21"],
                                          make_policy(), repo)
    assert rejections == []
    assert [rel for rel, _ in accepted] == ["qa/runs/2026-09-21"]


def test_multiple_rejections_all_reported(repo):
    accepted, rejections = mp.validate_rw(
        ["qa/baseline", "../outside", "README.md"], make_policy(), repo)
    assert accepted == []
    assert codes(rejections) == ["PROTECTED_PATH", "OUTSIDE_REPO",
                                 "NOT_IN_ALLOWLIST"]


def test_accepted_order_preserved(repo):
    accepted, rejections = mp.validate_rw(["qa/cases", "qa/runs"],
                                          make_policy(), repo)
    assert rejections == []
    assert [rel for rel, _ in accepted] == ["qa/cases", "qa/runs"]


# --------------------------------------------------------------------------
# 镜像
# --------------------------------------------------------------------------

def test_image_defaults_to_policy_reference():
    ref, rej = mp.resolve_image(make_policy(), None)
    assert ref == "localhost/qa-executor:0.1.0"
    assert rej is None


def test_image_same_value_is_not_an_override():
    ref, rej = mp.resolve_image(make_policy(), "localhost/qa-executor:0.1.0")
    assert ref == "localhost/qa-executor:0.1.0"
    assert rej is None


def test_image_override_denied_by_default():
    ref, rej = mp.resolve_image(make_policy(), "docker.io/library/alpine:3.20")
    assert ref is None
    assert rej[0] == "IMAGE_OVERRIDE_DENIED"


def test_image_override_allowed_when_policy_says_so():
    policy = make_policy(image={"reference": "a:1", "allow_override": True})
    ref, rej = mp.resolve_image(policy, "b:2")
    assert ref == "b:2"
    assert rej is None


# --------------------------------------------------------------------------
# 策略加载
# --------------------------------------------------------------------------

def test_load_policy_missing_file(tmp_path):
    with pytest.raises(mp.PolicyError):
        mp.load_policy(str(tmp_path / "nope.yaml"))


@pytest.mark.parametrize("bad", [
    "allowed_rw: []\nprotected: [a]\nimage: {reference: x}\nnetwork: {mode: none}\n",
    "allowed_rw: [a]\nprotected: []\nimage: {reference: x}\nnetwork: {mode: none}\n",
    "allowed_rw: [a]\nprotected: [b]\nimage: {}\nnetwork: {mode: none}\n",
    "allowed_rw: [a]\nprotected: [b]\nimage: {reference: x}\nnetwork: {}\n",
    "allowed_rw: [a]\nprotected: [b]\nimage: {reference: x}\n",
    "- not-a-mapping\n",
])
def test_load_policy_rejects_incomplete(tmp_path, bad):
    p = tmp_path / "policy.yaml"
    p.write_text(bad)
    with pytest.raises(mp.PolicyError):
        mp.load_policy(str(p))


def test_real_policy_file_loads_and_covers_protected_paths():
    """仓库里那份真实策略必须可加载，且受保护清单不能漏掉关键项。"""
    policy = mp.load_policy()
    for must in ("qa/baseline", "qa/plan", "scripts", "isolation", ".doc",
                 ".github", "CODEOWNERS", "Makefile"):
        assert must in policy["protected"], must
    assert policy["network"]["mode"] == "none"
    assert policy["image"]["allow_override"] is False


def test_real_policy_allowlist_does_not_intersect_protected():
    """白名单与受保护清单不得有交集，也不得互为父子。"""
    policy = mp.load_policy()
    root = mp.repo_root()
    for allowed in policy["allowed_rw"]:
        a = mp._norm(root, allowed)
        for prot in policy["protected"]:
            p = mp._norm(root, prot)
            assert not mp._inside(p, a), (allowed, prot)
            assert not mp._inside(a, p), (allowed, prot)


# --------------------------------------------------------------------------
# 参数生成与 manifest
# --------------------------------------------------------------------------

def test_build_args_shape(repo):
    accepted, _ = mp.validate_rw(["qa/runs"], make_policy(), repo)
    args = mp.build_args(accepted, make_policy(), "img:1", repo, ["echo", "hi"])
    assert args[:2] == ["run", "--rm"]
    assert "--network=none" in args
    assert "%s:/work:ro" % repo in args
    assert "%s/qa/runs:/work/qa/runs:rw" % repo in args
    assert args[-3:] == ["img:1", "echo", "hi"]
    # 只读基础挂载必须在可写挂载之前，否则 podman 会用后者覆盖前者的语义
    assert args.index("%s:/work:ro" % repo) < \
        args.index("%s/qa/runs:/work/qa/runs:rw" % repo)


def test_build_args_without_rw_has_only_readonly_mount(repo):
    args = mp.build_args([], make_policy(), "img:1", repo, ["true"])
    assert sum(1 for a in args if a.endswith(":rw")) == 0
    assert sum(1 for a in args if a.endswith(":ro")) == 1


def test_manifest_hashes_real_files():
    m = mp.manifest()
    assert m["sha256"]["isolation/mount-policy.yaml"]
    assert m["sha256"]["isolation/mount_policy.py"]
    assert len(m["sha256"]["isolation/mount_policy.py"]) == 64


# --------------------------------------------------------------------------
# CLI 退出码（门禁/脚本依赖这些码，必须锁住）
# --------------------------------------------------------------------------

def run_cli(args, cwd=None):
    return subprocess.run([sys.executable, TOOL] + args,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          cwd=cwd)


def test_cli_plan_success_writes_args(tmp_path):
    out = tmp_path / "args.txt"
    r = run_cli(["plan", "--rw", "qa/runs", "--out", str(out),
                 "--", "python", "-c", "print(1)"])
    assert r.returncode == 0, r.stderr.decode()
    lines = out.read_text().strip().split("\n")
    assert lines[0] == "run"
    assert lines[-3:] == ["python", "-c", "print(1)"]


def test_cli_plan_rejection_exit_65_and_no_args_file(tmp_path):
    out = tmp_path / "args.txt"
    r = run_cli(["plan", "--rw", "qa/baseline", "--out", str(out), "--", "true"])
    assert r.returncode == 65
    assert b"PROTECTED_PATH" in r.stderr
    assert not out.exists(), "被拒绝时不得留下可用的参数文件"


def test_cli_plan_requires_command(tmp_path):
    r = run_cli(["plan", "--rw", "qa/runs"])
    assert r.returncode == 65
    assert b"NO_COMMAND" in r.stderr


def test_cli_policy_error_exit_78(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("allowed_rw: []\n")
    r = run_cli(["plan", "--policy", str(bad), "--", "true"])
    assert r.returncode == 78
    assert b"POLICY_ERROR" in r.stderr


def test_cli_manifest_is_json():
    r = run_cli(["manifest"])
    assert r.returncode == 0
    data = json.loads(r.stdout.decode())
    assert "sha256" in data and "repo_root" in data


def test_cli_unknown_mode():
    r = run_cli(["nope"])
    assert r.returncode == 65


def test_cli_image_prints_policy_reference():
    r = run_cli(["image"])
    assert r.returncode == 0
    assert r.stdout.decode() == mp.load_policy()["image"]["reference"]
    # 验证脚本用它决定阳性对照镜像；带换行会污染 podman 参数
    assert not r.stdout.decode().endswith("\n")


def test_cli_image_policy_error_exit_78(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("image: {}\n")
    r = run_cli(["image", "--policy", str(bad)])
    assert r.returncode == 78
