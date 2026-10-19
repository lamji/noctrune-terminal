#!/usr/bin/env python3
"""The hooks of the Claude and Codex sessions Nocturne starts.

    nocturne_hook.py session-start|prompt|pre-tool|post-tool|stop claude|codex    (event JSON on stdin)
    nocturne_hook.py --diff                                                       (impact of the working-tree diff)

Nocturne hands each CLI these hooks on its own command line (Claude: --settings, Codex: -c), so nothing is
written to ~/.claude or ~/.codex settings. What each hook may do is read from the Nocturne settings file named
by NOCTURNE_TOOLS on every call, so a toggle in the Skills & Hooks screen applies to running sessions too.

Plain Python, standard library only: it runs on the interpreter Nocturne itself runs on. A hook never fails a
session: anything unexpected lets the tool call, prompt or stop go ahead.
"""
import ast
import fnmatch
import io
import json
import os
import re
import select
import shutil
import socket
import subprocess
import sys
import time
import tokenize
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPORTS = '.nocturne/reports'
MEMORY = '.nocturne/memory'
COMMAND = {'claude': '/', 'codex': '$'}
STDIN_WAIT = 1.5  # A shell may hand over a stdin it never closes; the event is optional.
APPROVAL_WAIT = 120  # Seconds a Codex command waits for an answer in Nocturne before it is refused.
PROJECT = None  # The session's directory, for the activity log.

# The hooks Nocturne ships. Each can be switched off in agent-tools.json. None of them loads a skill by itself.
BUILTIN_HOOKS = (
    {'id': 'session-context', 'name': 'Reports after a compact', 'when': 'After a compact',
     'what': 'Loads the session\'s own flow reports again once its conversation is compacted. Nothing is added when a '
             'session starts or resumes.'},
    {'id': 'project-memory', 'name': 'Recall project memory', 'when': 'Before each task prompt',
     'what': 'Loads saved project corrections and facts before investigation, with corrections first and a bounded context.'},
    {'id': 'impact-check', 'name': 'Impact check', 'when': 'Before and after a file edit',
     'what': 'Lists every place in the repository that calls, imports or references the code an edit changes '
             '(Python, JavaScript, TypeScript), so the edit does not break them. Never blocks the edit.'},
    {'id': 'report-upkeep', 'name': 'Keep reports current', 'when': 'Turn end',
     'what': 'When a file one of the session\'s flow reports cites was changed, the session updates that report '
             'before it stops. Asked once per change.'},
    {'id': 'memory-upkeep', 'name': 'Keep memory current', 'when': 'Before each prompt',
     'what': 'When you correct the agent or state a rule, the session saves it to project memory as part of the task, '
             'without interrupting it: it updates the entry that already covers the subject instead of adding another, and '
             'merges overlapping entries. Entries it writes are checked against the memory standard.'},
    {'id': 'git-flow', 'name': 'Git: confirm commit, push and PR', 'when': 'Before a shell command',
     'what': 'git commit, git push and gh pr create need your approval. Status, diff, staging and switching '
             'branches are not touched.'},
    {'id': 'database', 'name': 'Database: ask first', 'when': 'Before a shell command',
     'what': 'Database clients, migrations, dumps and SQL that changes data or schema need your approval.'},
    {'id': 'packages', 'name': 'Package manager: ask first', 'when': 'Before a shell command',
     'what': 'Installing, removing, updating or publishing packages, and running packages fetched from the registry '
             '(dlx), need your approval. Project scripts (test, build, lint, typecheck) run without asking.'},
    {'id': 'dev-server', 'name': 'One dev server at a time', 'when': 'Before a shell command',
     'what': 'Refuses to start a dev server when its port is already taken, and points the agent at the one '
             'that is running.'},
    {'id': 'protected-branch', 'name': 'Protected branches are read-only', 'when': 'Before an edit or commit',
     'what': 'No edits, commits or pushes while the checkout is on a protected branch. Set the branch names below.'},
)
RULE_EVENTS = (('pre-tool', 'Before a tool call'), ('post-tool', 'After a tool call'), ('prompt', 'When a prompt is sent'),
               ('stop', 'When a turn ends'))
RULE_ACTIONS = (('block', 'Block'), ('annotate', 'Add a note'), ('command', 'Run a command'), ('allow', 'Allow (skip later rules)'))


# Plumbing

def read_stdin():
    if sys.stdin is None or sys.stdin.isatty():
        return ''
    chunks, deadline = [], time.time() + STDIN_WAIT
    fd = sys.stdin.fileno()
    while True:
        left = deadline - time.time()
        if left <= 0 or not select.select([fd], [], [], left)[0]:
            break
        data = os.read(fd, 65536)
        if not data:
            break
        chunks.append(data)
    return b''.join(chunks).decode('utf-8', 'replace')


def parse_event(text):
    try:
        value = json.loads(text.lstrip('﻿'))
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def settings(strict=False):
    try:
        value = json.loads(Path(os.environ['NOCTURNE_TOOLS']).read_text())
    except FileNotFoundError:
        return {}
    except (KeyError, OSError, ValueError):
        if strict:
            raise
        return {}
    if strict and not isinstance(value, dict):
        raise ValueError('Invalid Nocturne check settings.')
    return value if isinstance(value, dict) else {}


def hook_on(config, hook_id):
    return (config.get('hooks') or {}).get(hook_id, True) is not False


def cli_dir():
    value = os.environ.get('NOCTURNE_CLI_DIR')
    return Path(value) if value else None


def log(entry):
    """One line in Nocturne's activity log, which the Skills & Hooks screen shows. A record, never a gate."""
    root = cli_dir()
    if not root:
        return
    try:
        root.mkdir(parents=True, exist_ok=True)
        path = root / 'activity.jsonl'
        with path.open('a') as out:
            out.write(json.dumps({'at': time.time(), 'term': os.environ.get('NOCTURNE_TERM_ID'), 'project': PROJECT, **entry}) + '\n')
        if path.stat().st_size > 256 << 10:
            lines = path.read_text().splitlines()[-400:]
            path.write_text('\n'.join(lines) + '\n')
    except OSError:
        pass


def emit(value):
    sys.stdout.write(json.dumps(value, ensure_ascii=True))


def frontmatter(text):
    """Top-level `key: value` pairs of a leading --- block."""
    fields = {}
    body = text.lstrip('﻿')
    if not body.startswith('---'):
        return fields
    end = body.find('\n---', 3)
    if end == -1:
        return fields
    for line in body[3:end].splitlines():
        match = re.match(r'^([A-Za-z_]+):\s*(.*)$', line)
        if match:
            fields[match[1]] = match[2].strip().strip('"\'')
    return fields


def read_head(path, size=4096):
    try:
        with open(path, encoding='utf-8', errors='replace') as source:
            return source.read(size)
    except OSError:
        return ''


def list_dir(path):
    try:
        return sorted(os.listdir(path))
    except OSError:
        return []


# Skills, reports and memory

def skills_dir():
    return Path(os.environ.get('NOCTURNE_SKILLS_DIR') or HERE.parent / '.claude' / 'skills')


def list_skills(root=None):
    found = []
    root = Path(root) if root else skills_dir()
    for name in list_dir(root):
        path = root / name / 'SKILL.md'
        if not path.is_file():
            continue
        fields = frontmatter(read_head(path))
        found.append({'name': fields.get('name') or name, 'description': fields.get('description', ''), 'file': str(path)})
    return sorted(found, key=lambda s: s['name'])


def list_reports(cwd):
    folder = Path(cwd, REPORTS)
    found = []
    for name in list_dir(folder):
        if not name.endswith('.md'):
            continue
        path = folder / name
        fields = frontmatter(read_head(path))
        try:
            at = path.stat().st_mtime
        except OSError:
            continue
        found.append({'id': name[:-3], 'session': fields.get('session'), 'title': fields.get('title') or name[:-3], 'at': at, 'file': str(path)})
    return sorted(found, key=lambda r: -r['at'])


def read_report(cwd, report_id, limit=12000):
    try:
        text = Path(cwd, REPORTS, f'{report_id}.md').read_text(errors='replace').lstrip('﻿')
    except OSError:
        return None
    return text if len(text) <= limit else text[:limit] + '\n[... cut; read the file for the rest]'


MEMORY_ORDER = ('correction', 'naming', 'environment', 'preference', 'fact')  # Recall corrections before general facts.
MEMORY_STOPWORDS = {  # Words too common in requests to say which memory entry they are about.
    'what', 'when', 'where', 'which', 'with', 'this', 'that', 'from', 'have', 'does', 'into', 'about', 'there', 'their', 'should',
    'would', 'could', 'how', 'why', 'the', 'and', 'for', 'you', 'please', 'make', 'need', 'want', 'works', 'work', 'working', 'setup',
    'set', 'up', 'local', 'locally', 'use', 'using', 'used', 'fix', 'get', 'run', 'running', 'can', 'are', 'not', 'but', 'its', 'all',
    'any', 'new', 'now', 'then', 'than', 'also', 'just', 'like', 'some', 'show', 'tell', 'explain', 'check', 'again', 'still', 'only',
    'one', 'our', 'out', 'was', 'were', 'will', 'here', 'they', 'them', 'these', 'those', 'your', 'yours', 'has', 'had', 'did', 'done',
    # Words every coding request uses: they never say which memory entry a request is about.
    'repository', 'project', 'file', 'files', 'folder', 'directory', 'code', 'function', 'functions', 'method', 'thing', 'things',
    'issue', 'issues', 'problem', 'problems', 'image', 'images', 'screenshot', 'picture', 'attached', 'pasted',
    'something', 'note', 'many', 'much', 'find', 'look', 'add', 'added', 'change', 'changed', 'update', 'updated', 'also', 'list',
    'app', 'apps', 'application'}


def word_stem(word):
    for suffix in ('ations', 'ation', 'ings', 'ing', 'ions', 'ion', 'ers', 'er', 'ed', 'es', 's', 'e'):
        if word.endswith(suffix) and len(word) - len(suffix) >= 4:
            return word[:-len(suffix)]
    return word


TERM_ALIASES = {  # Short or variant words mapped to one form, so "db" in a request finds "database" in memory and back.
    'db': 'database', 'dbs': 'database', 'postgres': 'postgresql', 'pg': 'postgresql', 'psql': 'postgresql',
    'repo': 'repository', 'repos': 'repository', 'env': 'environment', 'envs': 'environment', 'config': 'configuration',
    'cfg': 'configuration', 'conf': 'configuration', 'k8s': 'kubernetes', 'bq': 'bigquery', 'pr': 'pullrequest',
    'prs': 'pullrequest', 'js': 'javascript', 'ts': 'typescript', 'py': 'python', 'creds': 'credentials', 'cred': 'credentials',
    'perms': 'permissions', 'perm': 'permissions', 'deps': 'dependencies', 'dep': 'dependencies', 'docs': 'documentation',
    'doc': 'documentation', 'prod': 'production', 'dev': 'development', 'auth': 'authentication', 'authn': 'authentication',
    'authz': 'authorization', 'msg': 'message', 'msgs': 'message', 'img': 'image', 'imgs': 'image', 'ci': 'pipeline',
}


def text_words(text):
    """(stem, surface word) pairs of a text: identifiers like lite-query-switch or compareAllocationQueries are split into
    their words (the joined identifier is kept too), aliases are unified, and common request words are dropped."""
    text = re.sub(r'([a-z0-9])([A-Z])', r'\1 \2', text)
    pairs = []
    for raw in re.findall(r'[A-Za-z0-9_\-]{2,}', text):
        parts = [w for w in re.split(r'[_\-]', raw.lower()) if w]
        joined = ''.join(parts) if len(parts) > 1 and not any(w.isdigit() for w in parts) else None
        for word in parts + ([joined] if joined else []):
            word = TERM_ALIASES.get(word, word)
            if len(word) >= 3 and word not in MEMORY_STOPWORDS and not word.isdigit():
                pairs.append((word_stem(word), word))
    return pairs


