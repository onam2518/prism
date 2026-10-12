#!/usr/bin/env python3
"""주간 검수 생성 · 후보 CSV → 인텐트 부족분 우선 선정 → 원문 본문 수집 → 운영 등록·2인 배정.

  state   운영에서 누적 검수·배정·지난 주 계획을 받아 DIR/state.json 에 저장
  select  CSV 후보에서 주제별 N건 선정(지난 주 미충족 인텐트에 가중) · 본문·이미지 수집 · DIR/plan.json
  apply   plan 을 운영에 반영(기본 dry-run · --go 로 실행) · 이번 주 계획을 reports 에 저장

운영 접근은 `fly ssh console`(앱 prism-item) 경유다. 로컬 ~/.prism_supabase_* 는 옛 프로젝트라 쓰지 않는다.
"""
import argparse, base64, csv, collections as C, html.parser, json, os, re, subprocess, sys, time, urllib.request
from datetime import datetime, timedelta, timezone

APP = "prism-item"
TEAM_NAME = "DNM"
REPORT_KIND = "review_week_plan"      # reports(팀 스코프) · 주차별 부족분·연속 미충족 주수·배정 해시
PER_TOPIC = 100
MIN_REVIEWERS = 2                     # 2인 교차검수
ROSTER = {"뉴스": ["메리메리", "라이언"],            # mary.choi · lion.cho
          "연예": ["비니", "김덕호 기초검수"],        # binnie.jeong · kendrick.k
          "스포츠": ["애쉬", "김덕호 기초검수"]}      # ashe.an · kendrick.k
EXCLUDE_INTENTS = {"클립·하이라이트", "풀영상·본방", "라이브 중계", "연재"}   # TV 출처 전용 · 기사 후보에 없음
EXCLUDE_CAT_TIER1 = {"뉴스": ("Sports", "Entertainment"),   # 타 주제 담당 몫
                     "스포츠": ("Video Gaming",)}           # 누적 스포츠가 게임 위주였음
# 주제별 고유 인텐트(유입 비중이 낮아도 목표 10건 · dictionaries.INTENT_CATEGORIES_COMMON 의 기존 소속)
OWN = {"뉴스": ["속보·단신", "사건 경과 보도", "정책·행정", "노동·사회 이슈", "인물·사연", "국제·외교 보도", "트렌드·시장 분석"],
       "연예": ["열애·결혼·이혼", "컴백·신작 발매", "예고", "논란·해명", "방송 출연", "SNS·인플루언서", "캐스팅·공식소식",
               "이슈 편승(재조명)", "아티스트 소식", "라이브·공연", "예고편·티저", "인터뷰·비하인드", "교양·다큐"],
       "스포츠": ["경기 프리뷰", "경기 라인업·중계", "경기 결과·리뷰", "이적·계약 보도", "선수 분석 기사",
                "전술·데이터 분석", "부상·복귀 근황", "팬·응원 문화"]}
STREAK_WEIGHT = 0.5                   # 연속 미충족 1주마다 해당 인텐트 가중 +50%
CAP_CAT = 20                          # 1차 카테고리 상한(모자라면 2씩 완화)
KST = timezone(timedelta(hours=9))


def week_label(d=None):
    y, w, _ = (d or datetime.now(KST)).isocalendar()
    return "%d-W%02d" % (y, w)


# ── 운영 원격 실행 ─────────────────────────────────────────────────────────
REMOTE_HEAD = r'''
import json, sys, collections as C
from prism import serve, metaquery, reviewops
st = serve.get_store()
T = [t["id"] for t in st._get("teams", "select=id,name") if t.get("name") == %r][0]
def out(o): print("@@JSON@@" + json.dumps(o, ensure_ascii=False))
''' % TEAM_NAME

