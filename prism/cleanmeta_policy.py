"""공통 관찰값 재사용·조건부 상세 호출·정책 초안의 결정적 평가."""
import hashlib
import json
import time
from datetime import datetime, timezone

from . import cleanmeta as CM
from .cleanmeta_policy_specs import ATOMS, EXTERNAL, GROUPS, MANDATORY, PROMPTS, RULES, VERSION

OPTIONS = {"A": "존재", "B": "부재", "C": "판단 불가"}


def defaults():
    return {"ok": True, "input_scope": "text", "policy_set": "common_v1", "version": VERSION, "prompts": dict(PROMPTS),
            "groups": [{"id": k, "code": v[0], "name": v[1], "legacy_vl": v[2]} for k, v in GROUPS.items()],
            "external_facts": [{"id": k, "name": v} for k, v in EXTERNAL.items()],
            "policy_count": len(RULES), "atom_count": len(ATOMS) + len(EXTERNAL),
            "first_question_count": len(GROUPS) + len(MANDATORY), "batch_limit": CM.MAX_QUESTIONS}


def validate(data):
    prompts = data.get("prompts", PROMPTS)
    if not isinstance(prompts, dict) or set(prompts) != set(PROMPTS):
        raise ValueError("1차·2차 기본 프롬프트를 모두 입력하세요")
    if any(not isinstance(v, str) or not v.strip() or len(v) > 8000 for v in prompts.values()):
        raise ValueError("각 프롬프트는 1~8,000자로 입력하세요")
    parts = ["body"] + (["title"] if data.get("title") else [])
    case = data.get("case_ref", "")
    if not isinstance(case, str) or len(case) > 300:
        raise ValueError("확인 자료의 대상·사건은 300자 이내로 입력하세요")
    facts = data.get("reviewed_facts", [])
    if not isinstance(facts, list) or len(facts) > len(EXTERNAL):
        raise ValueError("검수 확인값을 확인하세요")
    seen = set()
    for fact in facts:
        if not isinstance(fact, dict):
            raise ValueError("검수 확인값 형식이 올바르지 않습니다")
        aid = fact.get("atom_id")
        if not isinstance(aid, str) or aid not in EXTERNAL or aid in seen or fact.get("value") not in ("present", "absent", "unknown"):
            raise ValueError("미등록 또는 중복된 검수 확인 항목입니다")
        if not case.strip() or fact.get("subject_ref") != case:
            raise ValueError("검수 확인값의 대상·사건을 일치시켜 주세요")
        source = fact.get("source")
        if not isinstance(source, str) or not source.strip() or len(source) > 1000:
            raise ValueError("검수 확인값마다 출처를 1~1,000자로 입력하세요")
        seen.add(aid)
    return dict(prompts), facts, parts


def _question(text, prompt, options=OPTIONS):
    # 사용자 프롬프트는 실험 지침. 선택지·응답 검증·후처리 규칙은 코드에서 유지.
    return CM._choice(prompt + "\n\n이번 질문: " + text, options)


def first_questions(prompts):
    questions = {"route." + k: _question(v[1] + "과 관련하여 검토할 신호가 있는가? 음주·흡연·성인 주제 등 별도 연령 조건도 관련 신호에 포함하세요.", prompts["screen"],
                 {"A": "관련 있음", "B": "관련 없음", "C": "판단 불가"}) for k, v in GROUPS.items()}
    questions.update({k: _question(ATOMS[k][1], prompts["screen"]) for k in MANDATORY})
    return questions


def detail_questions(groups, spans, prompts, include_mandatory=False):
    rules = [r for r in RULES if r["group"] in groups]
    aids = sorted({a for r in rules for a in r["requires"] if a in ATOMS})
    evidence = {chr(65 + i): s["id"] + ": " + s["text"] for i, s in enumerate(spans)}
    evidence["Z"] = "관련 원문 근거 없음"
    out = {}
    for aid in aids:
        if aid not in MANDATORY or include_mandatory:
            out[aid] = _question(ATOMS[aid][1], prompts["detail"])
        out[aid + "_evidence"] = _question(ATOMS[aid][0] + "의 존재를 직접 뒷받침하는 원문 구간은? 없으면 Z를 선택하세요.", prompts["detail"], evidence)
    for group in GROUPS:
        if group in groups:
            out["context." + group] = _question(GROUPS[group][1] + "에 대해 보도·교육·예방·비판·풍자·허구·지원 요청 등 예외를 검토할 실제 맥락이 있는가? 단순 표제나 면책 문구와 구분하세요.", prompts["detail"])
    for r in rules:
        if len(r["requires"]) > 1:
            labels = [ATOMS[a][0] if a in ATOMS else EXTERNAL[a] for a in r["requires"]]
            out["binding." + r["id"]] = _question("다음 조건이 모두 같은 대상·사건·행위에 결합되는가: " + " / ".join(labels) +
                ". 검수 확인값이 있으면 그 subject_ref와 원문 대상의 일치도 확인하세요. 다른 인물·사건이면 B, 확인할 수 없으면 C입니다.", prompts["detail"])
    return out