def joined_terms(text):
    """Stems of the glued identifiers text_words adds (litequeryswitch): good for matching, noise in a contents page."""
    text = re.sub(r'([a-z0-9])([A-Z])', r'\1 \2', text)
    found = set()
    for raw in re.findall(r'[A-Za-z0-9_\-]{2,}', text):
        parts = [w for w in re.split(r'[_\-]', raw.lower()) if w]
        if len(parts) > 1 and not any(w.isdigit() for w in parts):
            found.add(word_stem(''.join(parts)))
    return found


def prompt_terms(prompt):
    """The meaningful words of a text, stemmed, with identifiers split into their words and aliases unified."""
    return {stem for stem, _ in text_words(prompt)}


# The memory index: like a book's table of contents and index, so a prompt loads only the memory it needs. It covers the
# memory entries and the flow reports; .nocturne/index/index.json caches it (rebuilt per file when one changes) and
# .nocturne/index/CONTENTS.md is the readable table of contents and alphabetical index the agent can open.
INDEX_DIR = '.nocturne/index'
INDEX_VERSION = 4
FIELD_WEIGHT = {'head': 3, 'heading': 2, 'body': 1}  # Where a word appears: name / description / keywords, a heading, the text.


def index_document(path, kind, rel):
    """One memory entry or flow report: title, summary, sections (with lines) and the words in each."""
    text = read_head(path, 400_000).lstrip('﻿')
    fields = frontmatter(text)
    lines = text.splitlines()
    body_from = 0
    if text.startswith('---'):
        closing = next((i for i, line in enumerate(lines[1:], 1) if line.strip() == '---'), 0)
        body_from = closing + 1
    sections, fence, first_line = [], False, ''
    current = {'title': '', 'line': body_from + 1, 'words': []}
    for number, line in enumerate(lines[body_from:], body_from + 1):
        if line.lstrip().startswith('```'):
            fence = not fence
        heading = None if fence else re.match(r'^(#{1,6})\s+(.+?)\s*#*\s*$', line)
        if heading:
            if current['title'] or current['words']:
                sections.append(current)
            current = {'title': heading[2].strip(), 'line': number, 'level': len(heading[1]), 'words': text_words(heading[2])}
            current['heading_terms'] = sorted({stem for stem, _ in current['words']})
            continue
        if not first_line and line.strip() and not fence:
            first_line = re.sub(r'[*_`>#\[\]]', '', line).strip()
        current['words'] = current['words'] + text_words(line)
    if current['title'] or current['words']:
        sections.append(current)
    name = fields.get('name') or Path(rel).stem
    title = fields.get('title') or (next((x['title'] for x in sections if x['title']), '') if kind == 'report' else '') or name
    summary = ' '.join((fields.get('description') or first_line).split())[:200]
    keywords = [k for k in re.split(r'[,\s]+', fields.get('keywords') or fields.get('tags') or '') if k]
    head = text_words(' '.join([name, Path(rel).stem, title, summary, ' '.join(keywords)]))
    weight, display = {}, {}
    for field, pairs in [('head', head)] + [('heading', [(s, w) for s, w in x['words'] if s in x.get('heading_terms', [])]) for x in sections] + \
                        [('body', x['words']) for x in sections]:
        for stem, word in pairs:
            weight[stem] = weight.get(stem, 0) + FIELD_WEIGHT[field]
            display.setdefault(stem, {}).setdefault(word, 0)
            display[stem][word] += 1
    try:
        stat = path.stat()
    except OSError:
        stat = None
    return {
        'file': rel, 'kind': kind, 'type': fields.get('type', 'fact') if kind == 'memory' else 'report', 'name': name,
        'title': title, 'summary': summary, 'keywords': keywords, 'lines': len(lines), 'chars': len(text),
        'mtime': stat.st_mtime if stat else 0, 'size': stat.st_size if stat else 0,
        'head': sorted({stem for stem, _ in head}),
        'sections': [{'title': x['title'], 'line': x['line'], 'level': x.get('level', 0), 'heading': x.get('heading_terms', []),
                      'terms': sorted({stem for stem, _ in x['words']})} for x in sections],
        'weight': weight, 'display': {stem: max(words, key=words.get) for stem, words in display.items()},
        'joined': sorted(joined_terms(text + ' ' + rel) - {stem for stem, _ in text_words(' '.join(keywords))}),
    }


def memory_documents(cwd):
    """Every memory entry and flow report as (path, kind, relative path)."""
    found = []
    for folder, kind in ((MEMORY, 'memory'), (REPORTS, 'report')):
        for name in list_dir(Path(cwd, folder)):
            path = Path(cwd, folder, name)
            if path.suffix == '.md' and not path.is_symlink() and path.is_file():
                found.append((path, kind, f'{folder}/{name}'))
    return found


def write_if_changed(path, text):
    try:
        if path.exists() and path.read_text(errors='replace') == text:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f'.{path.name}.{os.getpid()}.tmp')
        temporary.write_text(text)
        os.replace(temporary, path)
    except OSError:
        pass


def memory_index(cwd):
    """The index of this project's memory and reports, refreshed for files that changed since it was last built."""
    cache = Path(cwd, INDEX_DIR, 'index.json')
    try:
        saved = json.loads(cache.read_text())
        docs = saved.get('docs', {}) if saved.get('version') == INDEX_VERSION else {}
    except (OSError, ValueError, AttributeError):
        docs = {}
    fresh, changed = {}, False
    for path, kind, rel in memory_documents(cwd):
        try:
            stat = path.stat()
        except OSError:
            continue
        old = docs.get(rel)
        if old and old.get('mtime') == stat.st_mtime and old.get('size') == stat.st_size:
            fresh[rel] = old
        else:
            fresh[rel] = index_document(path, kind, rel)
            changed = True
    changed = changed or set(fresh) != set(docs)
    if not fresh:
        return {}
    if changed or not Path(cwd, INDEX_DIR, 'CONTENTS.md').exists():
        exclude_from_git(cwd)
        write_if_changed(cache, json.dumps({'version': INDEX_VERSION, 'docs': fresh}, ensure_ascii=True, sort_keys=True))
        write_if_changed(Path(cwd, INDEX_DIR, 'CONTENTS.md'), contents_page(fresh))
    return fresh


def memory_sort_key(doc):
    return (doc['kind'] != 'memory', MEMORY_ORDER.index(doc['type']) if doc['type'] in MEMORY_ORDER else len(MEMORY_ORDER), doc['file'])


def key_terms(doc, count=8):
    joined = set(doc.get('joined', ()))
    ranked = sorted(((stem, w) for stem, w in doc['weight'].items() if stem not in joined), key=lambda item: (-item[1], item[0]))
    return [doc['display'].get(stem, stem) for stem, _ in ranked[:count]]


def contents_page(docs):
    """The readable table of contents (every entry, its summary, sections with line numbers and key terms) and the
    alphabetical index (each notable word and where it appears)."""
    ordered = sorted(docs.values(), key=memory_sort_key)
    memory = [d for d in ordered if d['kind'] == 'memory']
    reports = sorted((d for d in ordered if d['kind'] == 'report'), key=lambda d: d['file'], reverse=True)
    out = ['# Project memory: table of contents and index', '',
           f'Generated by Nocturne from `{MEMORY}/` and `{REPORTS}/`; rebuilt whenever they change, so do not edit it.',
           f'{len(memory)} memory entr{"y" if len(memory) == 1 else "ies"} · {len(reports)} flow report{"" if len(reports) == 1 else "s"}.',
           'Find the subject here, then open only the files (and lines) it points to.', '', '## Table of contents', '']
    for heading, group in (('Memory', memory), ('Flow reports', reports)):
        if not group:
            continue
        out += [f'### {heading}', '']
        for number, doc in enumerate(group, 1):
            updated = time.strftime('%Y-%m-%d', time.localtime(doc['mtime'])) if doc['mtime'] else '?'
            kind = f'{doc["type"]} · ' if doc['kind'] == 'memory' else ''
            out.append(f'{number}. **{doc["title"]}** — `{doc["file"]}` · {kind}{doc["lines"]} lines · updated {updated}')
            if doc['summary']:
                out.append(f'   {doc["summary"]}')
            sub = 0
            for section in doc['sections']:
                if section['title']:
                    sub += 1
                    indent = '   ' + '  ' * max(0, section['level'] - 2)
                    out.append(f'{indent}- {number}.{sub} {section["title"]} — line {section["line"]}')
            out.append(f'   - Key terms: {", ".join(key_terms(doc))}')
        out.append('')
    places = {}
    for doc in ordered:
        label = doc['name'] if doc['kind'] == 'memory' else f'report {Path(doc["file"]).stem}'
        notable = (set(doc['head']) | {t for x in doc['sections'] for t in x['heading']} |
                   {stem for stem, _ in sorted(doc['weight'].items(), key=lambda item: -item[1])[:30]}) - set(doc.get('joined', ()))
        for stem in notable:
            where = [f'§ {x["title"]} (line {x["line"]})' for x in doc['sections'] if x['title'] and stem in x['terms']][:3]
            places.setdefault(doc['display'].get(stem, stem), []).append(label + (' ' + ', '.join(where) if where else ''))
    out += ['## Index', '']
    for word in sorted(places, key=str.lower):
        out.append(f'- **{word}** — {"; ".join(sorted(places[word]))}')
    return '\n'.join(out) + '\n'


def score_documents(docs, terms):
    """The documents relevant to the request, with their scores. A shared word counts by where it appears (name /
    description / keywords, a heading, the text) and how rare it is across the memory. In long flow reports a word in
    the text counts fully only when it recurs, so a passing mention does not make a match. Weak matches (under 40% of
    the best one, or no solid hit) are dropped, so the hook loads what the request is about and nothing near it."""
    import math
    total = len(docs) or 1
    frequency = {}
    for doc in docs.values():
        for stem in doc['weight']:
            frequency[stem] = frequency.get(stem, 0) + 1
    scores = {}
    for rel, doc in docs.items():
        head = set(doc['head'])
        headings = {t for x in doc['sections'] for t in x['heading']}
        score = 0.0
        for stem in terms & set(doc['weight']):
            rarity = 1 + math.log(total / frequency[stem])
            if stem in head:
                score += FIELD_WEIGHT['head'] * rarity
            elif stem in headings:
                score += FIELD_WEIGHT['heading'] * rarity
            else:
                score += rarity * (min(1.0, doc['weight'][stem] / 3) if doc['kind'] == 'report' else 1.0)
        if score >= 1.0:
            scores[rel] = score
    best = max(scores.values(), default=0)
    return {rel: score for rel, score in scores.items() if score >= 0.4 * best}


def section_text(cwd, doc, terms, limit):
    """A relevant entry for the context: whole when short, else its heading block plus only the sections that match."""
    text = read_head(Path(cwd, doc['file']), limit + 1).lstrip('﻿')
    if doc['chars'] <= limit:
        return text
    lines = read_head(Path(cwd, doc['file']), 400_000).lstrip('﻿').splitlines()
    sections = doc['sections']
    keep = [i for i, x in enumerate(sections) if terms & set(x['terms'])]
    if not keep or not sections:
        return text[:limit] + '\n[truncated; read the file for the rest]'
    parts = ['\n'.join(lines[:max(0, sections[0]['line'] - 1)][:20])]
    for i in keep:
        stop = sections[i + 1]['line'] - 1 if i + 1 < len(sections) else len(lines)
        parts.append('\n'.join(lines[sections[i]['line'] - 1:stop]))
    shown = '\n'.join(part for part in parts if part.strip())[:limit]
    others = [x['title'] for j, x in enumerate(sections) if j not in keep and x['title']]
    if others:
        shown += f'\n[Only the matching sections are shown; also in this file: {", ".join(others[:8])}]'
    return shown


