import json
import os
import tempfile
import unittest
import pathlib
import shutil
import subprocess

from prism import reviewtime as RT


def _rec(**kw):
    base = {"hash": "h1", "outcome": "verdict", "verdict": "good", "wall_ms": 60000, "active_ms": 40000,
            "verdict_wall_ms": 50000, "verdict_active_ms": 30000}
    base.update(kw)
    return base


class ReviewTimeTest(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'), 'Node.js 없음')
    def test_axis_save_flows_into_time_statistics(self):
        script = r'''
const fs=require('fs'), vm=require('vm'), assert=require('assert/strict');
const window={addEventListener(){}};let now=10000;const sent=[];
const context={window,Date:{now:()=>now},document:{hidden:false},setInterval(){},clearInterval(){},
 fetch:async(url,opt)=>{sent.push(JSON.parse(opt.body));return {ok:true}}};
for(const f of ['app-20-operations.js','app-22-reviewtime.js'])vm.runInNewContext(fs.readFileSync('prism/vendor/'+f,'utf8'),context);
const app={};for(const p of window.PRISM_APP_PARTS)Object.defineProperties(app,Object.getOwnPropertyDescriptors(p()));
Object.assign(app,{reviewer:'tester',myVerdict:()=>'',_authHeaders:()=>({}),liveToast(){},_err(){},opsLoad:async()=>{},
 detailOpen:true,detail:{hash:'1234567890abcdef'},opsDetail:{hash:'1234567890abcdef',revision:0,basis:{token:'v1'}}});
const axes={summary:{status:'accurate'},entities:{status:'accurate'},intent:{status:'accurate'},content_category:{status:'accurate'}};
app.opsRequest=async()=>({hash:app.detail.hash,basis:{token:'v1'},review:{axes},cases:[]});
(async()=>{
 app._rtStart(app.detail);
 for(let i=0;i<10;i++){now+=1000;app._rtTick();}
 await app.opsSave({action:'review',axes});
 now+=5000;app._rtFlush();
 assert.equal(sent[0].outcome,'verdict');assert.equal(sent[0].verdict,'good');
 assert.equal(sent[0].verdict_active_ms,10000);assert.equal(sent[0].verdict_wall_ms,10000);
 app._rtStart(app.detail);await app.opsOpen(app.detail);
 now+=2000;await app.opsSave({action:'review',axes});app._rtFlush();
 assert.equal(sent[1].outcome,'revisit');
 app._rtStart(app.detail);app.opsRequest=async()=>{throw Error('save failed')};
 now+=2000;await app.opsSave({action:'review',axes});app._rtFlush();
 assert.equal(sent[2].outcome,'abandoned');
 app._rtStart(app.detail);axes.summary.status='needs_fix';
 app.opsRequest=async()=>({hash:app.detail.hash,basis:{token:'v1'},review:{axes},cases:[]});
 for(let i=0;i<10;i++){now+=1000;app._rtTick();}
 await app.opsSave({action:'review',axes});app._rtFlush();
 assert.equal(sent[3].verdict,'bad');assert.equal(sent[3].note_active_ms,10000);
 process.stdout.write(JSON.stringify(sent));
})().catch(e=>{console.error(e);process.exitCode=1});
'''
        result = subprocess.run(['node', '-e', script], cwd=pathlib.Path(__file__).resolve().parents[1],
                                check=True, capture_output=True, text=True, timeout=10)
        from prism.store import Store
        with tempfile.TemporaryDirectory() as tmp:
            st = Store(os.path.join(tmp, 'time.db'))
            for record in json.loads(result.stdout):
                st.log_event('tester', 'review_time', json.dumps(RT.clean(record)))
            summary = RT.stats(RT.parse(st.events_since(('review_time',), 0)))['people'][0]
            self.assertEqual((summary['n'], summary['active_med_s'], summary['wall_med_s']), (2, 10, 10))
            self.assertEqual((summary['revisit'], summary['abandoned']), (1, 1))

    def test_clean_guards(self):
        m = RT.clean(_rec(active_ms=90000, wall_ms=60000, body_len="1200"))
        self.assertEqual(m["active_ms"], 60000)                     # 작업 시간은 머문 시간을 넘지 않는다
        self.assertEqual(m["body_len"], 1200)
        self.assertEqual(RT.clean(_rec(wall_ms=10 ** 12))["wall_ms"], RT.MAX_MS)   # 상한 자르기
        self.assertTrue(RT.clean(_rec(hash="gold:ok:abc"))["gold"])  # 골드 문항은 해시로 판별
        for bad in (_rec(outcome="x"), _rec(hash=""), _rec(wall_ms="abc"), _rec(verdict_wall_ms=None),
                    _rec(active_ms=None)):
            with self.assertRaises(ValueError):
                RT.clean(bad)
        self.assertEqual(RT.clean(_rec(outcome="abandoned", verdict_wall_ms=None))["outcome"], "abandoned")

    def test_parse_and_stats(self):
        ev = lambda rv, kind, meta, ts: {"reviewer": rv, "kind": kind, "meta": json.dumps(meta), "ts": ts}
        t0 = 1791300000.0                                           # 2026-10-06 UTC 오후 → 한국 시간 일자 확인
        rows = RT.parse([
            ev("a", "review_time", RT.clean(_rec(verdict_active_ms=10000)), t0),
            ev("a", "review_time", RT.clean(_rec(verdict_active_ms=30000, verdict="bad", note_active_ms=50000, note_wall_ms=70000)), t0),
            ev("a", "review_time", RT.clean(_rec(verdict_active_ms=90000)), t0),
            ev("a", "review_time", RT.clean(_rec(outcome="abandoned", verdict_wall_ms=None)), t0),
            ev("a", "review_time", RT.clean(_rec(hash="gold:bad:x", verdict_active_ms=1000)), t0),
            ev("a", "review_time_est", {"hash": "old", "est_ms": 20000, "method": "gap"}, t0),
            {"reviewer": "a", "kind": "review_time", "meta": "{broken", "ts": t0},
        ])
        self.assertEqual(len(rows), 6)                              # 깨진 meta 는 버림
        self.assertEqual(rows[0]["day"], "2026-10-07")              # UTC+9
        s = RT.stats(rows, {"a": "에이"})
        p = s["people"][0]
        self.assertEqual((p["name"], p["n"], p["abandoned"], p["gold"]), ("에이", 3, 1, 1))
        self.assertEqual((p["active_med_s"], p["active_p90_s"], p["note_med_s"]), (30.0, 90.0, 50.0))
        self.assertEqual((p["est_n"], p["est_med_s"]), (1, 20.0))   # 과거 추정은 따로
        self.assertEqual(s["days"], [{"day": "2026-10-07", "n": 3, "active_med_s": 30.0}])

    def test_sqlite_store_roundtrip(self):
        from prism.store import Store
        st = Store(os.path.join(tempfile.mkdtemp(), "t.db"))
        st.log_event("검수자", "review_time", json.dumps(RT.clean(_rec())))
        st.log_event("검수자", "other_kind", "{}")
        rows = RT.parse(st.events_since(("review_time", "review_time_est"), 0))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["reviewer"], "검수자")
        self.assertEqual(st.event_bonus().get("검수자", {}).get("total"), 0)   # 보너스 합계에 영향 없음


    def test_supabase_log_event_avoids_once_index(self):
        """운영 고유 제약(reviewer_id, kind, day)이 측정 기록을 하루 1건으로 막지 않게 day=epoch 초 ·
        충돌하면 1초 뒤로 한 번 더(2026-10-07 배포 직후 발견)."""
        from prism.supastore import SupabaseStore
        st = SupabaseStore.__new__(SupabaseStore)
        sent, fail = [], {"n": 1}
        def fake_req(method, table, body=None, **kw):
            sent.append(body[0]["day"])
            if fail["n"]:
                fail["n"] -= 1
                raise RuntimeError("409 duplicate key")
            return []
        st._req = fake_req
        st.log_event("u1", "review_time", "{}", team="t")
        self.assertEqual(len(sent), 2)
        self.assertEqual(sent[1], sent[0] + 1)
        self.assertGreater(sent[0], 10 ** 9)                         # 날짜(YYYYMMDD)가 아니라 epoch 초


if __name__ == "__main__":
    unittest.main()
