import unittest
from unittest.mock import patch
from pathlib import Path

from scripts.source_intake import parse_submission, apply_submission


def issue(name='新直播', url='https://example.com/live.m3u', kind='直链', category='普通',
          author='felixwang1987'):
    body = ('### 来源名称\n\n' + name + '\n\n### 来源链接\n\n' + url +
            '\n\n### 来源类型\n\n' + kind + '\n\n### 分类\n\n' + category)
    return {'issue': {'user': {'login': author}, 'title': '[添加来源] 公开链接', 'body': body}}


def public_url(url):
    from scripts.collector import normalize_url
    result = normalize_url(url)
    if not result:
        raise ValueError('拒绝非公开地址')
    return result


class IntakeTests(unittest.TestCase):
    def parse(self, event):
        with patch('scripts.source_intake.check_public_url', side_effect=public_url):
            return parse_submission(event, 'FelixWang1987')

    def test_owner_issue_produces_all_four_fields(self):
        row = self.parse(issue())
        self.assertEqual(row, {'name': '新直播', 'url': 'https://example.com/live.m3u',
                               'type': 'direct', 'category': 'ordinary'})

    def test_other_author_or_non_submission_issue_is_ignored(self):
        self.assertIsNone(self.parse(issue(author='other')))
        event = issue()
        event['issue']['title'] = '普通讨论'
        self.assertIsNone(self.parse(event))

    def test_missing_duplicate_or_extra_field_is_rejected(self):
        for body in (issue()['issue']['body'].replace('### 分类', '### 其他'),
                     issue()['issue']['body'] + '\n\n### 分类\n\n成人',
                     issue()['issue']['body'] + '\n\n### 账号\n\nsecret'):
            with self.subTest(body=body[-35:]):
                event = issue()
                event['issue']['body'] = body
                with self.assertRaises(ValueError):
                    self.parse(event)

    def test_rejects_private_userinfo_and_secret_query(self):
        for url in ('http://127.0.0.1/a.json', 'https://alice:pass@example.com/a.json',
                    'https://example.local/a.json',
                    'https://example.com/a.json?ToKeN=abc',
                    'https://example.com/a.json?api_%6bey=abc',
                    'https://example.com/a.json?session=xyz',
                    'https://example.com/a.json?apiKey=abc',
                    'https://example.com/a.json?accessToken=abc',
                    'https://example.com/a.json?authToken=abc',
                    'https://example.com/a.json?sessionid=abc'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                self.parse(issue(url=url))

    def test_dns_private_address_is_rejected(self):
        with patch('scripts.source_intake.check_public_url', side_effect=ValueError('拒绝内网地址')):
            with self.assertRaises(ValueError):
                parse_submission(issue(), 'felixwang1987')

    def test_rejects_invalid_type_category_and_long_fields(self):
        for event in (issue(kind='直播'), issue(category='全部'),
                      issue(name='a' * 101), issue(url='https://example.com/' + 'a' * 2048)):
            with self.subTest(event=event['issue']['body'][-30:]), self.assertRaises(ValueError):
                self.parse(event)

    def test_empty_name_uses_url_label(self):
        self.assertEqual(self.parse(issue(name='_No response_'))['name'], 'live.m3u')

    def test_direct_source_is_added_once_and_keeps_category(self):
        config = {'seeds': [], 'source_pages': []}
        row = self.parse(issue(category='成人'))
        updated, status = apply_submission(config, row)
        self.assertEqual(status, 'accepted')
        self.assertEqual(updated['seeds'][0]['category'], 'adult')
        self.assertEqual(updated['seeds'][0]['url'], 'https://example.com/live.m3u')
        self.assertEqual(config['seeds'], [])
        again, status = apply_submission(updated, row)
        self.assertEqual(status, 'duplicate')
        self.assertEqual(again, updated)

    def test_generic_page_uses_links_parser_and_is_deduplicated(self):
        row = self.parse(issue(url='https://example.com/links.html', kind='收集网页'))
        updated, status = apply_submission({'seeds': [], 'source_pages': []}, row)
        self.assertEqual(status, 'accepted')
        self.assertEqual(updated['source_pages'][0]['parser'], 'links')
        self.assertEqual(updated['source_pages'][0]['max_links'], 30)
        self.assertEqual(updated['source_pages'][0]['category'], 'ordinary')
        self.assertEqual(apply_submission(updated, row)[1], 'duplicate')

    def test_capacity_limits_reject_new_items_but_allow_duplicates(self):
        row = self.parse(issue())
        config = {'seeds': [{'url': f'https://example.com/{i}.json'} for i in range(100)],
                  'source_pages': []}
        with self.assertRaises(ValueError):
            apply_submission(config, row)
        page = self.parse(issue(url='https://example.com/list.html', kind='收集网页'))
        config = {'seeds': [], 'source_pages': [{'url': f'https://example.com/{i}.html'}
                                                  for i in range(30)]}
        with self.assertRaises(ValueError):
            apply_submission(config, page)


class WorkflowContractTests(unittest.TestCase):
    def test_issue_submission_uses_same_collection_and_deployment_run(self):
        workflow = Path('.github/workflows/update.yml').read_text()
        self.assertIn('issues:', workflow)
        self.assertIn('types: [opened]', workflow)
        self.assertIn('queue: max', workflow)
        self.assertIn('ref: ${{ github.event.repository.default_branch }}', workflow)
        self.assertIn("cron: '23 10 * * *'", workflow)
        self.assertLess(workflow.index('id: intake'), workflow.index('python scripts/collector.py'))
        self.assertIn('python scripts/source_intake.py', workflow)
        self.assertIn("steps.intake.outputs.status == 'accepted'", workflow)
        self.assertIn('sources.config.json', workflow.split('name: 保存本轮快照')[1])
        self.assertIn('actions/deploy-pages@v4', workflow)

    def test_issue_feedback_is_owner_only_and_never_injects_body_into_shell(self):
        workflow = Path('.github/workflows/update.yml').read_text()
        self.assertIn('github.event.issue.user.login == github.repository_owner', workflow)
        self.assertIn('issues: write', workflow)
        self.assertIn('gh issue comment', workflow)
        self.assertNotIn('${{ github.event.issue.body', workflow)
        self.assertIn('GITHUB_EVENT_PATH', Path('scripts/source_intake.py').read_text())


if __name__ == '__main__':
    unittest.main()