def memory_context(cwd, limit=9000, prompt=''):
    """The project's memory for this prompt. The hook looks the request up in the memory index first (table of contents and
    index, below), so it already knows what to load: only the matching entries (whole, or just their matching sections)
    and pointers to matching flow reports. Nothing else enters the context. Returns (context, info). Never writes memory."""
    docs = memory_index(cwd)
    memory = {rel: d for rel, d in docs.items() if d['kind'] == 'memory'}
    info = {'entries': len(memory), 'relevant': [], 'reports': [], 'loaded': 0, 'total': sum(d['chars'] for d in memory.values())}
    if not memory:
        return '', info
    terms = prompt_terms(prompt)
    scores = score_documents(docs, terms)
    relevant = sorted((rel for rel in memory if rel in scores), key=lambda rel: (-scores[rel], memory_sort_key(memory[rel])))
    reports = sorted((rel for rel in docs if rel in scores and docs[rel]['kind'] == 'report'), key=lambda rel: -scores[rel])[:3]
    info['relevant'] = [Path(rel).name for rel in relevant]
    info['reports'] = [Path(rel).name for rel in reports]
    if not relevant and not reports:
        # The hook already checked the index: nothing to load. One line, so the agent knows memory exists without reading it.
        return (f'Project memory ({MEMORY}/): Nocturne checked its index for this request and no entry matched, so none was '
                f'loaded. If the request turns out to depend on project facts, look them up in {INDEX_DIR}/CONTENTS.md '
                '(table of contents and index) and open only the entries it points to. That folder is generated: never edit it.'), info
    lines = [f'Project memory ({MEMORY}/) — facts the user confirmed for this project, picked for this request by Nocturne '
             f'from its memory index. Read them before investigating.',
             'If an entry answers the request, answer from it: do not search the web and do not re-investigate code it '
             f'already covers. Name the memory file you used ({MEMORY}/<name>.md).',
             'Search the code or the web only for what memory does not cover, and say that you are doing so. If something you '
             'find contradicts an entry, say so and update that entry. The current user prompt wins over stale memory.',
             f'Only the matching entries are loaded ({len(relevant)} of {len(memory)}). For anything else, look it up in '
             f'{INDEX_DIR}/CONTENTS.md (table of contents and index) and open only what it points to; do not read every entry.',
             'When the user supplies a lasting correction, update the matching memory entry, preserving unrelated facts. Never save secrets.',
             f'{INDEX_DIR}/ is generated by Nocturne from memory: never edit it, and look facts up in {MEMORY}/, not in the index.']
    if relevant:
        lines.append('Relevant to this request: ' + ', '.join(f'{MEMORY}/{Path(rel).name}' for rel in relevant) + '.')
    if reports:
        lines += ['', 'Flow reports about this request (open them for the traced code paths):']
        for rel in reports:
            doc = docs[rel]
            where = [f'§ {x["title"]} (line {x["line"]})' for x in doc['sections'] if x['title'] and terms & set(x['terms'])][:4]
            lines.append(f'- {rel} — {doc["title"]}' + (' · ' + ', '.join(where) if where else ''))
    budget = limit
    for rel in relevant:
        if budget < 400:
            lines.append(f'\n[More relevant entries were not loaded for space: open them through {INDEX_DIR}/CONTENTS.md.]')
            break
        text = section_text(cwd, memory[rel], terms, min(3000, budget))
        lines.append(f'\n=== {rel}  (relevant to this request) ===\n{text}')
        budget -= len(text)
        info['loaded'] += len(text)
    return '\n'.join(lines), info


def memory_notice(info):
    """The line shown in the conversation each time memory is checked, so the user can see it was read and what it cost."""
    count, relevant = info['entries'], info['relevant']
    entries = f'{count} entr{"y" if count == 1 else "ies"}'
    size = lambda chars: f'{chars / 1000:.1f}k' if chars >= 1000 else str(chars)
    if relevant:
        names = ', '.join(relevant[:3]) + (f' +{len(relevant) - 3} more' if len(relevant) > 3 else '')
        found = f'{len(relevant)} matched ({names}); loaded {size(info["loaded"])} of {size(info["total"])} chars'
    else:
        found = 'none matched this prompt, nothing loaded'
    reports = f' · {len(info["reports"])} flow report{"" if len(info["reports"]) == 1 else "s"} point here' if info['reports'] else ''
    return f'Nocturne checked the memory index: {entries}, {found}{reports}'


def compact_context(cwd, session):
    """After a compact, the session's own flow reports, loaded again so their findings are not lost. A session that
    starts or resumes gets no added context; the only skill that runs by itself is the impact check around edits."""
    mine = [r for r in list_reports(cwd) if session and r['session'] == session]
    lines = ['The conversation was just compacted. This session\'s own flow reports, loaded again so their findings',
             'are not lost (check what they cite before relying on a line that may have changed):']
    reloaded = 0
    for report in mine[:3]:
        text = read_report(cwd, report['id'])
        if text:
            lines += ['', f'=== {REPORTS}/{report["id"]}.md ===', text]
            reloaded += 1
    return ('\n'.join(lines) if reloaded else ''), reloaded


def exclude_from_git(cwd):
    """Keep .nocturne/ out of commits without touching a tracked file: .git/info/exclude is local to the clone."""
    try:
        git = Path(cwd, '.git')
        if not git.is_dir():
            return
        exclude = git / 'info' / 'exclude'
        text = exclude.read_text() if exclude.exists() else ''
        if not re.search(r'^/?\.nocturne/?$', text, re.M):
            exclude.parent.mkdir(parents=True, exist_ok=True)
            exclude.write_text(text + ('' if not text or text.endswith('\n') else '\n') + '.nocturne/\n')
    except OSError:
        pass


# Tool calls: what a call runs and what it edits

def shell_command(tool_input):
    """The shell command a tool call runs, from Claude's Bash or Codex's shell tools."""
    if not isinstance(tool_input, dict):
        return ''
    for key in ('command', 'cmd'):
        value = tool_input.get(key)
        if isinstance(value, str):
            return value
        if isinstance(value, list) and all(isinstance(v, str) for v in value):
            if len(value) >= 3 and os.path.basename(value[0]) in ('bash', 'sh', 'zsh', 'dash') and value[1] in ('-c', '-lc'):
                return value[2]
            return ' '.join(value)
    return ''


def all_strings(value):
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [s for v in value for s in all_strings(v)]
    if isinstance(value, dict):
        return [s for v in value.values() for s in all_strings(v)]
    return []


def patch_edits(patch, when='before'):
    """The files an apply_patch changes, with the lines it removes (before) or adds (after)."""
    marker = '+' if when == 'after' else '-'
    edits, current = [], None
    for line in patch.splitlines():
        header = re.match(r'^\*\*\* (Update|Delete|Add) File: (.+)$', line)
        if header:
            current = None
            if (header[1] == 'Add' and when == 'before') or (header[1] == 'Delete' and when == 'after'):
                continue
            current = {'file': header[2].strip(), 'edits': [] if header[1] == 'Update' else None}
            edits.append(current)
            continue
        if current and current['edits'] is not None and line.startswith(marker) and line[1:].strip():
            current['edits'].append(line[1:])
    return edits


def file_edits(provider, tool, tool_input, when='before'):
    """[{'file', 'edits'}]: the text each edit replaces (before) or wrote (after); edits None for a whole-file write."""
    if not isinstance(tool_input, dict):
        return []
    patches = [s for s in all_strings(tool_input) if '*** Begin Patch' in s]
    if patches:
        return [e for patch in patches for e in patch_edits(patch, when)]
    path = tool_input.get('file_path') or tool_input.get('notebook_path')
    if not isinstance(path, str):
        return []
    key = 'new_string' if when == 'after' else 'old_string'
    if tool == 'Write':
        return [{'file': path, 'edits': None}]
    if tool == 'Edit' and isinstance(tool_input.get(key), str):
        return [{'file': path, 'edits': [tool_input[key]]}]
    if tool == 'MultiEdit' and isinstance(tool_input.get('edits'), list):
        return [{'file': path, 'edits': [e[key] for e in tool_input['edits'] if isinstance(e, dict) and isinstance(e.get(key), str)]}]
    if tool == 'NotebookEdit':
        return [{'file': path, 'edits': None}]
    return []


def tool_names(tool, command, edits):
    """Names a rule's matcher can use for a call: the CLI's own, plus Bash / Edit for Codex's shell and patch tools."""
    names = {tool.lower()} if tool else set()
    if command:
        names.add('bash')
    if edits:
        names.update(('edit', 'write'))
    return names


def matches_tool(matcher, names):
    matcher = (matcher or '').strip()
    return matcher in ('', '*') or any(m.strip().lower() in names for m in matcher.split('|'))


def glob_match(pattern, path):
    path = path.replace(os.sep, '/')
    return any(fnmatch.fnmatch(candidate, p) for p in {pattern, pattern.replace('**/', '')} for candidate in (path, os.path.basename(path)))


# Built-in command guards (from Atelier's): each returns (operation, reason) or None.

READ_PREFIX = re.compile(r'^\s*(rg|grep|egrep|cat|head|tail|less|more|git|ls|echo|which|type|code)\b', re.I)
GIT_FLOW = (('commit', re.compile(r'\bgit\b[^\n&|;]*\bcommit\b', re.I)), ('push', re.compile(r'\bgit\b[^\n&|;]*\bpush\b', re.I)),
            ('pull request', re.compile(r'\bgh\b[^\n&|;]*\bpr\b[^\n&|;]*\bcreate\b', re.I)))
DB_RULES = (
    ('database client', re.compile(r'\b(psql|mysql|mariadb|mongosh|mongo|sqlite3|redis-cli|clickhouse-client|cqlsh|surreal)\b', re.I), False),
    ('migration', re.compile(r'\b(prisma\s+(migrate|db\s+(push|execute|seed))|drizzle-kit\s+(push|migrate|drop)|knex\s+(migrate|seed)|'
                             r'sequelize\s+db:|typeorm\s+(migration|schema):|alembic\s+(upgrade|downgrade)|rails\s+db:|artisan\s+(migrate|db:)|'
                             r'flyway\s+(migrate|clean)|liquibase\s+(update|rollback)|goose\s+(up|down)|atlas\s+schema|manage\.py\s+(migrate|flush|dbshell))', re.I), False),
    ('migration', re.compile(r'\b(npm|pnpm|yarn|bun)\s+(run\s+)?(db|database|migrate|migration|seed|prisma)[\w:-]*\b', re.I), False),
    ('hosted database', re.compile(r'\b(supabase\s+db|wrangler\s+d1|turso\s+db|pscale\s+|fly\s+postgres|heroku\s+pg)', re.I), False),
    ('dump or restore', re.compile(r'\b(pg_dump|pg_dumpall|pg_restore|mysqldump|mongodump|mongorestore)\b', re.I), False),
    ('SQL statement', re.compile(r'\b(drop\s+(table|database|schema|index|view)|truncate\s+(table\s+)?\w|delete\s+from\s+\w|alter\s+table\s+\w|'
                                 r'update\s+\w+\s+set\s|insert\s+into\s+\w|create\s+(table|database|schema))\b', re.I), True),
)
PACKAGE_MANAGERS = ('npm', 'pnpm', 'yarn', 'bun')
# Subcommands that change dependencies, publish, or fetch and run code from the registry (dlx). Anything else (run, test,
# build, exec, `yarn <script>`, npx, which mostly runs the project's own binaries such as `npx tsc`) needs no approval.
PACKAGE_CHANGES = {'install', 'i', 'in', 'ins', 'inst', 'add', 'remove', 'rm', 'r', 'uninstall', 'un', 'unlink', 'update', 'up',
                   'upgrade', 'upgrade-interactive', 'ci', 'clean-install', 'install-test', 'it', 'link', 'ln', 'publish',
                   'unpublish', 'dlx', 'create', 'init', 'innit', 'dedupe', 'prune', 'patch', 'global'}
