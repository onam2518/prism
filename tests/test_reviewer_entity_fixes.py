"""게시판의 별칭 편집·IME 입력·수정 후 표시 회귀 검사."""
import pathlib
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs

from prism import dictops, entdict
from prism.store import Store
from prism.supastore import SupabaseStore


class AliasEditingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.st = Store(self.tmp.name + '/test.db')
        from prism import serve
        self.scope = patch.object(serve, '_STORE', self.st); self.scope.start(); self.addCleanup(self.scope.stop)
        entdict.ingest_meta(self.st, [('hash1', ['LA다저스', '뉴욕양키스'])])
        self.eid = self.st.ent_id_by_alias('LA다저스')
        self.st.ent_alias_add('[LA다저스, 다저스]', self.eid)

    def change(self, before, after):
        return dictops.entdict_action({'action':'update', 'id':self.eid, 'alias_change':{'before':before,'after':after}})

    def test_correct_existing_alias_and_remove_only_selected_alias(self):
        self.assertTrue(self.change('[LA다저스, 다저스]', '다저스')['ok'])
        self.assertEqual(self.st.ent_id_by_alias('다저스'), self.eid)
        self.assertFalse(self.st.ent_id_by_alias('[LA다저스, 다저스]'))
        self.assertTrue(self.change('다저스', '')['ok'])
        self.assertEqual(self.st.ent_aliases(self.eid), ['LA다저스'])

    def test_protect_canonical_conflicting_and_stale_aliases(self):
        for before, after in [('LA다저스',''),('[LA다저스, 다저스]','뉴욕양키스'),('없는 별칭','다저스')]:
            with self.subTest(before=before,after=after):
                self.assertFalse(self.change(before, after)['ok'])
        self.assertEqual(self.st.ent_aliases(self.eid), ['LA다저스','[LA다저스, 다저스]'])
        self.assertFalse(self.st.ent_alias_change('wrong-entity', '[LA다저스, 다저스]', '다저스'))

    def test_server_validates_alias_change_payload(self):
        for value in [None, [], {}, {'before': ['x'], 'after':'y'}, {'before':'x','after':'x'*201}]:
            self.assertFalse(dictops.entdict_action({'action':'update','id':self.eid,'alias_change':value})['ok'])

    def test_remote_update_and_delete_scope_to_alias_and_entity(self):
        st = object.__new__(SupabaseStore)
        with patch.object(st, '_req', return_value=[{'alias':'다저스'}]) as req:
            self.assertTrue(st.ent_alias_change('ent-1', '한글&별칭, 1', '다저스'))
            self.assertEqual(req.call_args[0], ('PATCH','entity_aliases'))
            q = parse_qs(req.call_args[1]['query'])
            self.assertEqual(q, {'alias':['eq.한글&별칭, 1'], 'entity_id':['eq.ent-1']})
            self.assertEqual(req.call_args[1]['body'], {'alias':'다저스'})
            st.ent_alias_change('ent-1','다저스','')
            self.assertEqual(req.call_args[0], ('DELETE','entity_aliases'))
        with patch.object(st, '_req', return_value=[]):
            self.assertFalse(st.ent_alias_change('ent-1','없는 별칭','수정'))

    @unittest.skipUnless(shutil.which('node'), 'Node.js 없음')
    def test_client_composition_and_saved_entity_display(self):
        script = r'''
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const window={};
for(const f of ['app-03-opendetail.js','app-10-hybridpick.js','app-20-operations.js']) vm.runInNewContext(fs.readFileSync('prism/vendor/'+f,'utf8'),{window});
const a={};for(const part of window.PRISM_APP_PARTS)Object.defineProperties(a,Object.getOwnPropertyDescriptors(part()));
a.hybEnt=[];a.dictData={};let prevented=0;
const ev=(isComposing,keyCode)=>({isComposing,keyCode,preventDefault(){prevented++;}});
for(const id of ['efent','faent']){
 const selected=[];
 a.hybComposition(id,{target:{value:'넷플릭스'}},true);a.hybInput(id,'넷플릭스');
 a.hybEnter(id,{kind:'entity',free:true},{},selected,ev(true,13));
 assert.equal(selected.length,0);assert.equal(prevented,0);
 a.hybComposition(id,{target:{value:'넷플릭스'}},false);
 a.hybEnter(id,{kind:'entity',free:true},{},selected,ev(false,229));assert.equal(selected.length,0);
 a.hybEnter(id,{kind:'entity',free:true},{},selected,ev(false,13));assert.deepEqual(selected,['넷플릭스']);
 assert.equal(a.hybSt(id).q,'');prevented=0;
}
(async()=>{
 a.detailOpen=true;a.detail={hash:'h',entities:['오류'],entities_scored:[{name:'오류',conf:.2}]};a.entFixHash='h';a.entFixList=['넷플릭스'];a.opsDetail={values:{entities:[{name:'오류',type:'OG'}]}};
 a.opsSave=async data=>{assert.equal(JSON.stringify(data.patch.entities),JSON.stringify([{name:'넷플릭스',type:null}]));return {ok:true};};a.liveToast=()=>{};a.loadEntLookup=()=>{};
 await a.saveEntFix();assert.equal(JSON.stringify(a.entsShown()),JSON.stringify([{name:'넷플릭스',conf:null}]));
 assert(a.entReviewChanges[0].includes('넷플릭스'));assert.equal(a.entFixOpen,false);
 a.rawData={items:[{hash:'h',grade:'G'}]};a.gradeDraft='R';a.gradeReason='본문 오류';
 let saved;a.opsSave=async data=>{saved=data;return {ok:true,basis:{snapshot:{quality_meta:{grade_review:{reason:data.grade_reason}}}}};};
 await a.saveGradeReview();assert.equal(saved.patch.finalGrade,'R');assert.equal(saved.grade_reason,'본문 오류');
 assert.equal(a.detail.grade,'R');assert.equal(a.rawData.items[0].grade,'R');
 a.gradeReason=' ';let err='';a._err=s=>{err=s;};await a.saveGradeReview();assert(err.includes('판정 사유'));
 a.entFixHash='h';a.entFixList=['다른 값'];a.opsSave=async()=>{a.detail={hash:'other',entities:['새 콘텐츠']};return {ok:true};};
 await a.saveEntFix();assert.equal(a.detail.entities[0],'새 콘텐츠');
})().catch(e=>{console.error(e);process.exitCode=1;});
'''
        subprocess.run(['node','-e',script],cwd=pathlib.Path(__file__).resolve().parents[1],check=True,capture_output=True,text=True,timeout=10)
