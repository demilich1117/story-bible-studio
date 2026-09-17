from support import StudioCase
from new_story_project import create_project
from studio_bible import audit_bible, finalize_bible
from studio_core import StudioError, load_yaml, write_yaml
from studio_versions import snapshot_root


class BibleAuditTests(StudioCase):
    def setUp(self):
        super().setUp()
        self.nsfw = self.project / 'StoryBible/NSFW基础设定.md'
        cfg = load_yaml(self.project / '项目配置.yaml')
        cfg['nsfw']['enabled'] = True
        cfg['nsfw']['primary_kinks'] = ['亲吻']
        write_yaml(self.project / '项目配置.yaml', cfg)

    def test_generated_scaffold_requires_content_then_freezes_without_parallel_sections(self):
        fresh = create_project(self.root, '新模板')
        scaffold = (fresh / 'StoryBible/NSFW基础设定.md').read_text(encoding='utf-8')
        self.assertIn('性器官', scaffold)
        self.assertIn('不写成解剖清单或参数表', scaffold)
        self.nsfw.write_text(scaffold, encoding='utf-8')
        with self.assertRaises(StudioError):
            finalize_bible(self.project, apply=True)
        self.assertEqual('building', load_yaml(self.project / '项目配置.yaml')['bible_status'])

        adopted = ('# NSFW 基础设定\n\n## 情色身体\n\n'
                   '她的外阴与阴唇形态已经确认，身体反应也有稳定设定。\n\n'
                   '## 亲密取向与互动方式\n\n她会直接表达需要。\n\n'
                   '## 偏好与具体表现\n\n她偏爱先亲吻，口交是已有的长期偏好。\n')
        self.nsfw.write_text(adopted, encoding='utf-8')
        result = finalize_bible(self.project, apply=True)
        self.assertEqual([], result['errors'])
        frozen = snapshot_root(self.project, result['bible_revision'])
        self.assertEqual(adopted, (frozen / 'StoryBible/NSFW基础设定.md').read_text(encoding='utf-8'))

    def test_default_erotic_body_requires_genital_detail_or_intentional_blank(self):
        self.nsfw.write_text(
            '# NSFW 基础设定\n\n## 情色身体\n\n胸部柔软，高潮时会颤抖。\n\n'
            '## 偏好与具体表现\n\n口交是长期偏好。\n',
            encoding='utf-8',
        )
        errors = audit_bible(self.project, for_finalize=True)['errors']
        self.assertTrue(any('性器官的稳定情色身体描写' in error for error in errors))

        self.nsfw.write_text(
            '# NSFW 基础设定\n\n## 情色身体\n\n胸部柔软；外阴具体外观有意留白。\n\n'
            '## 偏好与具体表现\n\n口交是长期偏好。\n',
            encoding='utf-8',
        )
        errors = audit_bible(self.project, for_finalize=True)['errors']
        self.assertFalse(any('性器官的稳定情色身体描写' in error for error in errors))

    def test_organizing_preferences_under_custom_headings_is_supported(self):
        text = '# 亲密生活\n\n## 她重视的相处\n\n她喜欢亲吻和拥抱，口交是已有偏好。\n'
        self.nsfw.write_text(text, encoding='utf-8')
        self.assertEqual([], audit_bible(self.project, for_finalize=True)['errors'])

    def test_legacy_sections_remain_readable_without_automatic_rewrite(self):
        text = ('# NSFW 基础设定\n\n## 身体与器官设定\n\n触感敏锐。\n\n'
                '## 关系欲望与长期偏好\n\n喜欢先亲吻，口交是已有偏好。\n\n'
                '## 十问式 H 标签\n\n亲吻。\n\n## 常用色气引擎\n\n久别重逢。\n\n'
                '## 淫语声线与节奏\n\n短句直接。\n\n## 事后余味\n\n靠在一起休息。\n')
        self.nsfw.write_text(text, encoding='utf-8')
        result = finalize_bible(self.project, apply=True)
        self.assertEqual([], result['errors'])
        self.assertEqual(text, self.nsfw.read_text(encoding='utf-8'))

    def test_stale_template_and_missing_voice_still_block_freeze(self):
        self.nsfw.write_text('# NSFW\n\n待构筑\n', encoding='utf-8')
        errors = audit_bible(self.project, for_finalize=True)['errors']
        self.assertTrue(any('待构筑' in error for error in errors))
        self.nsfw.write_text('# NSFW\n\n口交是已有的长期偏好。\n', encoding='utf-8')
        (self.project / 'StoryBible/语料/Mira Chen.md').unlink()
        errors = audit_bible(self.project, for_finalize=True)['errors']
        self.assertTrue(any('锚点角色缺少完整声线' in error for error in errors))

    def test_character_hub_requires_valid_nonempty_anchors(self):
        cfg = load_yaml(self.project / '项目配置.yaml')
        cfg['story_scope']['anchors'] = []
        write_yaml(self.project / '项目配置.yaml', cfg)
        errors = audit_bible(self.project, for_finalize=True)['errors']
        self.assertTrue(any('至少需要一个' in error for error in errors))

        cfg['story_scope']['anchors'] = ['Mira Chen', 'mira chen', '../越界']
        write_yaml(self.project / '项目配置.yaml', cfg)
        errors = audit_bible(self.project, for_finalize=True)['errors']
        self.assertTrue(any('重复角色 ID' in error for error in errors))
        self.assertTrue(any('非法 story_scope 锚点' in error for error in errors))

    def test_anchor_role_must_have_content_and_voice_cannot_be_orphaned(self):
        role = self.project / 'StoryBible/角色/Mira Chen.md'
        role.write_text('# Mira Chen\n', encoding='utf-8')
        errors = audit_bible(self.project, for_finalize=True)['errors']
        self.assertTrue(any('锚点角色档案仍为空骨架' in error for error in errors))

        role.write_text('# Mira Chen\n\n修表师。\n', encoding='utf-8')
        (self.project / 'StoryBible/语料/Orphan.md').write_text(
            '# Orphan\n\n## 核心声线\n\n短句。\n\n## 对象覆盖：陌生人\n\n礼貌。\n\n'
            '## 场景覆盖：日常\n\n直接。\n',
            encoding='utf-8',
        )
        errors = audit_bible(self.project, for_finalize=True)['errors']
        self.assertTrue(any('孤立语料缺少同名角色档案' in error for error in errors))

    def test_frozen_bible_warns_when_readable_ledger_still_has_open_topics(self):
        cfg = load_yaml(self.project / '项目配置.yaml')
        cfg['bible_status'] = 'frozen'
        write_yaml(self.project / '项目配置.yaml', cfg)
        (self.project / '00-项目索引.md').write_text(
            '# 测试\n\n- Story Bible：已冻结\n', encoding='utf-8')
        (self.project / '构筑/构筑状态.md').write_text(
            '# 构筑状态\n- 阶段：已冻结\n\n## 未收口人物｜open\n', encoding='utf-8')
        warnings = audit_bible(self.project, for_finalize=True)['warnings']
        self.assertTrue(any('仍有 open 主题' in warning for warning in warnings))