REFUSAL_MEMORY = 120  # Seconds a refused command is refused again without asking, so a retry loop doesn't flood the user.
DEV_RUNNERS = ((r'\bnext\s+(dev|start)\b', 3000), (r'(?:^|\s)(?:npx\s+|pnpm\s+exec\s+|bunx\s+)?vite(?![\w.-])(?!\s+build)', 5173),
               (r'\bnuxt\s+(dev|start|preview)\b', 3000), (r'\bng\s+serve\b', 4200), (r'\breact-scripts\s+start\b', 3000),
               (r'\bastro\s+(dev|preview)\b', 4321), (r'\bgatsby\s+(develop|serve)\b', 8000), (r'\bnest\s+start\b', 3000),
               (r'\b(storybook\s+dev|start-storybook)\b', 6006), (r'\b(webpack\s+serve|webpack-dev-server)\b', 8080),
               (r'\bexpo\s+start\b', 8081), (r'\bmanage\.py\s+runserver\b', 8000), (r'\bflask\s+run\b', 5000),
               (r'\b(uvicorn|fastapi\s+dev)\b', 8000), (r'\brails\s+(s|server)\b', 3000), (r'\bhugo\s+server\b', 1313),
               (r'\b(npm|pnpm|yarn|bun)\s+(run\s+)?(dev|start|serve|preview)\b(?![:\w-])', None))


def git_flow_intent(command):
    hits = [(m.start(), name) for name, pattern in GIT_FLOW for m in [pattern.search(command)] if m]
    return min(hits)[1] if hits else None


def db_intent(command):
    searching = bool(READ_PREFIX.match(command))
    for operation, pattern, sql_only in DB_RULES:
        if (not sql_only or not searching) and pattern.search(command):
            return operation
    return None


def package_intent(command):
    for segment in (s.strip() for s in re.split(r'&&|\|\||;|\n', command)):
        if not segment or READ_PREFIX.match(segment):
            continue
        tokens = segment.split()
        manager = tokens.pop(0).lower()
        if manager not in PACKAGE_MANAGERS:
            continue
        while tokens and re.match(r'^(--?[\w-]+)(=\S*)?$', tokens[0]):
            flag = tokens.pop(0)
            if flag in ('--filter', '-F', '-C', '--dir', '--prefix', '-w', '--workspace') and tokens:
                tokens.pop(0)
        if not tokens:
            if manager in ('yarn', 'pnpm', 'bun'):
                return f'{manager} install'  # A bare `yarn` installs the dependencies.
            continue
        if tokens[0].lower() in PACKAGE_CHANGES:
            return f'{manager} {tokens[0].lower()}'
    return None


def dev_server_ports(command):
    """Ports a dev-server start would bind, or None when the command does not start one."""
    if READ_PREFIX.match(command):
        return None
    explicit = [int(p) for p in re.findall(r'(?:--port[= ]|-p\s+|\bPORT=)(\d{2,5})\b', command)]
    for pattern, port in DEV_RUNNERS:
        if re.search(pattern, command, re.I):
            return explicit or ([port] if port else [3000, 5173, 8080])
    return None


def listening(port):
    for host in ('127.0.0.1', '::1'):
        try:
            with socket.socket(socket.AF_INET6 if ':' in host else socket.AF_INET, socket.SOCK_STREAM) as probe:
                probe.settimeout(0.2)
                if probe.connect_ex((host, port)) == 0:
                    return True
        except OSError:
            continue
    return False


