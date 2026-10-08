"""검증이 끊긴 보정은 활성 프롬프트나 버전에 남기지 않는다."""
import unittest
from unittest.mock import Mock, patch

from prism import learnops as LO, prompts as PR


class LearningFailureTests(unittest.TestCase):
    def setUp(self):
        self.sv = Mock()
        self.sv.get_store.return_value = None
        self.sv.rerun_unconfirmed.return_value = None
        for target, value in [('_SV', self.sv),
                              ('build_golden_from_reviews', Mock(return_value={'ok': True})),
                              ('_holdout_scope', Mock(return_value='all')),
                              ('snapshot_prompts', Mock(return_value={}))]:
            p = patch.object(LO, target, value)
            p.start(); self.addCleanup(p.stop)
        for target, value in [('LEARNED', {'analyze': '기존 지시'}), ('LEARNED_BY_MODEL', {})]:
            p = patch.object(PR, target, value)
            p.start(); self.addCleanup(p.stop)

    def test_golden_failure_stops_before_evaluation(self):
        LO.build_golden_from_reviews.return_value = {'ok': False}
        with patch.object(LO, 'eval_golden') as evaluate, patch.object(LO, 'meta_compile_run') as compile_:
            self.assertFalse(LO.learning_batch()['ok'])
            evaluate.assert_not_called(); compile_.assert_not_called()
        LO.snapshot_prompts.assert_not_called()

    def test_pre_evaluation_failure_does_not_compile_or_snapshot(self):
        with patch.object(LO, 'eval_golden', return_value={'ok': False}), patch.object(LO, 'meta_compile_run') as compile_:
            self.assertFalse(LO.learning_batch()['ok'])
            compile_.assert_not_called()
        LO.snapshot_prompts.assert_not_called()
        self.assertEqual(PR.LEARNED, {'analyze': '기존 지시'})

    def compile(self, team):
        PR.LEARNED = {'analyze': '검증 전 지시'}
        PR.LEARNED_BY_MODEL = {'model': {'review': '검증 전 지시'}}
        return {'ok': True}

    def test_failed_post_evaluation_restores_both_prompt_layers(self):
        pre = {'ok': True, 'grade_accuracy': .9, 'evaluated': 30}
        with patch.object(LO, 'eval_golden', side_effect=[pre, {'ok': False}]), patch.object(LO, 'meta_compile_run', side_effect=self.compile):
            report = LO.learning_batch()
        self.assertTrue(report['improve']['reverted'])
        self.assertIsNone(report['improve_delta'])
        self.assertIn('평가 실패', report['improve']['revert_reason'])
        self.assertEqual(PR.LEARNED, {'analyze': '기존 지시'})
        self.assertEqual(PR.LEARNED_BY_MODEL, {})

    def test_partial_compile_exception_restores_both_layers(self):
        def fail(team):
            self.compile(team)
            raise RuntimeError('model compile unavailable')
        with patch.object(LO, 'eval_golden', return_value={'ok': True}), patch.object(LO, 'meta_compile_run', side_effect=fail):
            with self.assertRaises(RuntimeError):
                LO.learning_batch()
        self.assertEqual(PR.LEARNED, {'analyze': '기존 지시'})
        self.assertEqual(PR.LEARNED_BY_MODEL, {})
        LO.snapshot_prompts.assert_not_called()

    def test_prompt_snapshot_does_not_replace_evaluated_corrections(self):
        def reset():
            PR.LEARNED = {'analyze': '평가하지 않은 원본 피드백'}
        self.sv.sync_prompt.side_effect = reset
        snapshot = LO.compose_prompts()
        self.assertEqual(snapshot['learned'], {'analyze': '기존 지시'})
        self.assertEqual(PR.LEARNED, snapshot['learned'])
        self.sv.sync_prompt.assert_not_called()

    def test_accepted_correction_survives_settings_sync_and_restart(self):
        import tempfile, os
        from prism.store import Store
        with tempfile.TemporaryDirectory() as tmp:
            st=Store(os.path.join(tmp,'learn.db'))
            self.sv.get_store.return_value=st
            pre={'ok':True,'grade_accuracy':.9,'evaluated':30}
            with patch.object(LO,'eval_golden',side_effect=[pre,pre]), patch.object(LO,'meta_compile_run',side_effect=self.compile):
                self.assertTrue(LO.learning_batch()['ok'])
            PR.LEARNED={};PR.LEARNED_BY_MODEL={}
            self.sv.get_store.return_value=Store(os.path.join(tmp,'learn.db'))
            LO.sync_learned()
            self.assertEqual(PR.LEARNED,{'analyze':'검증 전 지시'})
            self.assertEqual(PR.LEARNED_BY_MODEL,{'model':{'review':'검증 전 지시'}})

    def test_approved_write_failure_does_not_activate_candidate(self):
        self.sv.get_store.return_value=Mock()
        self.sv.get_store.return_value.save_report.side_effect=RuntimeError('DB unavailable')
        pre={'ok':True,'grade_accuracy':.9,'evaluated':30}
        with patch.object(LO,'eval_golden',side_effect=[pre,pre]), patch.object(LO,'meta_compile_run',side_effect=self.compile):
            self.assertFalse(LO.learning_batch()['ok'])
        self.assertEqual(PR.LEARNED,{'analyze':'기존 지시'})
        LO.snapshot_prompts.assert_not_called()