REMOTE_STATE = REMOTE_HEAD + r'''
week = sys.argv[1]
rows = st._get("contents", "select=hash,service,item_meta,quality_meta,source_url,source_fields,title&team_id=eq." + T)
fb = {r["content_hash"] for r in st._get("feedback", "select=content_hash&team_id=eq." + T)}
asg = st.assignees(T)
plan = st.get_report("%s", team=T) or {"weeks": []}
cur = next((w for w in plan["weeks"] if w["week"] == week), None)
batch = {h for hs in ((cur or {}).get("batch") or {}).values() for h in hs}
bodies = {}
hs = sorted(batch)
for i in range(0, len(hs), 80):
    for r in st._get("contents", "select=hash,body,image_urls&team_id=eq." + T + "&hash=in.(" + ",".join(hs[i:i + 80]) + ")"):
        bodies[r["hash"]] = {"body": r.get("body") or "", "images": r.get("image_urls") or []}
items = []
for r in rows:
    im = r.get("item_meta") or {}
    cats = [c if isinstance(c, str) else "%%s / %%s" %% (c.get("tier1"), c.get("tier2")) for c in im.get("content_category") or []]
    it = {"h": r["hash"], "svc": r.get("service"), "url": (r.get("source_url") or "").split("?")[0],
          "i": im.get("intent") or [], "c": cats, "reviewed": r["hash"] in fb, "assigned": r["hash"] in asg,
          "batch": r["hash"] in batch}
    if it["batch"]:
        it.update(key=(r.get("source_fields") or {}).get("item_unique_key") or r["hash"], title=r.get("title") or "",
                  summary=im.get("summary") or "", entities=" · ".join(im.get("entities") or []),
                  grade=(r.get("quality_meta") or {}).get("finalGrade") or "G", **bodies.get(r["hash"], {}))
    items.append(it)
out({"team": T, "members": st.team_members(T), "items": items, "plan": plan})
''' % REPORT_KIND

REMOTE_APPLY = REMOTE_HEAD + r'''
go = "go" in sys.argv
P = json.load(open("/tmp/rw_payload.json"))
res = []
for g in P["groups"]:
    judged = set()
    if g["drop"]:
        judged = {f["content_hash"] for f in st._get("feedback", "select=content_hash&team_id=eq." + T + "&content_hash=in.(" + ",".join(g["drop"]) + ")")}
    drop = [h for h in g["drop"] if h not in judged]          # 판정이 들어간 건은 지우지 않는다
    rows = [i["row"] for i in g["items"]]
    hashes = [metaquery._row_hash(r) for r in rows]
    known = (st.existing_hashes(hashes, team=T) or {}) if hashes else {}
    r = {"svc": g["svc"], "drop": len(drop), "kept_judged": len(judged), "new": len(rows), "already": len(known)}
    if go:
        for h in drop:
            st.remove_content(h, team=T)                         # 배정·초안까지 연쇄 삭제
        if rows:
            reg = metaquery.mq_register({"rows": rows}, team=T)
            if not reg.get("ok"):
                out({"error": reg.get("error"), "done": res}); sys.exit(1)
            r["images"] = sum(bool(st.set_image_urls(h, i["images"], team=T)) for h, i in zip(hashes, g["items"]) if i["images"])
            r["assigned"] = st.set_assignees_bulk(hashes, g["reviewers"], min_reviewers=P["min_reviewers"], team=T)
            reviewops._log_assign("주간검수생성 " + P["week"] + " " + g["svc"], "bulk", r["assigned"], g["reviewers"], P["min_reviewers"], T)
        g["final"] = g["keep"] + [h for h in g["drop"] if h in judged] + hashes
    res.append(r)
if go:
    plan = st.get_report("%s", team=T) or {"weeks": []}
    week = dict(P["report"], batch={g["svc"]: list(dict.fromkeys(g["final"])) for g in P["groups"]})
    plan["weeks"] = [w for w in plan["weeks"] if w["week"] != P["week"]][-11:] + [week]
    st.save_report("%s", plan, team=T)
out(res)
''' % (REPORT_KIND, REPORT_KIND)