def current_branch(cwd):
    try:
        result = subprocess.run(['git', '-C', cwd, 'symbolic-ref', '--short', '-q', 'HEAD'], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() or None


def protected_match(branch, patterns):
    name = branch.strip().lower()
    for pattern in patterns:
        rule = pattern.strip().lower()
        if rule and (rule == name or ('*' in rule and fnmatch.fnmatch(name, rule))):
            return pattern
    return None


def guard_decisions(config, cwd, command, edits):
    """Built-in guards, in order. Each result: (hook id, 'ask' | 'deny', reason)."""
    found = []
    flow = git_flow_intent(command) if command else None
    if command and hook_on(config, 'git-flow') and flow:
        found.append(('git-flow', 'ask', f'The {flow} step needs the user\'s confirmation. Attempted: {command.strip()[:300]}. '
                                         'If it is refused, do not retry it: say the changes are ready and let the user run it.'))
    patterns = [p for p in config.get('protected_branches') or [] if isinstance(p, str) and p.strip()]
    if hook_on(config, 'protected-branch') and patterns and (edits or flow in ('commit', 'push')):
        branch = current_branch(cwd)
        matched = branch and protected_match(branch, patterns)
        if matched:
            act = 'change files on it' if edits else f'{flow} to it'
            found.append(('protected-branch', 'deny', f'"{branch}" is a protected branch, so this session may not {act} (rule "{matched}"). '
                                                      'Do not switch branches yourself and do not retry. Say what you would change and in '
                                                      'which file, and ask the user to create a working branch or remove the protection in '
                                                      'Nocturne\'s Skills & Hooks screen.'))
    operation = db_intent(command) if command and hook_on(config, 'database') else None
    if operation:
        found.append(('database', 'ask', f'Database operation ({operation}) needs the user\'s approval: {command.strip()[:300]}'))
    operation = package_intent(command) if command and hook_on(config, 'packages') and not operation else None
    if operation:
        found.append(('packages', 'ask', f'Package command ({operation}) needs the user\'s approval: {command.strip()[:300]}'))
    ports = dev_server_ports(command) if command and hook_on(config, 'dev-server') else None
    busy = [p for p in ports or [] if listening(p)]
    if busy:
        found.append(('dev-server', 'deny', f'Something is already listening on http://localhost:{busy[0]}, so this dev server would '
                                            'clash with it. Use the server that is running. If it has to restart, ask the user to stop it.'))
    return found


def run_command(argument, cwd, extra):
    try:
        result = subprocess.run(argument, shell=True, cwd=cwd, capture_output=True, text=True, timeout=15,
                                env=dict(os.environ, **{k: v or '' for k, v in extra.items()}))
    except subprocess.TimeoutExpired:
        return 124, 'Timed out after 15 seconds.'
    except OSError as error:
        return 127, str(error)
    output = (result.stdout + result.stderr).strip()
    return result.returncode, output[-1000:]


def rule_results(config, event, cwd, names=(), paths=(), prompt='', extra=None):
    """The user's own rules for one event, in order: ('block' | 'note', rule, text). An allow rule ends the list."""
    results = []
    for rule in config.get('rules') or []:
        if not isinstance(rule, dict) or not rule.get('enabled', True) or rule.get('event') != event:
            continue
        matcher = (rule.get('matcher') or '').strip()
        if event in ('pre-tool', 'post-tool'):
            if not matches_tool(matcher, names):
                continue
            if rule.get('path') and not any(glob_match(rule['path'], p) for p in paths):
                continue
        elif event == 'prompt' and matcher not in ('', '*') and matcher.lower() not in prompt.lower():
            continue
        name = rule.get('name') or 'Unnamed hook'
        action, argument = rule.get('action'), (rule.get('argument') or '').strip()
        if action == 'allow':
            break
        if action == 'block':
            results.append(('block', rule, argument or f'Blocked by the hook "{name}".'))
            break
        if action == 'annotate' and argument:
            results.append(('note', rule, argument))
        if action == 'command' and argument:
            status, output = run_command(argument, cwd, extra or {})
            if status:
                results.append(('block', rule, f'The hook "{name}" failed (exit {status}): {output}'))
                break
            if output:
                results.append(('note', rule, f'{name}: {output}'))
    return results


# Approvals for Codex, which has no "ask" of its own in a hook: Nocturne asks instead.

def ask_nocturne(cwd, provider, session, title, reason, command):
    """True when the user allowed it in Nocturne, False when refused, None when nobody answered."""
    root = cli_dir()
    if not root:
        return None
    folder = root / 'approvals'
    try:
        alive = time.time() - (root / 'alive').stat().st_mtime < 10
    except OSError:
        alive = False
    if not alive:
        return None
    refused_file = folder / 'refused.json'
    key = f'{session}:{command.strip()}'
    try:
        refused = json.loads(refused_file.read_text())
    except (OSError, ValueError):
        refused = {}
    now = time.time()
    # Only a recent refusal stands: a single "no" must never block the same command for the rest of the session.
    refused = {k: at for k, at in refused.items() if now - at < REFUSAL_MEMORY} if isinstance(refused, dict) else {}
    if key in refused:
        return False
    request_id = uuid.uuid4().hex
    request, answer = folder / f'{request_id}.json', folder / f'{request_id}.answer'
    try:
        folder.mkdir(parents=True, exist_ok=True)
        request.write_text(json.dumps({'id': request_id, 'cli': provider, 'project': cwd, 'term': os.environ.get('NOCTURNE_TERM_ID'),
                                       'title': title, 'reason': reason, 'command': command, 'at': time.time(),
                                       'expires': time.time() + APPROVAL_WAIT}))
    except OSError:
        return None
    verdict = None
    deadline = time.time() + APPROVAL_WAIT
    while time.time() < deadline:
        if answer.exists():
            verdict = answer.read_text().strip() == 'yes'
            break
        time.sleep(0.25)
    for path in (request, answer):
        try:
            path.unlink()
        except OSError:
            pass
    if verdict is False:
        try:
            refused_file.write_text(json.dumps(dict(refused, **{key: time.time()})))
        except OSError:
            pass
    return verdict


# Impact check: what an edit can break. Python is read with ast and tokenize; JS and TS with a small lexer.

JS_EXTS = ('.ts', '.tsx', '.mts', '.cts', '.js', '.jsx', '.mjs', '.cjs')
CODE_PATHSPECS = ['*.py', *(f'*{ext}' for ext in JS_EXTS)]
# Tree-sitter, through Node, parses these; without Node the impact check falls back to Python's ast and a JS/TS scanner.
TREESITTER = HERE.parent / 'treesitter'
TS_LANGS = {'.py': 'python', '.pyi': 'python', '.js': 'javascript', '.jsx': 'javascript', '.mjs': 'javascript', '.cjs': 'javascript',
            '.ts': 'typescript', '.mts': 'typescript', '.cts': 'typescript', '.tsx': 'tsx', '.go': 'go', '.rs': 'rust', '.java': 'java'}
TS_ONLY = ('.go', '.rs', '.java')
TS_FAILED = False
TEST_FILE = re.compile(r'(^|/)(__tests__|tests?)/|\.(test|spec)\.[cm]?[jt]sx?$|(^|/)test_[^/]*\.py$|_test\.py$')
SKIP_DIRS = {'.git', 'node_modules', 'dist', 'build', '.next', '.venv', 'venv', '__pycache__', '.nocturne', 'target', 'coverage'}
MAX_FILES, MAX_SITES = 50, 60
JS_KEYWORDS = {'if', 'for', 'while', 'switch', 'catch', 'function', 'return', 'with', 'else', 'do', 'try', 'new', 'typeof', 'await', 'constructor'}


def node_binary():
    return os.environ.get('NOCTURNE_NODE') or shutil.which('node')


def ts_ready():
    return not TS_FAILED and (TREESITTER / 'impact.cjs').is_file() and bool(node_binary())


def ts_run(jobs):
    """Run tree-sitter on files: [{'path', 'lang', 'names', 'skip'}] -> one result each, or None when it cannot run."""
    global TS_FAILED
    if not jobs:
        return []
    if not ts_ready():
        return None
    try:
        result = subprocess.run([node_binary(), str(TREESITTER / 'impact.cjs')], input=json.dumps({'jobs': jobs}),
                                capture_output=True, text=True, timeout=25)
        results = json.loads(result.stdout)['results'] if result.returncode == 0 else None
    except (OSError, subprocess.SubprocessError, ValueError, KeyError):
        results = None
    if results is None or len(results) != len(jobs):
        TS_FAILED = True  # Fall back to the built-in parsers for the rest of this run.
        return None
    return results


def family(path):
    """Code can only use a name from its own language family: JS and TS together, the others each on their own."""
    lang = TS_LANGS.get(os.path.splitext(path)[1].lower())
    return 'js' if lang in ('javascript', 'typescript', 'tsx') else lang


def language(path):
    ext = os.path.splitext(path)[1].lower()
    if ext in ('.py', '.pyi'):
        return 'python'
    if ext in JS_EXTS:
        return 'js'
    return TS_LANGS[ext] if ext in TS_ONLY and ts_ready() else None


def js_mask(source):
    """The source with comments and string contents blanked, newlines and offsets kept."""
    out, i, n = list(source), 0, len(source)

    def blank(start, end):
        for k in range(start, min(end, n)):
            if out[k] != '\n':
                out[k] = ' '
    while i < n:
        c = source[i]
        if c == '/' and source.startswith('//', i):
            j = source.find('\n', i)
            j = n if j == -1 else j
            blank(i, j)
            i = j
        elif c == '/' and source.startswith('/*', i):
            j = source.find('*/', i + 2)
            j = n if j == -1 else j + 2
            blank(i, j)
            i = j
        elif c in '\'"`':
            j = i + 1
            while j < n and source[j] != c:
                if source[j] == '\\':
                    j += 1
                elif c != '`' and source[j] == '\n':
                    break
                j += 1
            blank(i + 1, j)
            i = j + 1
        else:
            i += 1
    return ''.join(out)


def statement_end(masked, start):
    """The last row of the declaration that begins at offset start: its braces, or its statement."""
    depth, opened, row = 0, False, masked.count('\n', 0, start)
    line_start = start
    for k in range(start, len(masked)):
        c = masked[k]
        if c in '([{':
            depth += 1
            opened = opened or c == '{'
        elif c in ')]}':
            depth -= 1
            if depth <= 0 and opened and c == '}':
                return row
        elif c == ';' and depth == 0:
            return row
        elif c == '\n':
            text = masked[line_start:k].rstrip()
            if depth == 0 and text and text[-1] not in '=,(+-*/?:&|{[<>.' and not opened:
                return row
            row += 1
            line_start = k + 1
    return row


JS_DECLARATIONS = (
    (re.compile(r'^\s*(export\s+)?(default\s+)?(async\s+)?function\s*\*?\s*([A-Za-z_$][\w$]*)'), 'function'),
    (re.compile(r'^\s*(export\s+)?(default\s+)?(abstract\s+)?class\s+([A-Za-z_$][\w$]*)'), 'class'),
    (re.compile(r'^\s*(export\s+)?(declare\s+)?(const|let|var)\s+([A-Za-z_$][\w$]*)\s*(:[^=]+)?='), 'variable'),
    (re.compile(r'^\s*(export\s+)?(declare\s+)?(interface|type|enum)\s+([A-Za-z_$][\w$]*)'), 'type'),
)
JS_METHOD = re.compile(r'^\s*((?:public|private|protected|static|async|readonly|override|get|set)\s+)*\*?\s*([A-Za-z_$][\w$]*)\s*(<[^>]*>)?\s*\([^;]*$')


def js_symbols(source):
    masked = js_mask(source)
    lines = masked.split('\n')
    offsets, total = [], 0
    for line in lines:
        offsets.append(total)
        total += len(line) + 1
    symbols, classes = [], []
    for row, line in enumerate(lines):
        found = None
        for pattern, kind in JS_DECLARATIONS:
            match = pattern.match(line)
            if match:
                name = match[4]
                if kind == 'variable' and re.search(r'=>|\bfunction\b', line):
                    kind = 'function'
                found = (name, kind, bool(match[1]))
                break
        if not found and classes and any(c['start'] < row <= c['end'] for c in classes):
            match = JS_METHOD.match(line)
            if match and match[2] not in JS_KEYWORDS and '{' in ''.join(lines[row:row + 4]):
                found = (match[2], 'method', False)
        if not found:
            continue
        name, kind, exported = found
        end = statement_end(masked, offsets[row])
        symbol = {'name': name, 'kind': kind, 'exported': exported, 'start': row, 'end': max(end, row), 'parent': None}
        if kind == 'method':
            symbol['parent'] = next((c['name'] for c in classes if c['start'] < row <= c['end']), None)
        symbols.append(symbol)
        if kind == 'class':
            classes.append(symbol)
    exported_names = set()
    for match in re.finditer(r'export\s*\{([^}]*)\}', masked):
        exported_names.update(re.findall(r'([A-Za-z_$][\w$]*)(?:\s+as\s+[\w$]+)?', match[1]))
    exported_names.update(re.findall(r'(?:module\.)?exports\.([A-Za-z_$][\w$]*)', masked))
    if re.search(r'module\.exports\s*=', masked):
        for match in re.finditer(r'module\.exports\s*=\s*\{([^}]*)\}', masked):
            exported_names.update(re.findall(r'([A-Za-z_$][\w$]*)', match[1]))
    for symbol in symbols:
        symbol['exported'] = symbol['exported'] or (symbol['name'] in exported_names and not symbol['parent'])
        if symbol['kind'] == 'method' and len(symbol['name']) >= 6:
            symbol['exported'] = True  # A distinctive method name is worth looking for in other files.
    return symbols, masked


def py_symbols(source):
    tree = ast.parse(source)
    symbols = []

    def visit(node, parent):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                method = parent is not None and parent['kind'] == 'class'
                kind = 'class' if isinstance(child, ast.ClassDef) else 'method' if method else 'function'
                start = min([child.lineno] + [d.lineno for d in child.decorator_list]) - 1
                private = child.name.startswith('_')
                symbol = {'name': child.name, 'kind': kind, 'start': start, 'end': (child.end_lineno or child.lineno) - 1,
                          'parent': parent['name'] if parent else None,
                          'exported': not private and (parent is None or (method and len(child.name) >= 6))}
                symbols.append(symbol)
                visit(child, symbol)
            else:
                visit(child, parent)
    visit(tree, None)
    return symbols


def parse_file(path, ts=None):
    """{'path', 'source', 'lines', 'symbols', 'lang', 'masked', 'ts'} for a code file, or None. With tree-sitter the
    symbols come from its parse; `ts` is an already-run tree-sitter result for this file."""
    lang = language(path)
    if not lang:
        return None
    try:
        if os.path.getsize(path) > 2 << 20:
            return None
        source = Path(path).read_text(errors='replace').replace('\r\n', '\n')
    except OSError:
        return None
    tslang = TS_LANGS.get(os.path.splitext(path)[1].lower())
    if ts is None and tslang and ts_ready():
        ts = (ts_run([{'path': path, 'lang': tslang, 'names': None}]) or [None])[0]
    if ts and 'error' not in ts:
        return {'path': path, 'source': source, 'lines': source.split('\n'), 'symbols': ts['symbols'], 'lang': lang, 'masked': None,
                'ts': tslang, 'uses': ts.get('uses')}
    if lang not in ('python', 'js'):
        return None
    try:
        if lang == 'python':
            return {'path': path, 'source': source, 'lines': source.split('\n'), 'symbols': py_symbols(source), 'lang': lang, 'masked': None}
        symbols, masked = js_symbols(source)
        return {'path': path, 'source': source, 'lines': source.split('\n'), 'symbols': symbols, 'lang': lang, 'masked': masked}
    except (SyntaxError, ValueError, RecursionError):
        return {'path': path, 'source': source, 'lines': source.split('\n'), 'symbols': [], 'lang': lang, 'masked': None}


def enclosing(symbols, row):
    inside = [s for s in symbols if s['start'] <= row <= s['end']]
    return min(inside, key=lambda s: s['end'] - s['start'])['name'] if inside else None


def py_uses(parsed, names, skip):
    """Uses of names in Python code, from its tokens: a name in a comment or a string is not a use."""
    found = []
    try:
        tokens = [t for t in tokenize.generate_tokens(io.StringIO(parsed['source']).readline)
                  if t.type not in (tokenize.NL, tokenize.NEWLINE, tokenize.COMMENT, tokenize.INDENT, tokenize.DEDENT)]
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return found
    for i, token in enumerate(tokens):
        if token.type != tokenize.NAME or token.string not in names:
            continue
        row = token.start[0] - 1
        if any(start <= row <= end for start, end in skip) or (i and tokens[i - 1].string in ('def', 'class')):
            continue
        text = parsed['lines'][row].strip()
        kind = ('import' if re.match(r'(from|import)\s', text) else
                'call' if i + 1 < len(tokens) and tokens[i + 1].string == '(' else 'reference')
        found.append((row, kind))
    return found


def js_uses(parsed, names, skip):
    found = []
    masked = parsed['masked'] if parsed['masked'] is not None else js_mask(parsed['source'])
    pattern = re.compile(r'(?<![\w$])(' + '|'.join(re.escape(n) for n in names) + r')(?![\w$])')
    for row, line in enumerate(masked.split('\n')):
        if any(start <= row <= end for start, end in skip):
            continue
        for match in pattern.finditer(line):
            rest = line[match.end():].lstrip()
            kind = ('import' if re.match(r'\s*(import\b|export\s*\{.*\}\s*from\b)', line) or 'require(' in line else
                    'call' if rest.startswith(('(', '`')) or re.match(r'<[^<>]*>\s*\(', rest) else 'reference')
            found.append((row, kind))
    return found


def uses_in(rel, parsed, names, skip, raw=None):
    sites, seen = [], set()
    if raw is None:
        raw = py_uses(parsed, set(names), skip) if parsed['lang'] == 'python' else js_uses(parsed, names, skip)
    for row, kind in raw:
        if (row, kind) in seen:
            continue
        seen.add((row, kind))
        sites.append({'file': rel, 'line': row + 1, 'kind': kind, 'inside': None if kind == 'import' else enclosing(parsed['symbols'], row),
                      'text': parsed['lines'][row].strip()[:160]})
    return sites


def repo_root(directory):
    try:
        result = subprocess.run(['git', '-C', directory, 'rev-parse', '--show-toplevel'], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() or None


def files_mentioning(root, names, is_git):
    if not names:
        return []
    if is_git:
        argv = ['git', '-C', root, 'grep', '-l', '-I', '-w', '--untracked']
        for name in names:
            argv += ['-e', name]
        try:
            specs = CODE_PATHSPECS + ([f'*{ext}' for ext in TS_ONLY] if ts_ready() else [])
            result = subprocess.run(argv + ['--', *specs], capture_output=True, text=True, timeout=20)
        except (OSError, subprocess.SubprocessError):
            return []
        return [line for line in result.stdout.splitlines() if line and language(line)]
    pattern = re.compile(r'\b(' + '|'.join(re.escape(n) for n in names) + r')\b')
    found, seen = [], 0
    for directory, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith('.')]
        for name in files:
            if not language(name):
                continue
            seen += 1
            if seen > 4000:
                return found
            path = os.path.join(directory, name)
            try:
                if os.path.getsize(path) < 2 << 20 and pattern.search(Path(path).read_text(errors='replace')):
                    found.append(os.path.relpath(path, root))
            except OSError:
                pass
    return found


def rows_of(content, text):
    needle = text.replace('\r\n', '\n')
    index = content.find(needle) if needle.strip() else -1
    if index == -1:
        return None
    start = content.count('\n', 0, index)
    return start, start + needle.count('\n')


def touched(symbols, ranges):
    """The innermost symbols the edited rows land in."""
    found = []
    for start, end in ranges:
        hits = [s for s in symbols if s['start'] <= end and s['end'] >= start]
        for hit in hits:
            holds_another = any(o is not hit and o['end'] - o['start'] < hit['end'] - hit['start'] and o['start'] >= hit['start'] and o['end'] <= hit['end']
                                for o in hits)
            if not holds_another and hit not in found:
                found.append(hit)
    return sorted(found, key=lambda s: s['start'])


def analyze_edit(cwd, file, edits, ranges=None):
    """The impact of editing file. edits: the text each edit replaces (None for a whole-file write); ranges: rows instead."""
    path = os.path.normpath(os.path.join(cwd, file))
    parsed = parse_file(path)
    if not parsed or not parsed['symbols']:
        return None
    root = repo_root(os.path.dirname(path))
    is_git = bool(root)
    root = root or cwd
    rel = os.path.relpath(path, root).replace(os.sep, '/')
    if ranges is not None:
        symbols = touched(parsed['symbols'], ranges)
    elif edits is None:
        symbols = [s for s in parsed['symbols'] if s['exported'] and not s['parent']]
    else:
        symbols = touched(parsed['symbols'], [r for r in (rows_of(parsed['source'], e) for e in edits) if r])
    if not symbols:
        return None
    names = list(dict.fromkeys(s['name'] for s in symbols))
    exported = list(dict.fromkeys(s['name'] for s in symbols if s['exported']))
    skip = [(s['start'], s['end']) for s in symbols]
    others = [f for f in files_mentioning(root, exported, is_git) if f.replace(os.sep, '/') != rel and family(f) == family(path)]
    truncated = len(others) > MAX_FILES
    others = others[:MAX_FILES]
    # With tree-sitter, one Node run reads this file's own uses and every other file's.
    batch = None
    if parsed.get('ts'):
        jobs = [{'path': path, 'lang': parsed['ts'], 'names': names, 'skip': skip}]
        jobs += [{'path': os.path.join(root, o), 'lang': TS_LANGS[os.path.splitext(o)[1].lower()], 'names': exported, 'skip': []}
                 for o in others if os.path.splitext(o)[1].lower() in TS_LANGS]
        batch = ts_run(jobs)
        if batch is None:
            return analyze_edit(cwd, file, edits, ranges)  # Node failed: TS_FAILED is set, so this reruns on the built-in parsers.
    if batch:
        own = batch.pop(0)
        sites = uses_in(rel, parsed, names, skip, own.get('uses') or [])
        found = dict(zip([o for o in others if os.path.splitext(o)[1].lower() in TS_LANGS], batch))
    else:
        sites = uses_in(rel, parsed, names, skip)
        found = {}
    for other in others:
        result = found.get(other)
        theirs = parse_file(os.path.join(root, other), ts=result) if batch else parse_file(os.path.join(root, other))
        if theirs:
            sites += uses_in(other.replace(os.sep, '/'), theirs, exported, [], (result or {}).get('uses') if theirs.get('ts') else None)
    sites.sort(key=lambda s: (s['file'], s['line']))
    truncated = truncated or len(sites) > MAX_SITES
    return {'file': rel, 'symbols': [dict(s, start=s['start'] + 1, end=s['end'] + 1) for s in symbols], 'sites': sites[:MAX_SITES],
            'tests': sorted({s['file'] for s in sites if TEST_FILE.search(s['file'])}), 'truncated': truncated}


def format_impact(impact, when='before'):
    def describe(symbol):
        return f'{symbol["name"]} ({symbol["kind"]}{", exported" if symbol["exported"] else ""}, lines {symbol["start"]}-{symbol["end"]})'
    files = len({s['file'] for s in impact['sites']})
    lines = [f'Nocturne impact check after editing {impact["file"]}.' if when == 'after' else f'Nocturne impact check for {impact["file"]}.',
             f'{"Changed" if when == "after" else "Changing"}: ' + '; '.join(describe(s) for s in impact['symbols'])]
    if not impact['sites']:
        lines.append('Nothing else in the repo uses it.')
    else:
        lines.append(f'Used at {len(impact["sites"])} site(s) in {files} file(s){" (more not listed)" if impact["truncated"] else ""}:')
        for site in impact['sites']:
            inside = f' in {site["inside"]}' if site['inside'] else ''
            lines.append(f'- {site["file"]}:{site["line"]} {site["kind"]}{inside}: {site["text"]}')
    if impact['tests']:
        lines.append('Tests: ' + ', '.join(impact['tests']))
    lines.append('Re-check each site above against the change just made, update any that no longer fits, then run the tests above.'
                 if when == 'after' else
                 'Keep every site above working: if the signature, return value or behaviour changes, update or re-check each one. '
                 'After the fix, re-check them and run the tests above (nocturne-impact-check skill).')
    return '\n'.join(lines)


def diff_ranges(cwd):
    ranges = {}
    try:
        diff = subprocess.run(['git', '-C', cwd, 'diff', 'HEAD', '--unified=0', '--no-color', '--no-ext-diff'],
                              capture_output=True, text=True, timeout=30).stdout
    except (OSError, subprocess.SubprocessError):
        return ranges
    root = repo_root(cwd) or cwd
    current = None
    for line in diff.splitlines():
        target = re.match(r'^\+\+\+ (?:b/)?(.+)$', line)
        if target:
            current = None if target[1] == '/dev/null' else os.path.join(root, target[1])
            continue
        hunk = re.match(r'^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@', line)
        if hunk and current:
            start = max(0, int(hunk[1]) - 1)
            count = 1 if hunk[2] is None else int(hunk[2])
            ranges.setdefault(current, []).append((start, start + max(0, count - 1)))
    return ranges


def diff_check(cwd):
    reports = [format_impact(impact, 'after') for file, ranges in diff_ranges(cwd).items()
               for impact in [analyze_edit(cwd, file, [], ranges)] if impact]
    return '\n\n'.join(reports) + '\n' if reports else 'No changed Python, JS or TS function in the working tree.\n'


# Edits made by shell commands (sed -i, cat > file, scripts): snapshot the code files before, compare after.

READ_ONLY = re.compile(r'^\s*(rg|grep|egrep|fgrep|cat|head|tail|less|more|ls|echo|which|type|pwd|wc|find|tree|stat|file|du|df|env|printenv|'
                       r'cd|true|sleep|ps|date|git\s+(status|log|diff|show|grep|branch|rev-parse|ls-files|blame|remote|fetch|config\s+--get))\b', re.I)
SNAP_FILES, SNAP_KEEP, SHELL_REPORTS = 20000, 200, 8


def read_only(command):
    """True when every part of a command only reads: no redirect into a file and no writing program."""
    plain = re.sub(r'\d*>&\d|\d*>\s*/dev/null|<<-?\s*\S+', '', command)
    if '>' in plain or re.search(r'\b(sed|perl)\s+(-\w*\s+)*-\w*i|\btee\b|\bfind\b.*-(delete|exec)', plain):
        return False
    return all(READ_ONLY.match(part) for part in re.split(r'&&|\|\||[;|\n]', plain) if part.strip())


def code_files(root, is_git):
    """{relative path: [mtime_ns, size]} for the code files under root that the impact check reads."""
    if is_git:
        try:
            out = subprocess.run(['git', '-C', root, 'ls-files', '-co', '--exclude-standard', '-z'], capture_output=True, text=True, timeout=20).stdout
        except (OSError, subprocess.SubprocessError):
            return {}
        names = [n for n in out.split('\0') if n and language(n)][:SNAP_FILES]
    else:
        names = []
        for directory, dirs, files in os.walk(root):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith('.')]
            names += [os.path.relpath(os.path.join(directory, f), root) for f in files if language(f)]
            if len(names) > 4000:
                break
    found = {}
    for name in names:
        try:
            info = os.stat(os.path.join(root, name))
            found[name] = [info.st_mtime_ns, info.st_size]
        except OSError:
            pass
    return found


