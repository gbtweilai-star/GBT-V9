# brain.py —— decide(): 统一前置分诊层
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 纪律:
#   - Laya 只做低风险/有限选项的候选决策; 不生成解释、不确认漏洞、不审批
#   - score 是业务评分, 不是置信度; choice 无分数 → confidence_kind="unknown", 回退
#   - 未校准/越界/低置信 → 确定性规则 → inference.runtime
#   - 高风险 gate 永远由本地策略强制, Laya/LLM 都不能替代审批

P_ACCEPT_YES = float(os.environ.get("LAYER_P_ACCEPT_YES", 0.95))
P_ACCEPT_NO  = float(os.environ.get("LAYER_P_ACCEPT_NO", 0.05))


def _eligible_for_laya(req) -> bool:
    if req.risk == "high":                      return False
    if req.task_type not in CALIBRATED_TYPES:   return False   # 未校准 → 不自动采纳
    if req.needs_reason or req.needs_multi_step:return False
    if req.option_cardinality > LAYA_MAX_OPTIONS:return False  # 高基数受 token 预算限制
    return req.task_type in ("route", "priority", "yesno")


def decide(self, req):
    """req: task_type, state, questions, allowed_values, risk, trace_id"""
    path, reason, raw, confidence, kind = None, None, None, None, "unknown"

    if not _eligible_for_laya(req):
        return self._llm_decide(req, reason="not_laya_eligible")

    try:
        raw = self.router.predict(req.state, req.questions)      # Laya
        res = _validate_laya_output(raw, req)                    # 越界/格式校验
        confidence, kind = _calibrated_confidence(res, req)
        if res.valid and _passes_policy_threshold(res, confidence, kind, req):
            answer, path = res.answer, "laya"
        else:
            answer, path, reason = self._rules_then_llm(req, "low_or_unknown_confidence")
    except Exception as exc:
        answer, path, reason = self._rules_then_llm(req, _safe_error_code(exc))

    _write_decision_audit(                                       # 审计可回溯
        trace_id=req.trace_id, task_type=req.task_type,
        input_hash=_hash_redacted(req.state), laya_result=_redact(raw),
        confidence=confidence, confidence_kind=kind,
        used_path=path, fallback_reason=reason,
        model_version=_model_version(), policy_version=_policy_version())

    return {"answer": answer, "used_router": path == "laya",
            "router_model": _model_version() if raw else None,
            "confidence": confidence, "confidence_kind": kind,
            "used_path": path, "fallback_reason": reason,
            "trace_id": req.trace_id}


def _rules_then_llm(self, req, why):
    """回退顺序固定: 确定性规则 → inference.runtime(vLLM→网关→Ollama)"""
    a = self._deterministic_rules(req)
    if a is not None:
        return a, "rules", why
    return self._llm_decide(req, reason=why), "llm", why
