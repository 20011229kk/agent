"""isolation/mount_policy.py 的回归测试。

重点：外部复核在真实容器里实测出的两条绕过，必须作为**永久回归场景**留在这里：

  R1  --rw qa/baseline  → 把受保护的覆盖率分母重新挂成可写（当时写入成功、退出 0）
  R2  --rw ../outside   → 穿越到仓库外的同级目录写入（当时写入成功、退出 0）

另外覆盖：符号链接组件、仓库根重叠、rw 之间嵌套、白名单之外、目录不存在、
受保护项即便被写进白名单也要拒绝（双重否决）、镜像覆盖拒绝、CLI 退出码。
"""

import json
import os
import shutil
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
    out = tmp_path / "args.json"
    r = run_cli(["plan", "--sep", "json", "--rw", "qa/runs", "--out", str(out),
                 "--", "python", "-c", "print(1)"])
    assert r.returncode == 0, r.stderr.decode()
    argv = json.loads(out.read_text())
    assert argv[0] == "run"
    assert argv[-3:] == ["python", "-c", "print(1)"]


# --------------------------------------------------------------------------
# 参数传递保真（外部复核实测出的假成功：多行命令被按行截断）
#
#   python -c "print('BEGIN')\nraise SystemExit(42)"
# 单行版本退出 42；当时的多行版本只打印 BEGIN 就退出 0 —— 参数内部的换行被当成了
# 行分隔符，raise SystemExit(42) 根本没执行。空参数也在按行读取时被跳过。
# 下面这组测试锁住 argv 保真，并明确禁止 plan 默认回到逐行输出。
# --------------------------------------------------------------------------

MULTILINE_CODE = "print('BEGIN')\nraise SystemExit(42)"

TRICKY_ARGS = [
    MULTILINE_CODE,
    "",                      # 空参数
    "a b\tc",                # 空格与制表符
    'quote"inside',
    "single'inside",
    "back\\slash",
    "trailing-newline\n",
    "多字节 中文",
]


def plan_argv(extra_cmd, cwd=None):
    r = run_cli(["plan", "--sep", "json", "--"] + extra_cmd)
    assert r.returncode == 0, r.stderr.decode()
    return json.loads(r.stdout.decode())


def test_plan_json_preserves_multiline_argument():
    argv = plan_argv(["python", "-c", MULTILINE_CODE])
    assert argv[-1] == MULTILINE_CODE
    assert "\n" in argv[-1]


def test_plan_json_preserves_every_tricky_argument():
    argv = plan_argv(["sh", "-c"] + TRICKY_ARGS)
    assert argv[-len(TRICKY_ARGS):] == TRICKY_ARGS


def test_plan_json_preserves_empty_argument_position():
    """空参数必须保留在原位置，不能被"跳过空行"吞掉。"""
    argv = plan_argv(["tool", "", "tail"])
    assert argv[-3:] == ["tool", "", "tail"]


def test_plan_nul_separated_roundtrip_is_lossless():
    """默认 NUL 分隔：按 \\0 切回来必须与原参数逐个相等。"""
    r = run_cli(["plan", "--", "python", "-c", MULTILINE_CODE, "", "x y"])
    assert r.returncode == 0, r.stderr.decode()
    raw = r.stdout.decode()
    assert raw.endswith("\0")
    parts = raw.split("\0")[:-1]
    assert parts[-4:] == ["-c", MULTILINE_CODE, "", "x y"]


def test_plan_line_mode_is_lossy_and_therefore_not_the_default():
    """逐行模式会丢信息，所以它不能是默认值，也不能被用于执行。

    这条测试**断言缺陷存在于逐行模式**，用来说明为什么默认值必须是 NUL：
    多行参数在逐行输出里会变成两行，无法还原成一个参数。
    """
    r_line = run_cli(["plan", "--sep", "line", "--", "python", "-c",
                      MULTILINE_CODE])
    assert r_line.returncode == 0
    lines = r_line.stdout.decode().rstrip("\n").split("\n")
    assert lines[-2:] == ["print('BEGIN')", "raise SystemExit(42)"], \
        "逐行模式确实会把一个参数拆成两行——这正是不能用它执行的原因"

    r_default = run_cli(["plan", "--", "python", "-c", MULTILINE_CODE])
    assert "\0" in r_default.stdout.decode(), "默认必须是 NUL 分隔"


def test_run_mode_execs_podman_with_exact_argv(monkeypatch):
    """run 模式必须把参数原样交给 podman，且用镜像 Id 而不是标签启动。"""
    captured = {}

    def fake_inspect(ref):
        return mp.load_policy()["image"]["digest"], "sha256:deadbeefcafe", None

    def fake_exec(argv):
        captured["argv"] = argv
        return 0

    rc = mp.cmd_run(["--", "python", "-c", MULTILINE_CODE, ""],
                    inspector=fake_inspect, execer=fake_exec)
    assert rc == 0
    argv = captured["argv"]
    assert argv[0] == "podman"
    assert argv[-2:] == [MULTILINE_CODE, ""]
    # 按不可变 Id 启动，不按可变标签
    assert "sha256:deadbeefcafe" in argv
    assert mp.load_policy()["image"]["reference"] not in argv


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


# --------------------------------------------------------------------------
# 运行时镜像身份绑定（外部复核 P2：C0 核对了镜像，普通执行仍按可变标签启动）
# --------------------------------------------------------------------------