def snapshot_path(event):
    root = cli_dir()
    if not root:
        return None
    key = event.get('tool_use_id') or event.get('call_id') or ''
    if not isinstance(key, str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,120}', key):
        raw = f'{event.get("session_id") or ""}\0{shell_command(event.get("tool_input") or {})}'
        key = 'cmd-' + __import__('hashlib').sha1(raw.encode()).hexdigest()
    return root / 'snapshots' / f'{key}.json'


def take_snapshot(event, cwd):
    """Before a shell command: the code files' stats, plus the text of the ones already changed since HEAD."""
    path = snapshot_path(event)
    root = repo_root(cwd)
    is_git = bool(root)
    root = root or cwd
    if not path or not os.path.isdir(root):
        return
    files = code_files(root, is_git)
    dirty = {}
    if is_git:
        try:
            out = subprocess.run(['git', '-C', root, 'status', '--porcelain', '-z', '-uall'], capture_output=True, text=True, timeout=20).stdout
        except (OSError, subprocess.SubprocessError):
            out = ''
        for entry in out.split('\0'):
            name = entry[3:]
            if len(entry) > 3 and name in files and files[name][1] < 512 << 10 and len(dirty) < SNAP_KEEP:
                try:
                    dirty[name] = Path(root, name).read_text(errors='replace')
                except OSError:
                    pass
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        for old in path.parent.glob('*.json'):  # Calls that never reached post-tool.
            if time.time() - old.stat().st_mtime > 3600:
                old.unlink()
        path.write_text(json.dumps({'root': root, 'git': is_git, 'files': files, 'dirty': dirty}))
    except OSError:
        pass


def changed_rows(before, after):
    """0-based (start, end) rows of `after` that differ from `before`."""
    import difflib
    ranges, rows = [], after.splitlines()
    for tag, _, _, j1, j2 in difflib.SequenceMatcher(None, before.splitlines(), rows, autojunk=False).get_opcodes():
        if tag == 'delete':  # Lines removed: the rows on either side of the gap.
            ranges.append((max(0, j1 - 1), min(j1, len(rows) - 1)))
        elif tag != 'equal':
            ranges.append((j1, j2 - 1))
    return [r for r in ranges if r[1] >= 0]


def shell_edits(event, cwd):
    """After a shell command: ([(absolute path, rows, or None for the whole file)], number of code files it changed), or None."""
    path = snapshot_path(event)
    if not path or not path.is_file():
        return None
    try:
        snap = json.loads(path.read_text())
        path.unlink()
    except (OSError, ValueError):
        return None
    root, is_git = snap['root'], snap['git']
    now = code_files(root, is_git)
    changed = sorted(name for name, stat in now.items() if snap['files'].get(name) != stat)
    found = []
    for name in changed[:SHELL_REPORTS]:
        full = os.path.join(root, name)
        try:
            after = Path(full).read_text(errors='replace')
        except OSError:
            continue
        before = snap['dirty'].get(name)
        if before is None and is_git and name in snap['files']:
            before = git_out_text(root, 'show', f'HEAD:{name}')
        if before is None:
            found.append((full, None))  # A new file, or no git to show the old text: judge the whole file.
        elif before != after:
            rows = changed_rows(before, after)
            if rows:
                found.append((full, rows))
    return found, len(changed)