def remote(code, *args, upload=None):
    urllib.request.urlopen("https://%s.fly.dev/" % APP, timeout=60).read()      # 자동 정지된 머신 깨우기
    time.sleep(5)
    if upload:
        p = subprocess.run(["fly", "ssh", "sftp", "shell", "-a", APP], input="put %s /tmp/rw_payload.json\n" % upload,
                           text=True, capture_output=True)
        if "written" not in p.stdout:
            sys.exit("업로드 실패: " + p.stdout[-300:] + p.stderr[-300:])
    b64 = base64.b64encode(code.encode()).decode()
    cmd = "sh -c 'echo %s | base64 -d > /tmp/rw.py && cd /app && PYTHONPATH=/app python3 /tmp/rw.py %s; rm -f /tmp/rw.py'" % (b64, " ".join(args))
    p = subprocess.run(["fly", "ssh", "console", "-a", APP, "-C", cmd], text=True, capture_output=True)
    line = next((l for l in p.stdout.splitlines() if l.startswith("@@JSON@@")), None)
    if not line:
        sys.exit("원격 실행 실패:\n" + (p.stdout + p.stderr)[-2000:])
    return json.loads(line[8:])


# ── 원문 본문 수집(v.daum.net) ────────────────────────────────────────────
class _Body(html.parser.HTMLParser):
    BLOCK = ("div", "section", "figure", "p", "figcaption", "li", "h3", "h4", "blockquote")

    def __init__(self):
        super().__init__(); self.on = 0; self.depth = 0; self.buf = []; self.paras = []; self.imgs = []

    def handle_starttag(self, t, a):
        a = dict(a)
        if t == "div" and "article_view" in (a.get("class") or ""):
            self.on, self.depth = 1, 0
        if not self.on:
            return
        if t in self.BLOCK: self.flush()
        if t == "div": self.depth += 1
        if t == "img" and a.get("data-org-src"): self.imgs.append(a["data-org-src"])
        if t == "br": self.buf.append("\n")

    def handle_endtag(self, t):
        if not self.on:
            return
        if t in self.BLOCK: self.flush()
        if t == "div":
            self.depth -= 1
            if self.depth < 0: self.on = 0

    def handle_data(self, d):
        if self.on: self.buf.append(d)

    def flush(self):
        t = re.sub(r"[ \t\r\f\v]+", " ", "".join(self.buf)).strip(); self.buf = []
        if t: self.paras.append(t)


def fetch_body(url):
    req = urllib.request.Request(url.split("?")[0], headers={"User-Agent": "Mozilla/5.0"})
    p = _Body(); p.feed(urllib.request.urlopen(req, timeout=20).read().decode("utf-8", "replace"))
    return {"body": "\n\n".join(p.paras), "images": list(dict.fromkeys(p.imgs))}


def poor(b):
    """본문 없는 영상 기사: 저작권·기자 줄을 뺀 본문 80자 미만 + 이미지 0장 → 검수 불가."""
    t = "\n".join(l for l in (b.get("body") or "").split("\n") if not re.search(r"Copyright|무단|@", l))
    return len(t.strip()) < 80 and not b.get("images")


# ── 선정 ─────────────────────────────────────────────────────────────────
def split(s):
    return [x.strip() for x in (s or "").split(" · ") if x.strip()]


def ents(r):
    return set(split(re.sub(r" 외 \d+$", "", r["entities"])))


def targets(svc, stream, n, carry):
    """목표 = 이번 주 유입 비중(10%↑ 30 · 3%↑ 20 · 그 외 10) · 대상 = 비중 1%↑ ∪ 주제 고유 ∪ 지난 주 미충족."""
    out = {}
    for i in (set(stream) | set(OWN[svc]) | set(carry)) - EXCLUDE_INTENTS:
        sh = stream[i] / max(1, n)
        if sh >= 0.01 or i in OWN[svc] or i in carry:
            out[i] = 30 if sh >= 0.10 else 20 if sh >= 0.03 else 10
    return out


def select(svc, pool, have, tgt, weight, fixed):
    pick = [r for r in pool if r["key"] in fixed or r["grade"] not in ("G", "")]      # 판정 들어간 건·유통불가 판정은 고정
    got = C.Counter(i for r in pick for i in r["i"])
    pc = C.Counter(r["c"][0] for r in pick)
    rest = [r for r in pool if r not in pick]
    cap = CAP_CAT

    def score(r):
        if pc[r["c"][0]] >= cap:
            return None
        d = sum(weight.get(i, 1) * max(0, tgt[i] - have[i] - got[i]) / tgt[i] for i in r["i"] if i in tgt)
        return d + 0.3 / (1 + pc[r["c"][0]])                  # 동점이면 덜 뽑힌 카테고리
    while len(pick) < PER_TOPIC and rest:
        best = max(rest, key=lambda r: score(r) if score(r) is not None else -1)
        if score(best) is None:
            cap += 2; continue
        rest.remove(best)
        if any(len(ents(best) & ents(x)) / max(1, len(ents(best) | ents(x))) >= 0.5 for x in pick):
            continue                                           # 같은 사건·같은 화보 중복
        pick.append(best); got.update(best["i"]); pc[best["c"][0]] += 1
    return pick, got