def test_runtime_image_rejects_digest_mismatch():
    """同名标签被重新构建/移动后，普通执行路径必须拒绝，而不是静默换镜像。"""
    def fake_inspect(ref):
        return "sha256:0000000000000000", "sha256:someid", None

    image_id, info, rej = mp.resolve_runtime_image(mp.load_policy(), None,
                                                   fake_inspect)
    assert image_id is None and info is None
    assert rej[0] == "IMAGE_DIGEST_MISMATCH"


def test_runtime_image_rejects_missing_image():
    def fake_inspect(ref):
        return None, None, "no such image"

    image_id, info, rej = mp.resolve_runtime_image(mp.load_policy(), None,
                                                   fake_inspect)
    assert image_id is None
    assert rej[0] == "IMAGE_NOT_FOUND"


def test_runtime_image_rejects_policy_without_digest(tmp_path):
    """策略没记 digest 就不能执行：否则运行时身份无从确认。"""
    policy = make_policy(image={"reference": "x:1", "allow_override": False})
    image_id, info, rej = mp.resolve_runtime_image(policy, None,
                                                   lambda ref: ("d", "i", None))
    assert image_id is None
    assert rej[0] == "IMAGE_DIGEST_UNSET"


def test_runtime_image_accepts_matching_digest_and_returns_id():
    expected = mp.load_policy()["image"]["digest"]

    def fake_inspect(ref):
        return expected, "sha256:theid", None

    image_id, info, rej = mp.resolve_runtime_image(mp.load_policy(), None,
                                                   fake_inspect)
    assert rej is None
    assert image_id == "sha256:theid"
    assert info["digest"] == expected


def test_runtime_image_override_denied_before_inspect():
    """镜像覆盖在核对之前就被拒绝，不应该去 inspect 一个不被允许的镜像。"""
    called = []

    def fake_inspect(ref):
        called.append(ref)
        return "d", "i", None

    image_id, info, rej = mp.resolve_runtime_image(
        mp.load_policy(), "docker.io/library/alpine:3.20", fake_inspect)
    assert image_id is None
    assert rej[0] == "IMAGE_OVERRIDE_DENIED"
    assert called == []


def test_run_mode_refuses_on_digest_mismatch_without_exec():
    """digest 不符时绝不能启动容器。"""
    execed = []

    rc = mp.cmd_run(["--", "true"],
                    inspector=lambda ref: ("sha256:wrong", "sha256:id", None),
                    execer=lambda argv: execed.append(argv))
    assert rc == 65
    assert execed == [], "digest 不符还启动了容器"


def test_inspect_image_parses_digest_and_id(monkeypatch):
    class Done:
        returncode = 0
        stdout = b"sha256:aaa\tsha256:bbb\n"
        stderr = b""

    monkeypatch.setattr(mp.subprocess, "run", lambda *a, **k: Done())
    digest, image_id, err = mp.inspect_image("x:1")
    assert (digest, image_id, err) == ("sha256:aaa", "sha256:bbb", None)


def test_inspect_image_reports_failure(monkeypatch):
    class Fail:
        returncode = 125
        stdout = b""
        stderr = b"no such image\n"

    monkeypatch.setattr(mp.subprocess, "run", lambda *a, **k: Fail())
    digest, image_id, err = mp.inspect_image("x:1")
    assert digest is None and image_id is None
    assert "no such image" in err


def test_inspect_image_handles_unparsable_output(monkeypatch):
    class Weird:
        returncode = 0
        stdout = b"only-one-field\n"
        stderr = b""

    monkeypatch.setattr(mp.subprocess, "run", lambda *a, **k: Weird())
    digest, image_id, err = mp.inspect_image("x:1")
    assert image_id is None and "无法解析" in err


# --------------------------------------------------------------------------
# 端到端：需要 podman，缺 podman 时跳过而不是假装通过
# --------------------------------------------------------------------------

RUNNER = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "isolation", "run-isolated.sh")
HAS_PODMAN = shutil.which("podman") is not None
e2e = pytest.mark.skipif(not HAS_PODMAN, reason="需要 podman")


def run_wrapper(cmd, timeout=180):
    return subprocess.run(["zsh", RUNNER, "--"] + cmd,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          timeout=timeout)


@e2e
def test_e2e_single_line_failure_control():
    """阳性对照：单行失败必须退出 42。控制先过，多行结论才有意义。"""
    r = run_wrapper(["python", "-c", "print('BEGIN'); raise SystemExit(42)"])
    assert b"BEGIN" in r.stdout
    assert r.returncode == 42, r.stderr.decode()


@e2e
def test_e2e_multiline_failure_actually_runs():
    """回归：多行命令的失败逻辑必须真的执行（旧实现只打印 BEGIN 就退出 0）。"""
    r = run_wrapper(["python", "-c", MULTILINE_CODE])
    assert b"BEGIN" in r.stdout
    assert r.returncode == 42, \
        "多行参数被截断：失败逻辑没执行却返回 %d" % r.returncode


@e2e
def test_e2e_empty_argument_preserved():
    """回归：空参数必须保留在原位置（旧实现按行读取时被跳过）。"""
    r = run_wrapper(["python", "-c",
                     "import sys; print(repr(sys.argv[1:]))", "", "tail"])
    assert b"['', 'tail']" in r.stdout, r.stdout.decode()


@e2e
def test_e2e_quoting_and_spaces_preserved():
    r = run_wrapper(["python", "-c", "import sys; print(repr(sys.argv[1:]))",
                     "a b\tc", 'quote"inside', "back\\slash"])
    out = r.stdout.decode()
    assert "a b\\tc" in out and 'quote"inside' in out and "back\\\\slash" in out