def git_out_text(root, *args):
    try:
        result = subprocess.run(['git', '-C', root, *args], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout if result.returncode == 0 else None


# Report upkeep: which of a session's reports cite files written after them.

CITATION = re.compile(r'`?([\w./@-]+\.[A-Za-z0-9]{1,6}):\d+(?:-\d+)?`?')


def stale_reports(cwd, session):
    due = []
    for report in list_reports(cwd):
        if report['session'] != session:
            continue
        try:
            text = Path(report['file']).read_text(errors='replace')
        except OSError:
            continue
        changed, latest = [], 0
        for cited in dict.fromkeys(CITATION.findall(text)):
            path = os.path.join(cwd, cited)
            try:
                mtime = os.path.getmtime(path)
            except OSError:
                continue
            if mtime > report['at'] + 1:
                changed.append(cited)
                latest = max(latest, mtime)
        if changed:
            due.append({'id': report['id'], 'changed': changed, 'at': latest})
    return due


def upkeep_request(cwd, session, provider):
    root = cli_dir()
    state_file = root / 'upkeep.json' if root else None
    try:
        asked = json.loads(state_file.read_text()) if state_file else {}
    except (OSError, ValueError):
        asked = {}
    due = [d for d in stale_reports(cwd, session) if asked.get(f'{session}:{d["id"]}') != d['at']][:3]
    if not due:
        return None
    files = list(dict.fromkeys(f for d in due for f in d['changed']))
    named = files[:6] + ([f'and {len(files) - 6} more'] if len(files) > 6 else [])
    for d in due:
        asked[f'{session}:{d["id"]}'] = d['at']
    if state_file:
        try:
            state_file.parent.mkdir(parents=True, exist_ok=True)
            state_file.write_text(json.dumps(dict(list(asked.items())[-500:])))
        except OSError:
            pass
    sigil = COMMAND.get(provider, '/')
    ids = ' '.join(d['id'] for d in due)
    return (f'Before stopping, bring your flow reports up to date: run {sigil}nocturne-init-feature update {ids}; '
            f'changed: {", ".join(named)}. Follow the skill\'s "Updating a report" section, then stop.')


# Memory upkeep: a prompt that corrects the agent or states a rule gets a short, unprinted instruction to save it, without
# duplicates. The prompt hook records when the turn began (cli/memory-turns/<session>.json); entries written after that are
# checked against the memory standard right after the tool that wrote them.

MEMORY_CUES = re.compile(r"\b(remember|note that|keep in mind|always|never|don'?t|do not|instead|actually|wrong|we use|"
                         r"prefer|from now on|should be|stop doing|not .{1,40} but)\b", re.I)
OVERLAP = 0.5  # Two memory entries sharing this share of all their words are about the same subject,
CONTAINED = 0.8  # and so is one whose words (at least 5) are nearly all inside another entry.


SECRET_PATTERNS = (
    re.compile(r'(?i)\b(password|passwd|secret|token|api[_-]?key|private[_-]?key)\b\s*[:=]\s*[\'"]?[^\s\'"`]{6,}'),
    re.compile(r'\bAKIA[0-9A-Z]{16}\b'), re.compile(r'-----BEGIN [A-Z ]*PRIVATE KEY-----'),
    re.compile(r'\b(ghp|gho|github_pat|glpat|xox[abp])[-_][A-Za-z0-9_-]{16,}'), re.compile(r'\bsk-[A-Za-z0-9_-]{20,}'),
    re.compile(r'://[^/\s:@]+:[^/\s@]+@'),  # credentials inside a connection string
)
MEMORY_LINT = (
    (re.compile(r'(?i)\b(today|yesterday|tomorrow|last (week|month)|this (week|month)|recently|just now|right now)\b'),
     'uses relative time ("{0}"); write an absolute date'),
    (re.compile(r'(?i)\b(TODO|WIP|in progress|next step|I will|I\'ll|currently working|for now)\b'),
     'reads like task progress ("{0}"); memory holds lasting facts only'),
    (re.compile(r'(?i)\b(maybe|probably|perhaps|I think|might be|not sure|seems to|apparently)\b'),
     'hedges ("{0}"); save only confirmed facts, stated plainly'),
    (re.compile(r'(?im)^\s*(update|edit|correction)\s*\d*\s*:'), 'appends an "{0}" line; correct the fact in place instead'),
)


def lint_memory_entry(path):
    """Ways a memory entry breaks the memory standard (empty when it follows it)."""
    path = Path(path)
    text = read_head(path, 20000).lstrip('﻿')
    problems = []
    if not text.startswith('---') or '\n---' not in text[3:]:
        return ['has no frontmatter block (name, description, type, keywords)']
    fields = frontmatter(text)
    body = text[text.index('\n---', 3) + 4:].strip()
    if not re.fullmatch(r'[a-z0-9]+(-[a-z0-9]+){0,6}', path.stem):
        problems.append('file name is not lowercase words joined by hyphens')
    if fields.get('name') != path.stem:
        problems.append(f'`name` must equal the file name ({path.stem})')
    description = fields.get('description', '')
    if not 20 <= len(description) <= 160:
        problems.append('`description` must state the fact in one sentence of 20 to 160 characters')
    if fields.get('type') not in MEMORY_ORDER:
        problems.append('`type` must be one of ' + ', '.join(MEMORY_ORDER))
    keywords = [k for k in re.split(r'[,\s]+', fields.get('keywords', '')) if k]
    if len(keywords) < 3:
        problems.append('`keywords` needs 3 to 8 words someone would use when asking about it')
    if not body:
        problems.append('has no body: state the fact under the frontmatter')
    elif len(body.splitlines()) > 15 or len(body) > 2000:
        problems.append('body is longer than 15 lines; keep one subject, stated briefly (split unrelated facts)')
    for pattern in SECRET_PATTERNS:
        if pattern.search(text):
            problems.append('appears to contain a secret; remove it and name where it is stored instead')
            break
    for pattern, message in MEMORY_LINT:
        found = pattern.search(body)
        if found:
            problems.append(message.format(found[0].strip().rstrip(':').strip()))
    return problems


def changed_memory(cwd, since):
    """Memory entries written since a time (this turn), as {relative path: mtime}."""
    found = {}
    folder = Path(cwd, MEMORY)
    for name in list_dir(folder):
        path = folder / name
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        if path.suffix == '.md' and mtime >= since - 0.5:  # Coarse file clocks lag a little.
            found[f'{MEMORY}/{name}'] = mtime
    return found


def memory_lint_notes(cwd, event):
    """Problems in memory entries written during this turn that were not reported yet, so the agent fixes them at once."""
    turn = read_turn(event)
    if turn is None:
        return []
    reported = turn.get('linted') or {}
    notes = []
    for rel, mtime in changed_memory(cwd, turn.get('at', 0)).items():
        if reported.get(rel) == mtime:
            continue
        reported[rel] = mtime
        problems = lint_memory_entry(Path(cwd, rel))
        if problems:
            notes.append(f'{rel} breaks the memory standard: ' + '; '.join(problems) + '.')
    write_turn(event, dict(turn, linted=reported))
    return notes


def cue_sentences(prompt, limit=4):
    """Sentences of the user's request that sound like a correction or a lasting rule."""
    sentences = re.split(r'(?<=[.!?])\s+|\n+', prompt or '')
    return [' '.join(x.split())[:200] for x in sentences if MEMORY_CUES.search(x)][:limit]


def turn_file(event):
    root, session = cli_dir(), event.get('session_id') or ''
    if not root or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', session):
        return None
    return root / 'memory-turns' / f'{session}.json'


def read_turn(event):
    path = turn_file(event)
    try:
        return json.loads(path.read_text()) if path else None
    except (OSError, ValueError):
        return None


def write_turn(event, turn, prune=False):
    path = turn_file(event)
    if path:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(turn))
            if prune:  # A new prompt: drop records of sessions idle for 14 days.
                for old in path.parent.glob('*.json'):
                    if time.time() - old.stat().st_mtime > 14 * 86400:
                        old.unlink()
        except OSError:
            pass


def entry_terms(doc):
    return set(doc['head']) | {t for x in doc['sections'] for t in x['terms']}


def overlapping_entries(docs):
    """Pairs of memory entries that cover the same subject (most of their words shared), as (a, b, share)."""
    memory = sorted((rel, entry_terms(d)) for rel, d in docs.items() if d['kind'] == 'memory')
    pairs = []
    for i, (left, a) in enumerate(memory):
        for right, b in memory[i + 1:]:
            share = len(a & b) / (len(a | b) or 1)
            inside = len(a & b) / min(len(a), len(b)) if min(len(a), len(b)) >= 5 else 0
            if share >= OVERLAP or inside >= CONTAINED:
                pairs.append((Path(left).name, Path(right).name, round(share, 2)))
    return pairs


def memory_upkeep_note(cwd, prompt, matched):
    """When a prompt corrects the agent or states a rule, the instruction to save it to memory while doing the request, or None.
    It goes in the prompt's added context, which neither CLI prints: a Stop continuation would show it in the conversation."""
    cues = cue_sentences(prompt, 2)
    if not cues:
        return None, {}
    docs = memory_index(cwd)
    memory = {rel: d for rel, d in docs.items() if d['kind'] == 'memory'}
    scores = score_documents(memory, prompt_terms(prompt)) if memory else {}
    candidates = list(dict.fromkeys(list(matched) + [Path(rel).name for rel in sorted(scores, key=lambda r: -scores[r])]))[:5]
    root = cli_dir()
    state_file = root / 'upkeep.json' if root else None
    try:
        asked = json.loads(state_file.read_text()) if state_file else {}
    except (OSError, ValueError):
        asked = {}
    pairs = []
    for left, right, share in overlapping_entries(docs):
        key = f'overlap:{left}:{right}'
        stamp = max(docs[f'{MEMORY}/{left}']['mtime'], docs[f'{MEMORY}/{right}']['mtime'])
        if asked.get(key) != stamp:
            pairs.append((left, right))
            asked[key] = stamp
    if pairs and state_file:
        try:
            state_file.parent.mkdir(parents=True, exist_ok=True)
            state_file.write_text(json.dumps(dict(list(asked.items())[-500:])))
        except OSError:
            pass
    parts = ['The user said: ' + ' / '.join(f'"{cue[:160]}"' for cue in cues) + '. If that is a lasting project fact (a correction, '
             f'environment detail, name, preference or decision), save it in {MEMORY}/ while doing the request: edit the entry that '
             'covers it' + (' (' + ', '.join(candidates) + ')' if candidates else '') + ', changing only the lines it affects; add '
             '`<kebab-name>.md` (frontmatter `name` = file name, `description` = the fact in one sentence, `type` = correction, '
             'environment, naming, preference or fact, `keywords` = 3 to 8 words) only if none does. Never save secrets.']
    parts += [f'Merge {left} into {right} (keep every distinct fact, delete the other file).' for left, right in pairs]
    parts.append('End your reply with one line: `Memory: updated|added|merged <file>` or `Memory: no change`.')
    return ' '.join(parts), {'candidates': candidates, 'pairs': pairs}


# Events

def on_session_start(event, provider, config):
    cwd = event.get('cwd') or os.environ.get('NOCTURNE_PROJECT') or os.getcwd()
    session = event.get('session_id') or event.get('sessionId')
    source = event.get('source') or 'startup'
    exclude_from_git(cwd)
    if source != 'compact':
        return {}  # Starting, resuming or clearing a session loads nothing.
    if not hook_on(config, 'session-context'):
        log({'cli': provider, 'event': 'session-start', 'hook': 'session-context', 'outcome': 'off'})
        return {}
    context, reloaded = compact_context(cwd, session)
    if not reloaded:
        return {}
    log({'cli': provider, 'event': 'session-start', 'hook': 'session-context', 'outcome': 'context', 'detail': f'{reloaded} report(s) loaded again after a compact'})
    return {'systemMessage': f'Nocturne: loaded {reloaded} flow report(s) again after the compact',
            'hookSpecificOutput': {'hookEventName': 'SessionStart', 'additionalContext': context}}


