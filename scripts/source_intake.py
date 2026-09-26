"""Accept owner-authored public-source Issues as bounded catalog inputs."""
from __future__ import annotations
import copy
import json
import os
from pathlib import Path
import re
from urllib.parse import unquote, urlsplit

try:
    from .collector import check_public_url, normalize_url, has_secret_query
except ImportError:
    from collector import check_public_url, normalize_url, has_secret_query


ROOT = Path(__file__).resolve().parents[1]
FIELDS = {'来源名称': 'name', '来源链接': 'url', '来源类型': 'type', '分类': 'category'}


def issue_fields(body):
    if not isinstance(body, str):
        raise ValueError('提交内容缺失')
    matches = list(re.finditer(r'(?m)^### ([^\r\n]+)\r?\n', body))
    if not matches or body[:matches[0].start()].strip():
        raise ValueError('表单格式不正确')
    values = {}
    for i, match in enumerate(matches):
        label = match[1].strip()
        if label not in FIELDS or label in values:
            raise ValueError('表单字段重复或未知')
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        values[label] = body[match.end():end].strip()
    if set(values) != set(FIELDS):
        raise ValueError('表单字段不完整')
    return {FIELDS[key]: value for key, value in values.items()}


def parse_submission(event: dict, owner: str) -> dict | None:
    issue = event.get('issue') or {}
    author = (issue.get('user') or {}).get('login', '')
    if not isinstance(author, str) or author.casefold() != owner.casefold():
        return None
    if not str(issue.get('title') or '').startswith('[添加来源]'):
        return None
    values = issue_fields(issue.get('body'))
    raw_url = values['url']
    if not raw_url or len(raw_url) > 2048:
        raise ValueError('链接为空或过长')
    if has_secret_query(raw_url):
        raise ValueError('链接包含账号或令牌参数')
    url = check_public_url(raw_url)
    kind = {'直链': 'direct', '收集网页': 'page'}.get(values['type'])
    category = {'普通': 'ordinary', '成人': 'adult'}.get(values['category'])
    if not kind or not category:
        raise ValueError('来源类型或分类无效')
    name = values['name'].strip()
    if name == '_No response_':
        name = ''
    if not name:
        name = unquote(urlsplit(url).path.rstrip('/').rsplit('/', 1)[-1]) or urlsplit(url).hostname
    if not name or len(name) > 100 or any(ord(char) < 32 for char in name):
        raise ValueError('来源名称无效或过长')
    return {'name': name, 'url': url, 'type': kind, 'category': category}


def apply_submission(config: dict, submission: dict) -> tuple[dict, str]:
    updated = copy.deepcopy(config)
    url = submission['url']
    for key in ('seeds', 'source_pages'):
        if any(normalize_url(row.get('url')) == url for row in updated.get(key, [])):
            return updated, 'duplicate'
    if submission['type'] == 'direct':
        rows = updated.setdefault('seeds', [])
        if len(rows) >= 100:
            raise ValueError('固定来源已达 100 条上限')
        rows.append({'name': submission['name'], 'url': url,
                     'category': submission['category'], 'sources': [url]})
    elif submission['type'] == 'page':
        rows = updated.setdefault('source_pages', [])
        if len(rows) >= 30:
            raise ValueError('收集网页已达 30 个上限')
        rows.append({'name': submission['name'], 'url': url, 'parser': 'links',
                     'max_links': 30, 'category': submission['category']})
    else:
        raise ValueError('来源类型无效')
    return updated, 'accepted'


def write_output(status, message):
    path = os.environ.get('GITHUB_OUTPUT')
    if path:
        with open(path, 'a', encoding='utf-8') as output:
            output.write('status=' + status + '\n')
            output.write('message=' + re.sub(r'[\r\n]+', ' ', message)[:180] + '\n')
    print(status + '：' + message)


def main():
    event = json.loads(Path(os.environ['GITHUB_EVENT_PATH']).read_text())
    owner = os.environ['GITHUB_REPOSITORY_OWNER']
    config_path = ROOT / 'sources.config.json'
    try:
        submission = parse_submission(event, owner)
        if submission is None:
            write_output('ignored', '只接收仓库所有者的添加来源表单')
            return
        config = json.loads(config_path.read_text())
        updated, status = apply_submission(config, submission)
        if status == 'accepted':
            temp = config_path.with_suffix('.tmp')
            temp.write_text(json.dumps(updated, ensure_ascii=False, indent=2) + '\n')
            temp.replace(config_path)
            write_output(status, '已纳入每日检查，能否进入合集以实播结果为准')
        else:
            write_output(status, '这个链接已经在每日检查配置中')
    except ValueError as error:
        write_output('invalid', str(error))


if __name__ == '__main__':
    main()