def _truth(answer, gate):
    if not answer or answer["selected_probability"] < gate:
        return "unknown"
    return {"A": "present", "B": "absent", "C": "unknown"}[answer["choice"]]


def evaluate(rule, observations, binding, context):
    """정책 조건의 세 값 AND. 불명과 맥락 예외를 거짓으로 취급하지 않는다."""
    values = [observations[a]["value"] for a in rule["requires"]]
    if "absent" in values:
        return "not_matched", "필수 조건 부재"
    if "unknown" in values:
        return "undetermined", "필수 조건 또는 근거 미확인"
    if len(values) > 1 and binding != "present":
        return "undetermined", "같은 대상·사건의 조건 결합 미확인"
    if rule["layer"] in ("common", "legal") and context != "absent":
        return "undetermined", "맥락 예외의 사람 검토 필요"
    return "matched", "실험 규칙의 조건 충족 · 실제 위반·조치 확정 아님"


def judge(data, mode, post, key=""):
    gate, price = CM.validate_input(data)
    prompts, facts, parts = validate(data)
    spans = CM.segments(data.get("title", ""), data["body"])
    state = {"source_segments": spans, "unverified_context": data.get("context", ""),
             "reviewed_facts": facts, "reviewed_case": data.get("case_ref", ""),
             "input_scope": "text", "requested_parts": parts,
             "instructions": "제목·본문 텍스트만 검사합니다. 자유 입력은 외부 사실의 검증 근거가 아닙니다."}
    answers, errors, calls = {}, {}, []
    started = time.monotonic()

    def request(stage, questions):
        items = list(questions.items())
        for offset in range(0, len(items), CM.MAX_QUESTIONS):
            batch = dict(items[offset:offset + CM.MAX_QUESTIONS])
            t = time.monotonic()
            call = {"stage": stage, "batch": offset // CM.MAX_QUESTIONS + 1, "questions": len(batch),
                    "question_ids": list(batch), "model": None, "input_tokens": None, "output_tokens": None, "status": "failed"}
            calls.append(call)
            try:
                response = post({"model": CM.selected_model(data), "state": state, "questions": batch}, key)
                if not isinstance(response, dict):
                    raise ValueError("응답 객체 누락")
                usage = response.get("usage") if isinstance(response.get("usage"), dict) else {}
                call.update(model=response.get("model") if isinstance(response.get("model"), str) else None,
                            input_tokens=CM._usage(usage.get("input_tokens")), output_tokens=CM._usage(usage.get("output_tokens")))
                raw = response.get("answers") if isinstance(response.get("answers"), dict) else {}
                for qid, question in batch.items():
                    try:
                        answers[qid] = CM._answer(raw.get(qid), question)
                    except ValueError as exc:
                        errors[qid] = str(exc)
                call["status"] = "invalid" if any(q in errors for q in batch) else "ok"
            except Exception as exc:
                errors.update({qid: "모델 요청 실패 (" + type(exc).__name__ + ")" for qid in batch})
            call["latency_ms"] = round((time.monotonic() - t) * 1000)

    first = first_questions(prompts)
    if mode == "all":
        request("전체 질문", {**first, **detail_questions(GROUPS, spans, prompts)})
        selected = set(GROUPS)
    else:
        request("1차 공통 선별", first)
        selected = {g for g in GROUPS if _truth(answers.get("route." + g), gate) != "absent"}
        selected.update(g for a, g in MANDATORY.items() if _truth(answers.get(a), gate) != "absent")
        confirmed = {f["atom_id"] for f in facts if f["value"] == "present"}
        selected.update(r["group"] for r in RULES if confirmed.intersection(r["requires"]))
        # 전체 요청 실패를 같은 입력의 상세 요청으로 자동 재시도하지 않는다.
        if selected and answers:
            request("2차 상세·근거", detail_questions(selected, spans, prompts))

    observations = {}
    for aid, (label, _) in ATOMS.items():
        answer = answers.get(aid)
        value = _truth(answer, gate)
        ev = answers.get(aid + "_evidence")
        evidence = None
        if ev and ev["choice"] != "Z" and ev["selected_probability"] >= gate:
            evidence = spans[ord(ev["choice"]) - 65]
        reason = "관찰 응답" if answer else "상세 검사 미요청"
        if value == "present" and evidence is None:
            value, reason = "unknown", "양성 응답의 유효한 원문 근거 미확인"
        observations[aid] = {"id": aid, "name": label, "value": value, "raw_answer": answer,
                             "evidence": evidence, "evidence_status": "model_observation", "reason": reason,
                             "selected_probability": answer["selected_probability"] if answer else None}
    reviewed = {f["atom_id"]: f for f in facts}
    for aid, label in EXTERNAL.items():
        f = reviewed.get(aid)
        observations[aid] = {"id": aid, "name": label, "value": f["value"] if f else "unknown",
                            "evidence": f, "evidence_status": "reviewer_confirmed" if f else "missing_external_evidence",
                            "reason": "운영자 확인값 · 출처의 자동 검증 없음" if f else "외부 확인 자료 미제공",
                            "selected_probability": None}
    policies = []
    for rule in RULES:
        if rule["group"] not in selected:
            status, reason = "not_matched", "공통 선별에서 관련 신호 미발견 · 실험 기준"
        else:
            status, reason = evaluate(rule, observations, _truth(answers.get("binding." + rule["id"]), gate),
                                      _truth(answers.get("context." + rule["group"]), gate))
        policies.append({**rule, "status": status, "reason": reason,
                         "action_candidate": rule["action"] if status != "not_matched" else "none",
                         "binding": answers.get("binding." + rule["id"]),
                         "context": answers.get("context." + rule["group"])})
    coverage_status = "failed" if not answers else "partial" if errors else "complete"
    core = [p for p in policies if p["layer"] in ("common", "legal")]
    raw_risk = any(_truth(answers.get(a), gate) == "present" for a in MANDATORY)
    common_status = ("review_required" if any(p["status"] == "matched" for p in core) or raw_risk else
                     "insufficient_evidence" if coverage_status != "complete" or any(p["status"] == "undetermined" for p in core) else
                     "no_common_issue_found")
    recommendation = {"review_required": "review", "insufficient_evidence": "hold", "no_common_issue_found": "pass_candidate"}[common_status]
    results = []
    for group, (code, name, vl) in GROUPS.items():
        ps = [p for p in policies if p["group"] == group]
        aids = sorted({a for p in ps for a in p["requires"]})
        results.append({"type": group, "code": code, "name": name, "legacy_vl": vl, "route": answers.get("route." + group),
                        "recommendation": "review" if any(p["status"] == "matched" for p in ps) else "hold" if any(p["status"] == "undetermined" for p in ps) else "pass_candidate",
                        "detail_status": "requested" if group in selected else "not_required", "policies": ps,
                        "details": [{**observations[a], "label": {"present": "존재", "absent": "부재", "unknown": "판단 불가"}[observations[a]["value"]]} for a in aids],
                        "reasons": [], "errors": {}, "evidence": None})
    tokens = sum(c["input_tokens"] for c in calls) if all(c["input_tokens"] is not None for c in calls) else None
    snapshot = {"title": data.get("title", ""), "body": data["body"], "context": data.get("context", ""),
                "parts": parts, "case_ref": data.get("case_ref", ""), "reviewed_facts": facts}
    prompt_hash = hashlib.sha256(json.dumps(prompts, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return {"mode": mode, "mode_label": "전체 질문" if mode == "all" else "공통 선별 후 상세", "status": coverage_status,
            "recommendation": recommendation, "common_status": common_status, "policy_set": "common_v1",
            "policy_version": VERSION, "question_version": VERSION, "decision_version": VERSION, "approval_status": "draft",
            "prompts": prompts, "prompt_hash": prompt_hash, "custom_prompts": prompts != PROMPTS,
            "input_scope": "text",
            "coverage": {"status": coverage_status, "requested_parts": parts, "assessed_parts": parts,
                         "scope": "제목·본문 텍스트 검사 결과 · 이미지·영상·음성 검증은 별도 체계"},
            "observations": list(observations.values()), "policies": policies, "results": results, "calls": calls, "errors": errors,
            "reviewed_facts": facts, "reviewer_supplied_context": data.get("context", ""), "segments": spans,
            "input_hash": hashlib.sha256(json.dumps(snapshot, sort_keys=True, ensure_ascii=False).encode()).hexdigest(),
            "created_at": datetime.now(timezone.utc).isoformat(), "gate": gate,
            "trace": {"requests": len(calls), "questions": sum(c["questions"] for c in calls), "input_tokens": tokens,
                      "latency_ms": round((time.monotonic() - started) * 1000),
                      "estimated_input_cost_usd": round(tokens * price / 1e6, 8) if tokens is not None and price is not None else None}}