def on_prompt(event, provider, config):
    cwd = event.get('cwd') or os.getcwd()
    prompt = event.get('prompt') if isinstance(event.get('prompt'), str) else ''
    results = rule_results(config, 'prompt', cwd, prompt=prompt, extra={'NOCTURNE_PROMPT': prompt[:4000]})
    for verdict, rule, text in results:
        log({'cli': provider, 'event': 'prompt', 'hook': rule.get('name'), 'outcome': 'blocked' if verdict == 'block' else 'note', 'detail': text[:300]})
        if verdict == 'block':
            return {'decision': 'block', 'reason': text}
    notes = [text for verdict, _, text in results if verdict == 'note']
    notice = None
    info = {'relevant': []}
    if hook_on(config, 'project-memory'):
        memory, info = memory_context(os.environ.get('NOCTURNE_PROJECT') or cwd, prompt=prompt)
        if memory:
            notes.insert(0, memory)  # First, so the model reads it before anything else.
            notice = memory_notice(info)  # Shown in the conversation (systemMessage) by both CLIs.
            log({'cli': provider, 'event': 'prompt', 'hook': 'project-memory', 'outcome': 'context',
                 'detail': f'{info["entries"]} entries, {len(info["relevant"])} relevant, {info["loaded"]} of {info["total"]} chars loaded'})
    if hook_on(config, 'memory-upkeep') and provider in ('claude', 'codex'):  # Nocturne Agent keeps its own turn loop.
        write_turn(event, {'at': time.time()}, prune=True)  # Entries written from now on are checked against the standard.
        request, found = memory_upkeep_note(os.environ.get('NOCTURNE_PROJECT') or cwd, prompt, info['relevant'])
        if request:
            notes.insert(1 if notice else 0, request)
            log({'cli': provider, 'event': 'prompt', 'hook': 'memory-upkeep', 'outcome': 'context',
                 'detail': f'candidates: {", ".join(found["candidates"]) or "none"}; overlaps: {len(found["pairs"])}'})
    if hook_on(config, 'impact-check'):
        notes.append('This session uses Nocturne\'s automatic impact check before and after edits. '
                     'Follow its Nocturne impact check reports. nocturne-impact-check is a skill (not a shell command) for a '
                     'final sweep, only when the user asks for one. '
                     'Do not implicitly invoke atelier-impact-check or its scripts in this session; '
                     'it belongs to a different application. No impact skill needs to be loaded before editing.')
    if not notes:
        return {}
    output = {'hookSpecificOutput': {'hookEventName': 'UserPromptSubmit', 'additionalContext': '\n'.join(notes)}}
    if notice:
        output['systemMessage'] = notice
    return output


def on_pre_tool(event, provider, config):
    cwd = event.get('cwd') or os.getcwd()
    tool = event.get('tool_name') or ''
    tool_input = event.get('tool_input') or {}
    session = event.get('session_id') or ''
    command = shell_command(tool_input)
    edits = file_edits(provider, tool, tool_input)
    paths = [os.path.relpath(os.path.join(cwd, e['file']), cwd) for e in edits]
    if isinstance(tool_input, dict):
        paths += [tool_input[k] for k in ('file_path', 'path', 'notebook_path') if isinstance(tool_input.get(k), str)]

    def deny(reason, hook):
        log({'cli': provider, 'event': 'pre-tool', 'tool': tool, 'hook': hook, 'outcome': 'blocked', 'detail': reason[:300]})
        return {'hookSpecificOutput': {'hookEventName': 'PreToolUse', 'permissionDecision': 'deny', 'permissionDecisionReason': reason}}
    guards = list(guard_decisions(config, cwd, command, edits))
    if provider == 'nocturne':
        denied = [reason for _, decision, reason in guards if decision == 'deny']
        if denied:
            return deny('\n'.join(denied), 'nocturne')
        reasons = [reason for _, _, reason in guards]
        if reasons and not event.get('nocturne_approved'):
            return {'hookSpecificOutput': {'hookEventName': 'PreToolUse', 'permissionDecision': 'ask',
                                           'permissionDecisionReason': '\n'.join(reasons)}}
    for hook, decision, reason in guards:
        if decision == 'deny':
            return deny(reason, hook)
        if provider == 'nocturne':
            continue  # The native runtime confirms the guards above, then still runs rules and the impact check.
        if provider == 'claude':
            log({'cli': provider, 'event': 'pre-tool', 'tool': tool, 'hook': hook, 'outcome': 'asked', 'detail': reason[:300]})
            return {'hookSpecificOutput': {'hookEventName': 'PreToolUse', 'permissionDecision': 'ask', 'permissionDecisionReason': reason}}
        name = next(h['name'] for h in BUILTIN_HOOKS if h['id'] == hook)
        verdict = ask_nocturne(cwd, provider, session, name, reason, command)
        if verdict is not True:
            return deny(reason.rstrip('.') + ('. The user refused it.' if verdict is False else
                                              '. ' +
                                              'Nocturne could not ask the user (no answer), so it was refused. Tell the user the command and let them run it.'), hook)
        log({'cli': provider, 'event': 'pre-tool', 'tool': tool, 'hook': hook, 'outcome': 'approved', 'detail': command[:300]})
    names = tool_names(tool, command, edits)
    notes = []
    for verdict, rule, text in rule_results(config, 'pre-tool', cwd, names, paths,
                                             extra={'NOCTURNE_TOOL': tool, 'NOCTURNE_PATH': paths[0] if paths else '', 'NOCTURNE_COMMAND': command}):
        if verdict == 'block':
            return deny(text, rule.get('name'))
        notes.append(text)
        log({'cli': provider, 'event': 'pre-tool', 'tool': tool, 'hook': rule.get('name'), 'outcome': 'note', 'detail': text[:300]})
    # The one skill that runs by itself: before an edit, every place that uses the code it changes, to avoid regressions.
    if command and not edits and hook_on(config, 'impact-check') and not read_only(command):
        take_snapshot(event, cwd)  # A shell command may edit code too; post-tool compares.
    if edits and hook_on(config, 'impact-check'):
        for edit in edits:
            impact = analyze_edit(cwd, edit['file'], edit['edits'])
            if impact:
                notes.append(format_impact(impact, 'before'))
                log({'cli': provider, 'event': 'pre-tool', 'tool': tool, 'hook': 'impact-check', 'outcome': 'context',
                     'detail': f'{impact["file"]}: {", ".join(s["name"] for s in impact["symbols"])}, {len(impact["sites"])} site(s)'})
    return {'hookSpecificOutput': {'hookEventName': 'PreToolUse', 'additionalContext': '\n\n'.join(notes)}} if notes else {}


def on_post_tool(event, provider, config):
    cwd = event.get('cwd') or os.getcwd()
    turn = read_turn(event)
    tool = event.get('tool_name') or ''
    tool_input = event.get('tool_input') or {}
    command = shell_command(tool_input)
    edits = file_edits(provider, tool, tool_input, 'after')
    paths = [os.path.relpath(os.path.join(cwd, e['file']), cwd) for e in edits]
    notes = []
    for verdict, rule, text in rule_results(config, 'post-tool', cwd, tool_names(tool, command, edits), paths,
                                             extra={'NOCTURNE_TOOL': tool, 'NOCTURNE_PATH': paths[0] if paths else '', 'NOCTURNE_COMMAND': command}):
        log({'cli': provider, 'event': 'post-tool', 'tool': tool, 'hook': rule.get('name'), 'outcome': 'flagged' if verdict == 'block' else 'note',
             'detail': text[:300]})
        if verdict == 'block':
            return {'decision': 'block', 'reason': text}
        notes.append(text)
    if turn is not None and hook_on(config, 'memory-upkeep'):
        for note in memory_lint_notes(os.environ.get('NOCTURNE_PROJECT') or cwd, event):
            notes.append(note + ' Fix this entry now.')
            log({'cli': provider, 'event': 'post-tool', 'hook': 'memory-upkeep', 'outcome': 'flagged', 'detail': note[:300]})
    if edits and hook_on(config, 'impact-check'):
        for edit in edits:
            impact = analyze_edit(cwd, edit['file'], edit['edits'])
            if impact:
                notes.append(format_impact(impact, 'after'))
    elif command and hook_on(config, 'impact-check'):
        result = shell_edits(event, cwd)
        if result:
            found, total = result
            for file, rows in found:
                impact = analyze_edit(cwd, file, None, rows)
                if impact:
                    notes.append('The shell command changed this file.\n' + format_impact(impact, 'after'))
                    log({'cli': provider, 'event': 'post-tool', 'tool': tool, 'hook': 'impact-check', 'outcome': 'context',
                         'detail': f'shell edit {impact["file"]}: {", ".join(s["name"] for s in impact["symbols"])}, {len(impact["sites"])} site(s)'})
            if total > SHELL_REPORTS:
                notes.append(f'The shell command changed {total} code files; only the first {SHELL_REPORTS} were checked. '
                             'Run the nocturne-impact-check skill for the rest.')
    return {'hookSpecificOutput': {'hookEventName': 'PostToolUse', 'additionalContext': '\n\n'.join(notes)}} if notes else {}


def on_stop(event, provider, config):
    cwd = event.get('cwd') or os.getcwd()
    session = event.get('session_id') or ''
    root, term = cli_dir(), os.environ.get('NOCTURNE_TERM_ID', '')
    if root and re.fullmatch(r'[A-Za-z0-9_-]{1,80}', term):
        try:
            (root / 'turns').mkdir(parents=True, exist_ok=True)
            (root / 'turns' / term).write_text(json.dumps({'at': time.time(), 'cli': provider, 'session': session}))
        except OSError:
            pass
    if event.get('stop_hook_active'):
        return {}  # Already continuing because of a stop hook: never twice in a row.
    for verdict, rule, text in rule_results(config, 'stop', cwd, extra={'NOCTURNE_SESSION': session}):
        log({'cli': provider, 'event': 'stop', 'hook': rule.get('name'), 'outcome': 'continued' if verdict == 'block' else 'note', 'detail': text[:300]})
        if verdict == 'block':
            return {'decision': 'block', 'reason': text}
    if not session or (provider == 'nocturne' and event.get('nocturne_conversation')):
        return {}
    requests, kept = [], []
    if hook_on(config, 'report-upkeep') and any(s['name'] == 'nocturne-init-feature' for s in list_skills()):
        request = upkeep_request(cwd, session, provider)
        if request:
            log({'cli': provider, 'event': 'stop', 'hook': 'report-upkeep', 'outcome': 'continued', 'detail': request[:300]})
            requests.append(request)
            kept.append('flow reports')
    if not requests:
        return {}
    # One continuation for everything due: the stop_hook_active guard allows only one in a row.
    return {'decision': 'block', 'reason': '\n\n'.join(requests), 'systemMessage': f'Nocturne: keeping {" and ".join(kept)} current'}


HANDLERS = {'session-start': on_session_start, 'prompt': on_prompt, 'pre-tool': on_pre_tool, 'post-tool': on_post_tool, 'stop': on_stop}


def main(argv):
    if argv[1:2] == ['--diff']:
        sys.stdout.write(diff_check(os.getcwd()))
        return
    handler = HANDLERS.get(argv[1] if len(argv) > 1 else '')
    provider = argv[2] if len(argv) > 2 else 'claude'
    global PROJECT
    output = {}
    if handler:
        try:
            event = parse_event(read_stdin())
            PROJECT = event.get('cwd') if isinstance(event.get('cwd'), str) else os.getcwd()
            output = handler(event, provider, settings(strict=provider == 'nocturne'))
            if provider == 'nocturne':
                output = dict(output, nocturne_ok=True)
        except Exception as error:
            log({'cli': provider, 'event': argv[1], 'hook': 'nocturne', 'outcome': 'error', 'detail': f'{type(error).__name__}: {error}'[:300]})
            # Native execution requires an explicit failure; vendor CLI hooks keep their existing protocol.
            output = {'nocturne_error': True} if provider == 'nocturne' else {}
    elif provider == 'nocturne':
        output = {'nocturne_error': True}
    emit(output)


if __name__ == '__main__':
    main(sys.argv)