def cmd_state(a):
    os.makedirs(a.dir, exist_ok=True)
    st = remote(REMOTE_STATE, a.week)
    json.dump(st, open(os.path.join(a.dir, "state.json"), "w"), ensure_ascii=False)
    cnt = C.Counter((i["svc"], i["reviewed"], i["batch"]) for i in st["items"] if i["svc"] in ROSTER)
    print("저장:", os.path.join(a.dir, "state.json"), "· (주제, 검수완료, 이번주배치) 건수", dict(cnt))
    last = [w for w in st["plan"]["weeks"] if w["week"] != a.week][-1:]
    print("지난 계획:", last[0]["week"] if last else "없음(첫 주)")


def cmd_select(a):
    st = json.load(open(os.path.join(a.dir, "state.json")))
    cache_p = os.path.join(a.dir, "bodies.json")
    cache = json.load(open(cache_p)) if os.path.exists(cache_p) else {}
    prev = [w for w in st["plan"]["weeks"] if w["week"] != a.week][-1:]
    prev = prev[0] if prev else {"intents": {}}
    known = {i["url"] for i in st["items"] if i["url"] and not i["batch"]}
    rows = []
    for p in a.csv:
        rows += list(csv.DictReader(open(p, encoding="utf-8-sig")))
    for i in st["items"]:                                      # 이번 주 이미 등록한 배치도 후보(재편)
        if i["batch"]:
            rows.append({"content_unique_key": i["key"], "displayServiceName": i["svc"], "title": i["title"],
                         "grade": i["grade"], "summary": i["summary"], "entities": i["entities"],
                         "intent": " · ".join(i["i"]), "content_category": " · ".join(i["c"]), "url": i["url"], "_hash": i["h"]})
            cache.setdefault(i["key"], {"body": i.get("body", ""), "images": i.get("images", [])})
    reviewed_keys = {i["key"] for i in st["items"] if i["batch"] and i["reviewed"]}
    plan = {"week": a.week, "groups": [], "report": {"week": a.week, "intents": {}}}
    for svc in ROSTER:
        srows = [r for r in rows if r.get("displayServiceName") == svc]
        if not srows:
            print("!", svc, "후보 없음 · 건너뜀"); continue
        empty = [r["content_unique_key"] for r in srows if not (split(r["content_category"]) and split(r["intent"]) and r["entities"].strip())]
        if empty:                                              # 추출 누락 의심 · 선정에서는 빠지므로 보고만
            print("!", svc, "메타 빈 행 %d건(카테고리·인텐트·엔티티) 제외:" % len(empty), ", ".join(empty[:10]) + (" 외" if len(empty) > 10 else ""))
        stream = C.Counter(i for r in srows for i in split(r["intent"]))
        carry = {i: v for i, v in (prev["intents"].get(svc) or {}).items() if v.get("short", 0) > 0}
        tgt = targets(svc, stream, len(srows), carry)
        weight = {i: 1 + STREAK_WEIGHT * v.get("streak", 0) for i, v in carry.items()}
        # 이미 확보 = 검수 완료 + 이전 주차 배정 대기분(이번 주 배치는 후보라 제외)
        have = C.Counter(x for i in st["items"] if i["svc"] == svc and not i["batch"] and (i["reviewed"] or i["assigned"]) for x in i["i"])
        for _ in range(5):                                     # 본문 수집 → 영상 기사 제외 → 재선정, 안정될 때까지
            seen_k, seen_t, pool = set(), set(), []
            for r in srows:
                k, t = r["content_unique_key"], r["title"].strip()
                cats, ints = split(r["content_category"]), split(r["intent"])
                if k in seen_k or t in seen_t or not cats or not ints or not r["entities"].strip() or r["url"].split("?")[0] in known:
                    continue
                if cats[0].split(" / ")[0] in EXCLUDE_CAT_TIER1.get(svc, ()) or (k in cache and poor(cache[k])):
                    continue
                seen_k.add(k); seen_t.add(t)
                pool.append(dict(r, key=k, i=ints, c=cats))
            pick, got = select(svc, pool, have, tgt, weight, reviewed_keys)
            todo = [r for r in pick if r["key"] not in cache]
            if not todo:
                break
            for r in todo:
                try:
                    cache[r["key"]] = fetch_body(r["url"])
                except Exception as e:
                    cache[r["key"]] = {"body": "", "images": [], "err": str(e)[:80]}
                time.sleep(0.25)
            json.dump(cache, open(cache_p, "w"), ensure_ascii=False)
        ids = {m["name"]: m["id"] for m in st["members"]}
        missing = [n for n in ROSTER[svc] if n not in ids]
        if missing:
            sys.exit("팀 멤버 없음: %s · ROSTER 확인" % missing)
        batch_old = {r["content_unique_key"]: r["_hash"] for r in srows if r.get("_hash")}
        keys = {r["key"] for r in pick}
        plan["groups"].append({
            "svc": svc, "reviewers": [ids[n] for n in ROSTER[svc]],
            "keep": [h for k, h in batch_old.items() if k in keys],
            "drop": [h for k, h in batch_old.items() if k not in keys],
            "items": [{"row": {"id": r["key"], "service": svc, "title": r["title"], "body": cache[r["key"]]["body"],
                               "url": r["url"].split("?")[0], "grade": r["grade"], "summary": r["summary"],
                               "entities": re.sub(r" 외 \d+$", "", r["entities"]), "intent": r["intent"],
                               "category": r["content_category"], "source_fields": {"item_unique_key": r["key"]}},
                       "images": cache[r["key"]]["images"]} for r in pick if r["key"] not in batch_old]})
        rep = {}
        for i, t in sorted(tgt.items(), key=lambda kv: -(kv[1] - have[kv[0]])):
            short = max(0, t - have[i] - got[i])
            rep[i] = {"target": t, "have": have[i], "picked": got[i], "short": short,
                      "streak": (carry.get(i, {}).get("streak", 0) + 1) if short else 0}
        plan["report"]["intents"][svc] = rep
        g = plan["groups"][-1]
        print("== %s 후보 %d · 선정 %d (신규 %d · 유지 %d · 제외 %d)" % (svc, len(pool), len(pick), len(g["items"]), len(g["keep"]), len(g["drop"])))
        for i, v in rep.items():
            if v["short"] or i in carry:
                print("   %-14s 목표 %2d 확보 %3d 선정 %3d 부족 %2d%s" % (i, v["target"], v["have"], v["picked"], v["short"],
                                                             " · 연속 %d주" % v["streak"] if v["streak"] > 1 else ""))
    plan["min_reviewers"] = MIN_REVIEWERS
    json.dump(plan, open(os.path.join(a.dir, "plan.json"), "w"), ensure_ascii=False)
    print("저장:", os.path.join(a.dir, "plan.json"))


def cmd_apply(a):
    p = os.path.join(a.dir, "plan.json")
    res = remote(REMOTE_APPLY, *(["go"] if a.go else []), upload=p)
    print(("반영" if a.go else "dry-run"), json.dumps(res, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest="cmd", required=True)
    for n in ("state", "select", "apply"):
        s = sp.add_parser(n)
        s.add_argument("--dir", required=True, help="작업 디렉토리(세션 scratchpad 권장)")
        s.add_argument("--week", default=week_label(), help="주차 라벨(기본 이번 주 · 예 2026-W42)")
        if n == "select":
            s.add_argument("--csv", nargs="+", required=True, help="후보 CSV(서비스는 displayServiceName 열로 구분)")
        if n == "apply":
            s.add_argument("--go", action="store_true", help="실제 반영(없으면 dry-run)")
    a = ap.parse_args()
    {"state": cmd_state, "select": cmd_select, "apply": cmd_apply}[a.cmd](a)
