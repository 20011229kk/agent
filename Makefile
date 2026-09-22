# QA Agent Suite —— 本地检查入口
#
# 重要：`make gate` 只是**本地自检**，不是门禁。
# 合并门禁是受保护分支的 CI required check，且只认 CI 从可信执行的原始产物重新生成的那份
# merge-gate-report。理由见 docs/protected-paths.md。
#
# 解释器：固定用系统 python3（3.9.6，已自带 PyYAML 与 pytest）。
# 不用 homebrew 的 3.14——它没有 pytest，改用它需要额外安装依赖。

PY := /usr/bin/python3
ROOT := $(shell pwd)

.PHONY: help check validate validate-agents validate-workflow test gate isolation-verify clean

help:
	@echo "make check             结构校验 + 角色配置校验 + 全部单测（提交前跑这个）"
	@echo "make validate          只跑结构校验"
	@echo "make validate-agents   只校验 .kiro/agents/*.json"
	@echo "make test              只跑单测"
	@echo "make gate              本地门禁自检（非门禁，仅参考）"
	@echo "make isolation-verify  隔离边界验证 C0–C12（需要 podman）"
	@echo "make clean             清理临时产物"

check: validate validate-agents validate-workflow test

validate-workflow:
	@echo "==> check_workflow_env"
	@$(PY) scripts/check_workflow_env.py --root $(ROOT)

validate:
	@echo "==> validate_spec"
	@$(PY) scripts/validate_spec.py --root $(ROOT)

validate-agents:
	@echo "==> validate_agents"
	@$(PY) scripts/validate_agents.py --root $(ROOT)

isolation-verify:
	@echo "==> verify-isolation (C0–C12)"
	@zsh isolation/verify-isolation.sh

test:
	@echo "==> pytest"
	@$(PY) -m pytest tests/ -q

# DIFF 可选：make gate DIFF=/tmp/candidate.diff 会把门槛变化提示并入报告
DIFF ?=

gate:
	@echo "==> 追溯与覆盖"
	@$(PY) scripts/trace_matrix.py --root $(ROOT) || true
	@echo ""
	@echo "==> 门槛变化提示"
	@if [ -n "$(DIFF)" ]; then \
		$(PY) scripts/guard_scan.py --root $(ROOT) --diff-file "$(DIFF)" --with-waivers || true; \
	else \
		echo "  未提供 DIFF；用 make gate DIFF=/path/to.diff 启用门槛变化扫描"; \
	fi
	@echo ""
	@echo "==> 合并门禁判定"
	@if [ -n "$(DIFF)" ]; then \
		$(PY) scripts/gate_check.py --root $(ROOT) --diff-file "$(DIFF)" || true; \
	else \
		$(PY) scripts/gate_check.py --root $(ROOT) || true; \
	fi
	@echo ""
	@echo "提醒：本地结果仅供自检。合并门禁只认受保护 CI 从可信执行的原始产物"
	@echo "      重新生成的报告；本地运行读的是工作副本，可被候选任意改动。"

clean:
	@find . -type d -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null || true
	@find . -type d -name .pytest_cache -prune -exec rm -rf {} + 2>/dev/null || true
	@echo "cleaned"
