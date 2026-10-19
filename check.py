import tempfile
from pathlib import Path
import nocturne as n

temp = Path(tempfile.mkdtemp())
n.STORE = temp / 'workspace.json'
alpha, beta = temp / 'alpha', temp / 'beta'
alpha.mkdir(); beta.mkdir()
# Agent resume commands and Codex session discovery, against temporary agent homes.
import json, os, re, time
os.environ['NOCTURNE_APPEARANCE'] = 'dark'  # The desktop's Light / Dark must not change the test or preview.png.
os.environ['CLAUDE_CONFIG_DIR'] = str(temp / 'claude')
os.environ['CODEX_HOME'] = str(temp / 'codex')
record = {'kind': 'claude', 'name': 'feature a', 'id': None}
assert '--session-id' in n.agent_command(record) and record['id'], 'New Claude tab should get its own id'
(temp / 'claude/projects/x').mkdir(parents=True)
(temp / 'claude/projects/x' / f"{record['id']}.jsonl").write_text('{}')
assert n.agent_command(record) == f"claude --resume {record['id']} -n 'feature a'", n.agent_command(record)
assert n.agent_command({'kind': 'codex', 'name': None, 'id': 'abc'}) == 'codex resume abc'
# Renaming an agent renames its real conversation: Claude reads the last custom-title, Codex the last session_index entry.
assert 'Renamed the Claude session' in n.rename_agent_session(record, 'renamed a')
last = (temp / 'claude/projects/x' / f"{record['id']}.jsonl").read_text().splitlines()
assert last[0] == '{}' and json.loads(last[-1]) == {'type': 'custom-title', 'customTitle': 'renamed a', 'sessionId': record['id']}, last
(temp / 'codex').mkdir()
assert 'Renamed the Codex session' in n.rename_agent_session({'kind': 'codex', 'name': None, 'id': 'abc'}, 'review pass')
entry = json.loads((temp / 'codex/session_index.jsonl').read_text().splitlines()[-1])
assert entry['id'] == 'abc' and entry['thread_name'] == 'review pass' and entry['updated_at'].endswith('Z'), entry
assert 'first message' in n.rename_agent_session({'kind': 'codex', 'name': None, 'id': None}, 'later')
assert n.rename_agent_session({'kind': 'shell', 'name': None, 'id': None}, 'x') is None
assert n.agent_command({'kind': 'codex', 'name': None, 'id': None}) == 'codex'
assert n.agent_command({'kind': 'shell', 'name': None, 'id': None}) is None
assert n.clean_sessions([{'kind': 'codex', 'id': 'x'}, {'kind': 'bogus'}, 'junk']) == [{'kind': 'codex', 'name': None, 'id': 'x'}]
# Skills and hooks reach each CLI on its own command line; Codex's $ menu gets links into the app and nothing else.
import sys
launch = n.agent_launch('claude', 'claude --session-id x')
assert launch.startswith('claude --add-dir ') and ' --settings ' in launch and launch.endswith('--session-id x'), launch
settings = json.loads((n.cli_dir() / 'claude-settings.json').read_text())
assert set(settings['hooks']) == {'SessionStart', 'UserPromptSubmit', 'PreToolUse', 'PostToolUse', 'Stop'}, settings
launch = n.agent_launch('codex', 'codex resume abc')
assert launch.startswith('codex -c features.hooks=true') and 'hooks.PreToolUse=' in launch and launch.endswith(' resume abc'), launch
assert n.agent_launch('shell', 'git status') == 'git status'
# Gateway routes keep multiple providers server-side and give each client only the gateway credential.
gateway = n.validate_gateway({'base_url': 'http://127.0.0.1:4000/v1/', 'token': 'sk-test-gateway', 'routes': [
    {'name': 'claude-test', 'model': 'anthropic/test-model', 'key': 'provider-secret-a', 'client': 'claude'},
    {'name': 'coding-test', 'model': 'openai/test-model', 'key': 'os.environ/TEST_PROVIDER_KEY', 'client': 'codex'}]})
assert gateway['base_url'] == 'http://127.0.0.1:4000'
n.private_json(n.STORE.with_name('gateway.json'), gateway)
assert n.STORE.with_name('gateway.json').stat().st_mode & 0o777 == 0o600
assert n.gateway_config() == gateway
server_config = n.gateway_server_config(gateway)
assert [r['litellm_params']['model'] for r in server_config['model_list']] == ['anthropic/test-model', 'openai/test-model']
assert server_config['model_list'][1]['litellm_params']['api_key'] == 'os.environ/TEST_PROVIDER_KEY'
assert server_config['general_settings']['master_key'] == 'sk-test-gateway'
assert 'fallbacks' not in server_config and 'database_url' not in server_config['general_settings']
for url in ('http://remote.example.com', 'https://user:secret@example.com', 'https://example.com/?key=secret', 'file:///tmp/key'):
    try:
        n.gateway_url(url)
        raise AssertionError('Unsafe gateway URL accepted')
    except ValueError:
        pass
try:
    n.validate_gateway(dict(gateway, routes=[gateway['routes'][0], gateway['routes'][0]]))
    raise AssertionError('Duplicate model route accepted')
except ValueError:
    pass
saved_gateway = {'kind': 'codex', 'name': None, 'id': 'abc', 'gateway': 'coding-test'}
assert n.clean_sessions([saved_gateway]) == [saved_gateway]
for kind, route in zip(('claude', 'codex'), gateway['routes']):
    connection = n.gateway_route(route['name'], kind)
    command = n.agent_launch(kind, kind + ' resume abc', connection)
    env = n.gateway_environment(kind, *connection)
    assert 'sk-test-gateway' not in command and 'provider-secret-a' not in command
    assert 'provider-secret-a' not in json.dumps(env)
    if kind == 'claude':
        args = n.shlex.split(command)
        path = Path(args[args.index('--settings') + 1])
        settings_gateway = json.loads(path.read_text())
        assert settings_gateway['hooks'] == settings['hooks']
        assert settings_gateway['env']['ANTHROPIC_MODEL'] == 'claude-test'
        assert settings_gateway['env']['ANTHROPIC_AUTH_TOKEN'] == 'sk-test-gateway'
        assert settings_gateway['env']['ANTHROPIC_API_KEY'] == ''
        assert path.stat().st_mode & 0o777 == 0o600
    else:
        assert 'hooks.PreToolUse=' in command and 'wire_api=' in command and 'responses' in command
        assert 'env_key=' in command and env == {'NOCTURNE_GATEWAY_KEY': 'sk-test-gateway'}
try:
    n.gateway_route('removed-route', 'codex')
    raise AssertionError('A missing gateway route must not fall back to a direct provider')
except ValueError:
    pass
# Context optimization: immutable transcript, Unicode budgets, pinned constraints and valid tool pairs.
def context_read(number, path='lib.py', content=None):
    output = content if content is not None else '\n'.join(f'{i}: def function_{i}(): return {i}' for i in range(1, 501)) + '\n[500 total lines]'
    return [dict(type='function_call', call_id=str(number), name='read_file',
                 arguments=json.dumps(dict(path=path, start_line=1, line_count=500))),
            dict(type='function_call_output', call_id=str(number), output=output)]
context_input = [dict(type='message', role='user', content='Fix lib.py without changing its public interface.')]
for number in range(12): context_input += context_read(number)
context_input += [dict(type='message', role='user', content='Now inspect lib.py and preserve my local changes.')]
context_input += context_read(12)
context_original = json.dumps(context_input)
prepared, measure = n.agent_context(context_input, 'RULE: never overwrite unrelated work.', n.native_tools(), 'inspect lib.py')
assert json.dumps(context_input) == context_original, 'Context assembly mutated the durable transcript'
assert measure['after_upper_bound'] < measure['before_upper_bound'] // 4, measure
assert prepared[-1] == context_input[-1], 'Recent relevant source was lost despite available budget'
assert [x for x in prepared if x.get('role') == 'user'] == [x for x in context_input if x.get('role') == 'user']
assert [x for x in prepared if x.get('type') == 'function_call'] == [x for x in context_input if x.get('type') == 'function_call']
assert {x['call_id'] for x in prepared if x.get('type') == 'function_call_output'} == {x['call_id'] for x in prepared if x.get('type') == 'function_call'}
# Later mutations invalidate earlier source, even when the read would fit the budget.
stale = context_read(1) + [dict(type='function_call', call_id='edit', name='edit_file', arguments=json.dumps({'path': 'lib.py'})),
                          dict(type='function_call_output', call_id='edit', output='Edited lib.py; verification pending.')]
assert 'invalidated' in n.agent_context(stale)[0][1]['output']
assert n.agent_context(stale)[0][-1] == stale[-1]
# Most recent duplicate remains; paths differ so equal file contents never become false duplicates.
repeat = context_read(1) + context_read(2)
assert 'duplicate' in n.agent_context(repeat)[0][1]['output']
different = context_read(1, 'a.py') + context_read(2, 'b.py')
assert n.agent_context(different)[0] == different
# Bounded excerpts retain line attribution and prefer the relevant path to unrelated source.
ranked = [dict(type='message', role='user', content='Fix lib.py')] + context_read(1, 'unrelated.py') + context_read(2)
ranked_result, ranked_metrics = n.agent_context(ranked, prompt='lib.py', limit=27000, reserve=4096)
assert 'excerpt' in ranked_result[2]['output'] and ranked_result[-1] == ranked[-1], ranked_metrics
assert ranked_metrics['after_upper_bound'] <= ranked_metrics['input_budget']
# Hook guidance, tool failures, command exits and refusals are never cleared.
protected = [dict(type='message', role='user', content='old')]
for number, content in enumerate(('Blocked: forbidden', 'The user refused', 'Tool failed: bad path',
                                  '1: code\n[1 total lines]\n\ncustom guard guidance')):
    protected += context_read(number, content=content)
protected += [dict(type='message', role='user', content='new')]
assert n.agent_context(protected)[0] == protected
unicode_items = [dict(type='message', role='user', content='代码🙂' * 20000)]
try:
    n.agent_context(unicode_items)
    raise AssertionError('Oversized pinned user request escaped the context budget')
except n.AgentRuntimeError:
    pass
print('PASS: context budgets, stale reads, relevance, deduplication, immutable transcripts and pinned safety; fixture upper estimates:', measure)

# Exercise both real HTTP protocols, authentication, provider failures and redirect credential handling.
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
requests = []
class GatewayStub(BaseHTTPRequestHandler):
    status = 200
    native_steps = []
    stream_gate = None
    header_gate = None
    headers_started = None
    scope_text = None
    incomplete = False
    usage = None
    session_headers = []
    def log_message(self, *_):
        pass
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps({'data': [{'id': 'coding-test'}, {'id': 'claude-test'}]}).encode())
    def do_POST(self):
        payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        requests.append((self.path, self.headers['Authorization'], payload))
        self.session_headers.append(self.headers.get('x-session-id'))
        if self.header_gate is not None:
            self.headers_started.set()
            self.header_gate.wait(10)
        if self.path.endswith('/responses') and payload.get('stream'):
            scope = any('NOCTURNE_SCOPE' in str(m.get('content', '')) for m in payload.get('input', []) if isinstance(m, dict))
            if scope:
                prompt = payload['input'][-1]['content']
                casual = prompt in ('hi', 'HI!!!', 'hello', 'hey...', 'good morning', 'thanks', 'thank you!',
                                    'can you help me code?', 'Could you help with programming?', 'What can you do?')
                message = {'role': 'assistant', 'content': self.scope_text if self.scope_text is not None else json.dumps({'needs_project': not casual, 'reply': 'What would you like to build or fix?'})}
            else:
                message = self.native_steps.pop(0) if self.native_steps else {'role': 'assistant', 'content': 'ok'}
            output = []
            if message.get('content'):
                output.append({'type': 'message', 'role': 'assistant', 'id': 'msg_test', 'status': 'completed',
                    'content': [{'type': 'output_text', 'text': message['content'], 'annotations': []}]})
            for call in message.get('tool_calls') or []:
                output.append({'type': 'function_call', 'id': 'fc_' + call['id'], 'call_id': call['id'],
                    'name': call['function']['name'], 'arguments': call['function']['arguments'], 'status': 'completed'})
            self.send_response(self.status)
            self.send_header('Content-Type', 'text/event-stream')
            self.end_headers()
            events = []
            if message.get('content'):
                events.append({'type': 'response.output_text.delta', 'delta': message['content'], 'sequence_number': 0})
            events.append({'type': 'response.incomplete' if self.incomplete else 'response.completed', 'sequence_number': 1,
                'response': {'id': 'resp_test', 'object': 'response', 'status': 'incomplete' if self.incomplete else 'completed', 'output': output, **({'usage': self.usage} if self.usage is not None else {})}})
            for event in events:
                try:
                    self.wfile.write(('data: ' + json.dumps(event) + '\n\n').encode()); self.wfile.flush()
                    if not scope and self.stream_gate is not None:
                        self.stream_gate.wait(15)
                except (BrokenPipeError, ConnectionResetError):
                    return
            return
        if self.path.endswith('chat/completions'):
            message = self.native_steps.pop(0) if self.native_steps and payload.get('tools') else {'role': 'assistant', 'content': 'ok'}
            self.send_response(self.status)
            self.send_header('Content-Type', 'text/event-stream' if payload.get('stream') else 'application/json')
            self.end_headers()
            if payload.get('stream'):
                delta = dict(message)
                if delta.get('tool_calls'):
                    delta['tool_calls'] = [dict(c, index=i) for i, c in enumerate(delta['tool_calls'])]
                for chunk in ({'choices': [{'delta': delta, 'finish_reason': None}]},
                              {'choices': [{'delta': {}, 'finish_reason': 'tool_calls' if message.get('tool_calls') else 'stop'}]}):
                    try:
                        self.wfile.write(('data: ' + json.dumps(chunk) + '\n\n').encode())
                        self.wfile.flush()
                        if self.stream_gate is not None:
                            self.stream_gate.wait(15)
                    except (BrokenPipeError, ConnectionResetError):
                        return
                try:
                    self.wfile.write(b'data: [DONE]\n\n')
                except (BrokenPipeError, ConnectionResetError):
                    pass
            else:
                self.wfile.write(json.dumps({'choices': [{'message': message}]}).encode())
            return
        self.send_response(self.status)
        self.send_header('Content-Type', 'application/json')
        if self.status == 307:
            self.send_header('Location', '/credential-leak')
        self.end_headers()
        self.wfile.write(json.dumps({'type': 'message', 'content': []} if self.path.endswith('messages')
                                   else {'object': 'response', 'output': []}).encode())
stub = ThreadingHTTPServer(('127.0.0.1', 0), GatewayStub)
thread = threading.Thread(target=stub.serve_forever, daemon=True)
thread.start()
try:
    connection = dict(gateway, base_url=f'http://127.0.0.1:{stub.server_port}')
    for route, endpoint in zip(gateway['routes'], ('/v1/messages', '/v1/responses')):
        assert n.probe_gateway(connection, route).startswith('Connected:')
        assert requests[-1][0:2] == (endpoint, 'Bearer sk-test-gateway')
        assert requests[-1][2]['model'] == route['name']
    GatewayStub.status = 401
    assert 'HTTP 401' in n.probe_gateway(connection, route)
    GatewayStub.status = 307
    count = len(requests)
    assert 'HTTP 307' in n.probe_gateway(connection, route) and len(requests) == count + 1
    GatewayStub.status = 200
    assert n.gateway_models(connection) == ['claude-test', 'coding-test']
    assert n.probe_gateway(connection, {'client': 'nocturne', 'name': 'coding-test'}).startswith('Connected:')
    assert requests[-1][0] == '/v1/responses', 'The native connection test must verify the SDK protocol'
    # A complete native agent turn reads, reviews impact, edits and tests through its own streamed tool loop.
    native_root = temp / 'native-project'
    native_root.mkdir()
    (native_root / 'lib.py').write_text('def double(value):\n    return value * 2\n')
    (native_root / 'use.py').write_text('from lib import double\nprint(double(3))\n')
    outputs, approvals = [], []
    native = n.NocturneAgent(native_root, 'native-test', emit=outputs.append,
                            approve=lambda text: approvals.append(text) or True)
    native.model = 'coding-test'
    def native_call(name, args, number):
        return {'role': 'assistant', 'content': None, 'tool_calls': [{'id': f'call_{number}', 'type': 'function',
            'function': {'name': name, 'arguments': json.dumps(args)}}]}
    edit_args = {'path': 'lib.py', 'old_string': 'return value * 2', 'new_string': 'return value * 3'}
    GatewayStub.native_steps = [native_call('read_file', {'path': 'lib.py'}, 1),
        native_call('edit_file', edit_args, 2), native_call('edit_file', edit_args, 3),
        native_call('run_shell', {'command': 'python3 use.py'}, 4),
        {'role': 'assistant', 'content': 'Changed the multiplier and checked the caller.'}]
    native.turn('Change the multiplier to three and test its caller.', connection)
    assert 'return value * 3' in (native_root / 'lib.py').read_text()
    results = [m['content'] for m in native.messages if m['role'] == 'tool']
    assert results[1].startswith('NOT EXECUTED') and 'use.py' in results[1], results
    assert results[2].startswith('Edited') and 'after editing' in results[2], results
    assert 'Exit 0\n9' in results[3] and len(approvals) == 1, results
    native.approve = lambda _: False
    assert 'refused' in native.execute_tool('run_shell', {'command': 'touch denied.txt'})
    assert not (native_root / 'denied.txt').exists()
    assert 'refused' in native.execute_tool('run_shell', {'command': 'git commit -m refused'})
    assert not list((n.cli_dir() / 'snapshots').glob('*.json')), 'A refused shell command leaked its impact snapshot'
    assert native.path.stat().st_mode & 0o777 == 0o600
    resumed = n.NocturneAgent(native_root, 'native-test', emit=outputs.append)
    assert resumed.model == native.model and resumed.messages == native.messages
    resumed.messages.append(native_call('write_file', {'path': 'must-not-run.txt', 'content': 'x'}, 99))
    resumed.save()
    interrupted = n.NocturneAgent(native_root, 'native-test', emit=outputs.append)
    assert interrupted.messages[-1]['role'] == 'tool' and 'Interrupted' in interrupted.messages[-1]['content']
    assert not (native_root / 'must-not-run.txt').exists(), 'Restoring a session executed a pending tool call'
    try:
        native.project_path('../outside-project')
        raise AssertionError('Native file tools escaped their project')
    except ValueError:
        pass
    try:
        native.execute_tool('write_file', {'path': 'lib.py', 'content': 'discard user work'})
        raise AssertionError('Whole-file tool overwrote an existing file')
    except ValueError:
        pass
    # Layer 1: real transport/hook cancellation, serialized state and crash recovery.
    def runtime_agent(name):
        agent = n.NocturneAgent(native_root, name, emit=lambda _: None, approve=lambda _: True)
        agent.model = 'coding-test'
        return agent
    def run_thread(action):
        errors = []
        def run():
            try:
                action()
            except BaseException as error:
                errors.append(error)
        worker = threading.Thread(target=run)
        worker.start()
        return worker, errors
    def wait_for(predicate, seconds=5):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if predicate():
                return True
            time.sleep(.01)
        return False
    # Standalone greetings cannot inspect the repository, even with stale coding context or provider tool calls.
    for greeting in ('hi', 'HI!!!', 'hello', 'hey...', 'good morning', 'thanks', 'thank you!',
                     'can you help me code?', 'Could you help with programming?', 'What can you do?'):
        agent = runtime_agent('greeting-' + str(len(requests)))
        agent.append_message({'role': 'user', 'content': 'OLD_TASK_SHOULD_NOT_CONTINUE'})
        agent.append_message({'role': 'system', 'content': 'OLD_CONTEXT_SHOULD_NOT_BE_SENT'})
        count = len(requests)
        agent.turn(greeting, connection)
        assert len(requests) == count + 1 and agent.task['status'] == 'completed'
        sent = requests[-1][2]
        assert sent.get('tools') == [] and sent['max_output_tokens'] == 512
        assert sent['input'][1:] == [{'role': 'user', 'content': greeting}]
        assert agent.task['engine'] == 'openrouter-agent-sdk'
        assert not any(m.get('role') == 'tool' for m in agent.messages)
        for tool, args in (('list_files', {'path': '.'}), ('read_file', {'path': 'lib.py'}),
                           ('write_file', {'path': 'greeting-wrote.txt', 'content': 'bad'}),
                           ('run_shell', {'command': 'touch greeting-shell.txt'})):
            try:
                agent.execute_tool(tool, args)
                raise AssertionError('A conversational turn executed a tool')
            except n.AgentRuntimeError:
                pass
        assert not (native_root / 'greeting-wrote.txt').exists() and not (native_root / 'greeting-shell.txt').exists()
    # A new coding request after a greeting regains tools.
    GatewayStub.native_steps = [native_call('read_file', {'path': 'lib.py'}, 620),
                               {'role': 'assistant', 'content': 'Read the requested file.'}]
    agent.turn('Read lib.py.', connection)
    assert agent.task['status'] == 'completed' and requests[-1][2].get('tools')
    assert any(m.get('role') == 'tool' for m in agent.messages)
    try:
        GatewayStub.scope_text = 'invalid scope result'
        agent = runtime_agent('invalid-scope')
        count = len(requests)
        agent.turn('Read lib.py.', connection)
        assert len(requests) == count + 1 and agent.task['conversational']
        assert not any(m.get('role') == 'tool' for m in agent.messages)
    finally:
        GatewayStub.scope_text = None
    try:
        GatewayStub.incomplete = True
        agent = runtime_agent('sdk-incomplete')
        try:
            agent.turn('Read lib.py.', connection)
            raise AssertionError('An incomplete SDK response was accepted')
        except n.AgentRuntimeError:
            pass
        assert agent.task['status'] == 'failed' and not any(m.get('role') == 'tool' for m in agent.messages)
    finally:
        GatewayStub.incomplete = False
    agent = runtime_agent('context-roundtrip')
    (native_root / 'large.py').write_text('def helper(): return 1\n' * 500)
    GatewayStub.usage = {'input_tokens': 100, 'output_tokens': 10, 'total_tokens': 110,
                         'input_tokens_details': {'cached_tokens': 40}, 'cost': 0.001}
    GatewayStub.native_steps = [native_call('read_file', {'path': 'large.py', 'line_count': 500}, 710),
        native_call('read_file', {'path': 'large.py', 'line_count': 500}, 711),
        {'role': 'assistant', 'content': 'Inspected large.py.'}]
    count = len(requests)
    try:
        agent.turn('Read large.py twice to check this fixture.', connection)
    finally:
        GatewayStub.usage = None
    assert len(requests) == count + 4, 'Context optimization made additional inference requests'
    assert agent.task['context']['requests'] == 4 and agent.task['context']['compacted_results'] == 1, agent.task['context']
    assert agent.task['context']['saved_upper_bound_total'] > 8000
    assert agent.task['usage'] == dict(input_tokens=400, output_tokens=40, cached_tokens=160, reported_requests=4,
                                      cost_usd=0.004, cost_reported_requests=4), agent.task
    assert GatewayStub.session_headers[-4:] == [agent.session_id] * 4
    assert 'duplicate observation' in json.dumps(requests[-1][2]['input'])
    assert '[500 total lines]' in agent.messages[-2]['content'], 'Inference compaction destroyed the saved full read'
    assert json.loads(agent.path.read_text())['task']['usage'] == agent.task['usage']
    # Missing provider metrics remain unknown, rather than estimates being presented as real tokens.
    agent.turn('hi', connection)
    assert agent.task['usage'] == {} and 'context' in agent.task
    # A Stop-hook continuation reuses this turn's scope decision, saving one classification call.
    agent = runtime_agent('context-continuation')
    original_hook = agent.hook
    def continuation_hook(event, payload=None, **kwargs):
        result = original_hook(event, payload, **kwargs)
        if event == 'stop' and not payload.get('stop_hook_active'):
            result = dict(result, decision='block', reason='Explain the completed verification.')
        return result
    agent.hook = continuation_hook
    GatewayStub.native_steps = [{'role': 'assistant', 'content': 'Task complete.'},
                               {'role': 'assistant', 'content': 'Verification complete.'}]
    count = len(requests)
    agent.turn('Inspect lib.py.', connection)
    assert len(requests) == count + 3 and agent.task['model_steps'] == 3
    # Oversized project instructions stop before the coding request; no silent instruction truncation.
    rules = native_root / 'AGENTS.md'
    rules.write_text('REQUIRED RULE ' * 6000)
    try:
        agent = runtime_agent('context-too-large')
        count = len(requests)
        try:
            agent.turn('Read lib.py.', connection)
            raise AssertionError('Oversized required instructions were silently truncated')
        except n.AgentRuntimeError as error:
            assert 'context budget' in str(error)
        assert len(requests) == count + 1 and agent.task['model_steps'] == 1
    finally:
        rules.unlink()
    print('PASS: official OpenRouter Agent SDK scope gate, streaming, guarded tools, invalid scope and incomplete responses')
    cancellation_times = {}
    for mode in ('cancel', 'deadline'):
        agent = runtime_agent('headers-' + mode)
        agent.request_timeout = .3 if mode == 'deadline' else 120
        GatewayStub.header_gate, GatewayStub.headers_started = threading.Event(), threading.Event()
        gate = GatewayStub.header_gate
        worker, errors = run_thread(lambda: agent.turn('Check the project.', connection))
        try:
            assert GatewayStub.headers_started.wait(5), 'The HTTP request never started'
            start = time.monotonic()
            if mode == 'cancel':
                agent.cancel()
            worker.join(1.5)
            cancellation_times[mode] = round(time.monotonic() - start, 3)
            assert not worker.is_alive(), 'A request remained blocked before response headers'
            assert errors and isinstance(errors[0], KeyboardInterrupt if mode == 'cancel' else n.AgentRuntimeError), errors
            saved = json.loads(agent.path.read_text())
            assert saved['task']['status'] == ('interrupted' if mode == 'cancel' else 'failed'), saved['task']
        finally:
            gate.set()
            GatewayStub.header_gate = None
            agent.cancel()
            worker.join(5)
    # A hook handler exception must stop a native edit, while vendor hook output stays compatible.
    actual_hook = n.HOOK_SCRIPT
    crash_hook = temp / 'crash-hook.py'
    crash_hook.write_text('import runpy, sys\nns = runpy.run_path(' + repr(str(actual_hook)) + ')\n'
        'def crash(*args): raise RuntimeError("injected check failure")\n'
        'ns["HANDLERS"]["pre-tool"] = crash\nns["main"](sys.argv)\n')
    try:
        n.HOOK_SCRIPT = crash_hook
        agent = runtime_agent('failed-check')
        GatewayStub.native_steps = [native_call('write_file', {'path': 'must-stay-missing.txt', 'content': 'unsafe'}, 601)]
        try:
            agent.turn('Create a file.', connection)
            raise AssertionError('A failed mandatory hook was accepted')
        except n.AgentRuntimeError:
            pass
        assert not (native_root / 'must-stay-missing.txt').exists()
        assert json.loads(agent.path.read_text())['task']['status'] == 'failed'
        assert agent.messages[-1]['role'] == 'tool' and 'Interrupted' in agent.messages[-1]['content']
        try:
            agent.hook('unknown-event')
            raise AssertionError('An unknown mandatory hook event was accepted')
        except n.AgentRuntimeError:
            pass
        for provider in ('claude', 'codex'):
            output = n.subprocess.run([sys.executable, str(crash_hook), 'pre-tool', provider],
                input=json.dumps({'cwd': str(native_root)}), text=True, capture_output=True, check=True)
            assert json.loads(output.stdout) == {}, 'The vendor hook failure protocol changed'
        invalid_hook = temp / 'invalid-hook.py'
        for text in ('print("{}")', 'print("[]")', 'print("not json")', 'raise SystemExit(2)'):
            invalid_hook.write_text(text)
            n.HOOK_SCRIPT = invalid_hook
            try:
                agent.execute_tool('write_file', {'path': 'invalid-check.txt', 'content': 'unsafe'})
                raise AssertionError('An invalid mandatory hook was accepted')
            except n.AgentRuntimeError:
                pass
            assert not (native_root / 'invalid-check.txt').exists()
        slow_hook = temp / 'slow-hook.py'
        marker = temp / 'hook-started'
        slow_hook.write_text('import time\nfrom pathlib import Path\nPath(' + repr(str(marker)) + ').touch()\ntime.sleep(30)\nprint("{}")\n')
        n.HOOK_SCRIPT = slow_hook
        agent = runtime_agent('hook-cancel')
        worker, errors = run_thread(lambda: agent.turn('Wait for the check.', connection))
        try:
            assert wait_for(marker.exists), 'The slow hook never started'
            writers = [threading.Thread(target=lambda k=i: [agent.queue_prompt(f'follow-up-{k}-{j}') for j in range(5)]) for i in range(4)]
            for writer in writers:
                writer.start()
            for writer in writers:
                writer.join()
            start = time.monotonic()
            agent.cancel()
            worker.join(1.5)
            cancellation_times['hook'] = round(time.monotonic() - start, 3)
            assert not worker.is_alive() and errors and isinstance(errors[0], KeyboardInterrupt), errors
            assert not agent.processes, 'A cancelled hook process was not reaped'
            saved = json.loads(agent.path.read_text())
            assert len(set(saved['pending_prompts'])) == 20 and saved['task']['status'] == 'interrupted'
        finally:
            agent.cancel()
            worker.join(5)
        agent = runtime_agent('hook-deadline')
        agent.hook_timeout = .15
        try:
            agent.turn('Wait for the check.', connection)
            raise AssertionError('A hook exceeded its runtime deadline')
        except n.AgentRuntimeError:
            pass
        assert agent.task['status'] == 'failed' and not agent.processes
    finally:
        n.HOOK_SCRIPT = actual_hook
    # Invalid persisted guard settings cannot silently disable native policy checks.
    settings_path = n.tools_path()
    settings_bytes = settings_path.read_bytes() if settings_path.exists() else None
    try:
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        settings_path.write_text('{broken')
        try:
            agent.hook('pre-tool', {'tool_name': 'Write', 'tool_input': {'file_path': str(native_root / 'lib.py')}})
            raise AssertionError('Corrupt policy settings were treated as an empty policy')
        except n.AgentRuntimeError:
            pass
    finally:
        if settings_bytes is None:
            settings_path.unlink()
        else:
            settings_path.write_bytes(settings_bytes)
    # Queue consumption and user-message persistence happen in the same saved state transition.
    agent = runtime_agent('hook-cancel')
    prompt = agent.pending_prompts[0]
    GatewayStub.native_steps = [{'role': 'assistant', 'content': 'Queue recovered.'}]
    agent.turn(prompt, connection, queued=True)
    saved = json.loads(agent.path.read_text())
    assert saved['task']['status'] == 'completed' and len(saved['pending_prompts']) == 19
    assert sum(m.get('role') == 'user' and m.get('content') == prompt for m in saved['messages']) == 1
    agent.set_phase('executing', status='running', tool_call_id='crash-call')
    agent.append_message(native_call('write_file', {'path': 'crash-replayed.txt', 'content': 'unsafe'}, 602))
    resumed = runtime_agent('hook-cancel')
    assert resumed.task['status'] == 'interrupted' and resumed.pending_prompts == agent.pending_prompts
    assert resumed.messages[-1]['role'] == 'tool' and not (native_root / 'crash-replayed.txt').exists()
    assert json.loads(resumed.path.read_text())['task']['status'] == 'interrupted'
    # Concurrent turns are rejected before changing conversation/queue state.
    agent.turn_lock.acquire()
    before = agent.path.read_bytes()
    try:
        try:
            agent.turn('A concurrent turn.', connection)
            raise AssertionError('A concurrent turn was accepted')
        except n.AgentRuntimeError:
            pass
        assert agent.path.read_bytes() == before
    finally:
        agent.turn_lock.release()
    # A failed after-edit check remains durable and must recover before any next model request.
    post_crash = temp / 'post-crash.py'
    post_crash.write_text('import runpy, sys\nns = runpy.run_path(' + repr(str(actual_hook)) + ')\n'
        'def crash(*args): raise RuntimeError("injected post failure")\n'
        'ns["HANDLERS"]["post-tool"] = crash\nns["main"](sys.argv)\n')
    agent = runtime_agent('pending-after-check')
    GatewayStub.native_steps = [native_call('write_file', {'path': 'checked-after-resume.py', 'content': 'x = 1\n'}, 603)]
    try:
        n.HOOK_SCRIPT = post_crash
        try:
            agent.turn('Create a module.', connection)
            raise AssertionError('A post-edit check failure did not stop the turn')
        except n.AgentRuntimeError:
            pass
        assert (native_root / 'checked-after-resume.py').exists()
        assert json.loads(agent.path.read_text())['pending_check']['tool_name'] == 'Write'
    finally:
        n.HOOK_SCRIPT = actual_hook
    agent = runtime_agent('pending-after-check')
    GatewayStub.native_steps = [{'role': 'assistant', 'content': 'Recovered the pending check.'}]
    agent.turn('Inspect current state.', connection)
    assert json.loads(agent.path.read_text())['pending_check'] is None and agent.task['status'] == 'completed'
    # Invalid tool calls remain recoverable; runtime failures and exhausted deadlines stop the turn.
    agent = runtime_agent('invalid-tool-recovery')
    bad_call = native_call('read_file', {'path': 'lib.py'}, 604)
    bad_call['tool_calls'][0]['function']['arguments'] = '{invalid'
    GatewayStub.native_steps = [bad_call, {'role': 'assistant', 'content': 'Recovered.'}]
    agent.turn('Inspect the file.', connection)
    assert agent.task['status'] == 'completed' and agent.task['tool_failures'] == 1
    agent = runtime_agent('provider-failure')
    try:
        GatewayStub.status = 503
        try:
            agent.turn('Inspect the file.', connection)
            raise AssertionError('A provider failure was accepted')
        except RuntimeError as error:
            assert '503' in str(error)
        assert agent.task['status'] == 'failed'
    finally:
        GatewayStub.status = 200
    agent = runtime_agent('turn-deadline')
    agent.turn_timeout = 0
    try:
        agent.turn('Inspect the file.', connection)
        raise AssertionError('The turn deadline was not enforced')
    except n.AgentRuntimeError:
        pass
    assert agent.task['status'] == 'failed'
    # An approval modal must respect the turn deadline too.
    terminal = n.AgentTerminal('approval-deadline')
    terminal.agent.deadline = time.monotonic() - 1
    try:
        terminal.request_approval('Wait for approval.')
        raise AssertionError('An approval waited past the turn deadline')
    except n.AgentRuntimeError:
        pass
    # Prompts rejected by policy must not leak into subsequent model context.
    rejecting_hook = temp / 'rejecting-hook.py'
    rejecting_hook.write_text('import json, sys\n'
        'data = json.load(sys.stdin)\n'
        'print(json.dumps(dict(nocturne_ok=True, **({"decision":"block","reason":"Denied"} '
        'if sys.argv[1] == "prompt" else {}))))\n')
    agent = runtime_agent('blocked-prompt')
    try:
        n.HOOK_SCRIPT = rejecting_hook
        agent.turn('DO_NOT_SEND_THIS_BLOCKED_PROMPT', connection)
        assert agent.task['status'] == 'blocked'
    finally:
        n.HOOK_SCRIPT = actual_hook
    GatewayStub.native_steps = [{'role': 'assistant', 'content': 'Allowed turn.'}]
    agent.turn('Allowed request.', connection)
    assert all(m.get('content') != 'DO_NOT_SEND_THIS_BLOCKED_PROMPT' for m in requests[-1][2]['input'])
    assert not any(any(k.startswith('nocturne_') for k in m) for m in requests[-1][2]['input'])
    print('PASS: native runtime state, queued prompt recovery, mandatory hook failures, provider failures, deadlines; cancellation seconds:', cancellation_times)
    # The native CLI discovers its models and switches them without launching either vendor CLI.
    n.private_json(n.STORE.with_name('gateway.json'), connection)
    # SIGTERM during a piped request must persist interruption and reap transport resources.
    stopped_record = {'kind': 'nocturne', 'name': None, 'id': None}
    stopped_command = n.agent_command(stopped_record)
    GatewayStub.header_gate, GatewayStub.headers_started = threading.Event(), threading.Event()
    gate = GatewayStub.header_gate
    stopped = n.subprocess.Popen(n.shlex.split(stopped_command), stdin=n.subprocess.PIPE,
        stdout=n.subprocess.DEVNULL, stderr=n.subprocess.DEVNULL, cwd=native_root, text=True)
    try:
        stopped.stdin.write('Wait for the provider.\n')
        stopped.stdin.flush()
        assert GatewayStub.headers_started.wait(5)
        competing = n.subprocess.run(n.shlex.split(stopped_command), input='/exit\n', cwd=native_root,
            capture_output=True, text=True, timeout=5)
        assert competing.returncode == 1 and 'already open' in competing.stdout, competing.stdout
        stopped.terminate()
        stopped.wait(timeout=3)
        state = json.loads((n.cli_dir() / 'agent-sessions' / f'{stopped_record["id"]}.json').read_text())
        assert state['task']['status'] == 'interrupted'
    finally:
        gate.set()
        GatewayStub.header_gate = None
        if stopped.poll() is None:
            stopped.kill()
            stopped.wait()
        stopped.stdin.close()
    record_native = {'kind': 'nocturne', 'name': None, 'id': None}
    command_native = n.agent_command(record_native)
    assert '--agent-run' in command_native and record_native['id']
    assert n.agent_launch('nocturne', command_native) == command_native
    assert n.clean_sessions([record_native]) == [record_native]
    cli = n.subprocess.run(n.shlex.split(command_native), input='/exit\n', cwd=native_root,
                           capture_output=True, text=True, timeout=30)
    assert cli.returncode == 0 and 'Model: claude-test' in cli.stdout and 'Ready.' in cli.stdout, cli.stdout + cli.stderr
    assert 'AVAILABLE MODELS' not in cli.stdout and 'Choose model' not in cli.stdout, 'Startup blocked on a model picker'
    cli = n.subprocess.run(n.shlex.split(command_native), input='/model\n2\n/exit\n', cwd=native_root,
                           capture_output=True, text=True, timeout=30)
    assert cli.returncode == 0 and 'NOCTURNE AGENT' in cli.stdout and 'AVAILABLE MODELS' in cli.stdout, cli.stdout + cli.stderr
    assert 'Model: claude-test' in cli.stdout and 'Model: coding-test' in cli.stdout
    fresh_command = n.agent_command({'kind': 'nocturne', 'name': None, 'id': None})
    fresh = n.subprocess.run(n.shlex.split(fresh_command), input='/exit\n', cwd=native_root,
                            capture_output=True, text=True, timeout=30)
    assert fresh.returncode == 0 and 'Model: coding-test' in fresh.stdout and 'Choose model' not in fresh.stdout
    # A real PTY exercises the interactive CLI (the pipe mode above is only for scripts).
    import pty, fcntl, termios, struct, select
    master, slave = pty.openpty()
    original_tty = termios.tcgetattr(slave)
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', 30, 110, 0, 0))
    interactive_record = {'kind': 'nocturne', 'name': None, 'id': None}
    interactive_command = n.agent_command(interactive_record)
    interactive = n.subprocess.Popen(n.shlex.split(interactive_command), stdin=slave, stdout=slave, stderr=slave,
                                     cwd=native_root, env=dict(os.environ, TERM='xterm-256color'), start_new_session=True)
    screen = bytearray()
    def tty_wait(predicate, seconds=12):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if predicate():
                return
            if interactive.poll() is not None:
                raise AssertionError('Interactive CLI exited: ' + screen.decode('utf-8', 'replace')[-2000:])
            if select.select([master], [], [], .05)[0]:
                screen.extend(os.read(master, 65536))
        raise AssertionError('Interactive CLI timed out: ' + screen.decode('utf-8', 'replace')[-2000:])
    def tty_send(data):
        os.write(master, data)
    try:
        tty_wait(lambda: b'enter send' in screen)
        assert b'\x1b[?1049h' in screen and b'Choose model number' not in screen
        tty_send(b'/')
        tty_wait(lambda: b'Commands' in screen)
        tty_send(b'model\r')
        tty_wait(lambda: b'Select model' in screen and b'claude-test' in screen)
        tty_send(b'\x1b[A\r')
        preferences = n.STORE.with_name('agent-preferences.json')
        tty_wait(lambda: json.loads(preferences.read_text())['model'] == 'claude-test')
        assert interactive.poll() is None
        tty_send(b'\x1b[200~explain\nthis project\x1b[201~\r')
        transcript_path = n.cli_dir() / 'agent-sessions' / f"{interactive_record['id']}.json"
        def saved_answer():
            messages = json.loads(transcript_path.read_text())['messages']
            return messages and messages[-1].get('role') == 'assistant' and messages[-1].get('content') == 'ok'
        tty_wait(saved_answer)
        transcript_native = json.loads(transcript_path.read_text())
        assert [m['content'] for m in transcript_native['messages'] if m['role'] == 'user'] == ['explain\nthis project']
        GatewayStub.native_steps = [native_call('run_shell', {'command': 'touch declined-by-ui.txt'}, 501),
                                   {'role': 'assistant', 'content': 'The command was declined.'}]
        at = len(screen)
        tty_send(b'run the check\r')
        tty_wait(lambda: b'Approval required' in screen[at:])
        tty_send(b'n')
        tty_wait(lambda: any(m.get('content') == 'The command was declined.' for m in json.loads(transcript_path.read_text())['messages']))
        assert not (native_root / 'declined-by-ui.txt').exists()
        command = "python3 -c \"import time;from pathlib import Path;Path('running-ui.txt').write_text('yes');time.sleep(30);Path('after-stop-ui.txt').write_text('bad')\""
        GatewayStub.native_steps = [native_call('run_shell', {'command': command}, 502)]
        at = len(screen)
        tty_send(b'run the slow check\r')
        tty_wait(lambda: b'Approval required' in screen[at:])
        tty_send(b'y')
        tty_wait(lambda: (native_root / 'running-ui.txt').exists())
        tty_send(b'\x1b')
        tty_wait(lambda: b'Interrupted' in screen[at:])
        assert not (native_root / 'after-stop-ui.txt').exists(), 'Esc failed to stop the running shell process'
        GatewayStub.stream_gate = threading.Event()
        GatewayStub.native_steps = [{'role': 'assistant', 'content': 'Streaming test in progress'}]
        at = len(screen)
        tty_send(b'check streaming\r')
        tty_wait(lambda: b'Streaming test in progress' in screen[at:])
        at = len(screen)
        tty_send(b'\x1b')
        tty_wait(lambda: b'Interrupted' in screen[at:], seconds=5)
        GatewayStub.stream_gate.set()
        GatewayStub.stream_gate = None
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', 24, 80, 0, 0))
        tty_send(b'/status\r')
        tty_wait(lambda: b'Session:' in screen[at:])
        tty_send(b'/exit\r')
        interactive.wait(timeout=5)
        assert interactive.returncode == 0
        assert termios.tcgetattr(slave) == original_tty, 'The CLI did not restore terminal input settings'
    finally:
        if GatewayStub.stream_gate is not None:
            GatewayStub.stream_gate.set()
            GatewayStub.stream_gate = None
        if interactive.poll() is None:
            interactive.kill()
            interactive.wait()
        os.close(master)
        os.close(slave)
    n.private_json(n.STORE.with_name('gateway.json'), gateway)
finally:
    stub.shutdown()
    stub.server_close()
    thread.join()
skills = sorted(p.name for p in n.APP_SKILLS.iterdir())
links = temp / 'codex/skills'
assert sorted(p.name for p in links.iterdir()) == skills and all((links / name).is_symlink() for name in skills), list(links.iterdir())
# Another app's copy of a Nocturne skill (Atelier's atelier-impact-check) is switched off by name in Nocturne's Codex sessions.
(links / 'other-impact-check').mkdir()
(links / 'other-impact-check' / 'SKILL.md').write_text('---\nname: other-impact-check\n---\n')
assert n.shadowing_skills() == ['other-impact-check'] and 'skills.config=[{name="other-impact-check",enabled=false}]' in n.agent_launch('codex', 'codex')
n.shutil.rmtree(links / 'other-impact-check')
(links / 'mine').mkdir()
n.save_agent_tools(dict(n.agent_tools(), skills={'nocturne-debugging': False}))
n.agent_launch('claude', 'claude')
assert not (links / 'nocturne-debugging').exists() and (links / 'mine').is_dir(), 'A disabled skill keeps its link, or a user skill was touched'
assert not (n.cli_dir() / 'skills/.claude/skills/nocturne-debugging').exists()
native_disabled = n.NocturneAgent(native_root, 'skills-disabled')
assert 'nocturne-debugging' not in {s['name'] for s in n.hooklib().list_skills(native_disabled.skill_root)}
# The hook script: guards ask (Claude) or refuse when Nocturne cannot ask (Codex), rules block, edits get an impact list.
work = temp / 'hooks'
work.mkdir()
(work / 'lib.py').write_text('def price(value):\n    return value * 2  # price\n')
(work / 'use.py').write_text('from lib import price\nprint(price(3), "price")\n')

def hook(event, provider, payload, **env):
    result = n.subprocess.run([sys.executable, str(n.HOOK_SCRIPT), event, provider], input=json.dumps(dict(payload, cwd=str(work))),
                              capture_output=True, text=True, timeout=30, env={**os.environ, **n.agent_env('check'), 'NOCTURNE_PROJECT': str(work), **env})
    return json.loads(result.stdout or '{}')
out = hook('pre-tool', 'claude', {'tool_name': 'Bash', 'tool_input': {'command': 'git add -A && git commit -m x'}})['hookSpecificOutput']
assert out['permissionDecision'] == 'ask' and 'commit' in out['permissionDecisionReason'], out
out = hook('pre-tool', 'codex', {'tool_name': 'shell', 'tool_input': {'command': ['bash', '-lc', 'npm install left-pad']}})['hookSpecificOutput']
assert out['permissionDecision'] == 'deny' and 'npm install' in out['permissionDecisionReason'], out
assert hook('pre-tool', 'claude', {'tool_name': 'Bash', 'tool_input': {'command': 'git status'}}) == {}
# Only commands that change dependencies need approval; project scripts (tests, typecheck, builds) run without asking.
for command in ('npm run typecheck', 'npm test -- --runTestsByPath src/a.test.ts', 'cd backend && pnpm build', 'yarn lint', 'npx tsc --noEmit'):
    assert hook('pre-tool', 'codex', {'tool_name': 'shell', 'tool_input': {'command': ['bash', '-lc', command]}}) == {}, command
hooks = n.hooklib()
assert [hooks.package_intent(c) for c in ('pnpm add zod', 'npm --prefix api ci', 'yarn', 'git status && npm i', 'pnpm dlx x')] == \
    ['pnpm add', 'npm ci', 'yarn install', 'npm i', 'pnpm dlx']
# A refusal is remembered for two minutes only, so one "no" can't block a command for the rest of the session.
real_cli = os.environ.get('NOCTURNE_CLI_DIR')
os.environ['NOCTURNE_CLI_DIR'] = str(n.cli_dir())  # Never the running Nocturne's approvals.
approvals = n.cli_dir() / 'approvals'
approvals.mkdir(parents=True, exist_ok=True)
(n.cli_dir() / 'alive').touch()
(approvals / 'refused.json').write_text(json.dumps({'s9:npm install x': time.time(), 's8:npm install x': time.time() - 600}))
assert hooks.ask_nocturne(str(work), 'codex', 's9', 'Packages', 'r', 'npm install x') is False
hooks.APPROVAL_WAIT = 0.5
assert hooks.ask_nocturne(str(work), 'codex', 's8', 'Packages', 'r', 'npm install x') is None, 'An old refusal must ask again'
(approvals / 'refused.json').write_text(json.dumps(['s9:npm install x']))  # The old, permanent list is ignored.
assert hooks.ask_nocturne(str(work), 'codex', 's9', 'Packages', 'r', 'npm install x') is None
for leftover in approvals.iterdir():
    leftover.unlink()
hooks.APPROVAL_WAIT = 120
os.environ.pop('NOCTURNE_CLI_DIR') if real_cli is None else os.environ.__setitem__('NOCTURNE_CLI_DIR', real_cli)
# The impact check runs by itself before an edit: every place that uses the code it changes.
out = hook('pre-tool', 'claude', {'tool_name': 'Edit', 'tool_input': {'file_path': str(work / 'lib.py'), 'old_string': 'return value * 2', 'new_string': 'return value'}})
context = out['hookSpecificOutput']['additionalContext']
assert 'Changing: price (function' in context and 'use.py:1 import' in context and 'use.py:2 call' in context and 'lib.py:2' not in context, context
edit = {'tool_name': 'Edit', 'tool_input': {'file_path': str(work / 'lib.py'), 'old_string': 'return value * 2', 'new_string': 'return value'}}
assert hook('pre-tool', 'claude', edit, NOCTURNE_NODE='/bin/false')['hookSpecificOutput']['additionalContext'] == context, 'Without Node the built-in parsers must give the same answer'
# Tree-sitter (through Node) also reads Go, Rust and Java, and only matches uses in the same language.
if n.shutil.which('node'):
    (work / 'cart.go').write_text('package shop\nfunc (c *Cart) Checkout() int { return price(1) } // price\nfunc price(v int) int { return v * 2 }\n')
    (work / 'report.go').write_text('package shop\nfunc Report() int { return price(4) + len("price") }\n')
    out = hook('pre-tool', 'claude', {'tool_name': 'Edit', 'tool_input': {'file_path': str(work / 'cart.go'), 'old_string': 'return v * 2', 'new_string': 'return v'}})
    context = out['hookSpecificOutput']['additionalContext']
    assert 'cart.go:2 call in Checkout' in context and 'report.go:2 call in Report' in context and 'use.py' not in context and context.count('report.go') == 1, context
    for name in ('cart.go', 'report.go'):
        (work / name).unlink()
# Codex edits through apply_patch: the removed lines before the edit, the added lines after it.
patch = '*** Begin Patch\n*** Update File: lib.py\n@@\n-    return value * 2  # price\n+    return value * 3  # price\n*** End Patch\n'
context = hook('pre-tool', 'codex', {'tool_name': 'apply_patch', 'tool_input': {'input': patch}})['hookSpecificOutput']['additionalContext']
assert 'Changing: price (function' in context and 'use.py:2 call' in context, context
original = (work / 'lib.py').read_text()
(work / 'lib.py').write_text(original.replace('* 2', '* 3'))
context = hook('post-tool', 'codex', {'tool_name': 'apply_patch', 'tool_input': {'input': patch}})['hookSpecificOutput']['additionalContext']
assert 'after editing lib.py' in context and 'use.py:2 call' in context, context
(work / 'lib.py').write_text(original)
# Edits a shell command makes (sed -i, cat > file) get the check after the command, for both CLIs, with and without git.

def hook_at(cwd, event, provider, payload):
    result = n.subprocess.run([sys.executable, str(n.HOOK_SCRIPT), event, provider], input=json.dumps(dict(payload, cwd=str(cwd))),
                              capture_output=True, text=True, timeout=30, env={**os.environ, **n.agent_env('check'), 'NOCTURNE_PROJECT': str(cwd)})
    return json.loads(result.stdout or '{}')
snaps = n.cli_dir() / 'snapshots'
bash = {'tool_name': 'Bash', 'tool_use_id': 'toolu_check1', 'tool_input': {'command': "sed -i 's/value \\* 2/value * 4/' lib.py"}}
assert hook('pre-tool', 'claude', bash) == {} and (snaps / 'toolu_check1.json').is_file()
(work / 'lib.py').write_text(original.replace('* 2', '* 4'))
context = hook('post-tool', 'claude', bash)['hookSpecificOutput']['additionalContext']
assert 'The shell command changed this file' in context and 'price (function' in context and 'use.py:2 call' in context, context
assert not (snaps / 'toolu_check1.json').exists(), 'The snapshot must go once the command is done'
(work / 'lib.py').write_text(original)
assert hook('pre-tool', 'claude', {'tool_name': 'Bash', 'tool_use_id': 'toolu_ro', 'tool_input': {'command': 'git status && rg price | head 2>&1'}}) == {}
assert not (snaps / 'toolu_ro.json').exists(), 'Read-only commands need no snapshot'
assert hook('post-tool', 'claude', {'tool_name': 'Bash', 'tool_use_id': 'toolu_ro', 'tool_input': {'command': 'git status'}}) == {}
grepo = temp / 'shell-git'
grepo.mkdir()
(grepo / 'lib.py').write_text('def price(value):\n    return value * 2\n\n\ndef tax(value):\n    return value / 10\n')
(grepo / 'use.py').write_text('from lib import price, tax\nprint(price(3), tax(4))\n')
for args in (['init', '-q'], ['add', '.'], ['-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-qm', 'init']):
    n.subprocess.run(['git', '-C', str(grepo), *args], check=True, capture_output=True)
shell = {'tool_name': 'shell', 'session_id': 'codex-s', 'tool_input': {'command': ['bash', '-lc', "sed -i 's/value \\/ 10/value \\/ 20/' lib.py"]}}
assert hook_at(grepo, 'pre-tool', 'codex', shell) == {}
(grepo / 'lib.py').write_text((grepo / 'lib.py').read_text().replace('/ 10', '/ 20'))
context = hook_at(grepo, 'post-tool', 'codex', shell)['hookSpecificOutput']['additionalContext']
assert 'Changed: tax (function' in context and 'price (' not in context and 'use.py:2 call' in context, context
# Codex skips hooks it has not trusted; Nocturne only reads that state, to warn.
assert n.codex_hooks_trusted() is False
codex_config = n.codex_home() / 'config.toml'
codex_config.write_text(''.join(f'[hooks.state."/<session-flags>/config.toml:{e}:0:0"]\ntrusted_hash = "sha256:x"\n'
                                for e in ('session_start', 'user_prompt_submit', 'pre_tool_use', 'post_tool_use', 'stop')))
assert n.codex_hooks_trusted() is True
codex_config.write_text('[hooks.state."/<session-flags>/config.toml:stop:0:0"]\ntrusted_hash = "sha256:x"\n')
assert n.codex_hooks_trusted() is False
codex_config.unlink()
for skill in n.APP_SKILLS.iterdir():
    head = (skill / 'SKILL.md').read_text().split('\n---\n', 1)[0]
    assert 'disable-model-invocation: true' in head, f'{skill.name} may be loaded by Claude itself'
    assert 'allow_implicit_invocation: false' in (skill / 'agents/openai.yaml').read_text(), f'{skill.name} may be picked by Codex itself'
n.save_agent_tools(dict(n.agent_tools(), rules=[{'id': 'r', 'name': 'Protect env', 'enabled': True, 'event': 'pre-tool', 'matcher': 'Edit|Write',
                                                 'path': '**/*.env', 'action': 'block', 'argument': 'Never edit .env files.'}]))
out = hook('pre-tool', 'claude', {'tool_name': 'Write', 'tool_input': {'file_path': str(work / 'app.env'), 'content': ''}})['hookSpecificOutput']
assert out['permissionDecision'] == 'deny' and out['permissionDecisionReason'] == 'Never edit .env files.', out
for source in ('startup', 'resume', 'clear'):
    assert hook('session-start', 'codex', {'session_id': 's1', 'source': source}) == {}, f'A {source} session must start with nothing loaded'
# Route overlapping global impact skills on prompts, without loading skills at startup.
(temp / 'impact-off.json').write_text(json.dumps({'hooks': {'impact-check': False}}))
for provider in ('claude', 'codex'):
    context = hook('prompt', provider, {'prompt': 'Fix this defect'})['hookSpecificOutput']['additionalContext']
    assert 'Nocturne' in context and 'Do not implicitly invoke atelier-impact-check' in context, context
    assert hook('prompt', provider, {'prompt': 'Fix this defect'},
                NOCTURNE_TOOLS=str(temp / 'impact-off.json')) == {}, 'Disabled impact checks must add no routing context'
# Project memory is fresh on every prompt, scoped to the imported project, and absent at startup.
memory = work / '.nocturne/memory'
memory.mkdir(parents=True)
entry = memory / 'local-db.md'
entry.write_text('---\ntype: correction\n---\nLocal PostgreSQL runs natively, never in Docker.\n')
for provider in ('claude', 'codex'):
    context = hook('prompt', provider, {'prompt': 'Investigate the database'},
                   NOCTURNE_PROJECT=str(work))['hookSpecificOutput']['additionalContext']
    assert 'Local PostgreSQL runs natively' in context and 'before investigating' in context, context
    assert context.startswith('Project memory') and 'do not search the web' in context, context
    answer = hook('prompt', provider, {'prompt': 'Why is PostgreSQL slow here?'}, NOCTURNE_PROJECT=str(work))
    asked = answer['hookSpecificOutput']['additionalContext']
    assert 'local-db.md  (relevant to this request)' in asked, asked
    # The user sees that memory was read: both CLIs show a hook's systemMessage in the conversation.
    assert answer['systemMessage'].startswith('Nocturne checked the memory index: 1 entry, 1 matched (local-db.md); loaded'), answer
    quiet = hook('prompt', provider, {'prompt': 'Rename the button'}, NOCTURNE_PROJECT=str(work))
    assert quiet['systemMessage'].endswith('1 entry, none matched this prompt, nothing loaded'), quiet
    assert 'runs natively' not in quiet['hookSpecificOutput']['additionalContext'], 'An unmatched entry must not be loaded'
    # Relevance is whole words: "per" inside "impersonation" or a shared "local" must not flag an unrelated entry.
    (memory / 'perf-notes.md').write_text('---\ntype: fact\n---\nSet per-page limits locally.\n')
    asked = hook('prompt', provider, {'prompt': 'how does impersonation work locally?'}, NOCTURNE_PROJECT=str(work))['hookSpecificOutput']['additionalContext']
    assert 'perf-notes' not in asked, 'An entry that only shares a common word must not be loaded or listed'
    (memory / 'perf-notes.md').unlink()
    assert hook('session-start', provider, {'source': 'startup'}) == {}
entry.write_text('---\ntype: correction\n---\nUpdated database fact.\n')
context = hook('prompt', 'codex', {'prompt': 'Check the db'}, NOCTURNE_PROJECT=str(work))['hookSpecificOutput']['additionalContext']
assert 'Updated database fact' in context and 'runs natively' not in context, 'The index must refresh when an entry changes (db = database)'
(temp / 'memory-off.json').write_text(json.dumps({'hooks': {'project-memory': False}}))
off = hook('prompt', 'codex', {'prompt': 'Continue'}, NOCTURNE_TOOLS=str(temp / 'memory-off.json'), NOCTURNE_PROJECT=str(work))
assert 'Updated database fact' not in off['hookSpecificOutput']['additionalContext'] and 'systemMessage' not in off, off
# A large memory: the hook checks its table of contents and index first, and loads only what the prompt is about.
for i in range(40):
    (memory / f'topic-{i}.md').write_text(f'---\nname: topic-{i}\ndescription: Notes on subsystem{i}\ntype: fact\n---\n' + ('filler text ' * 400))
(memory / 'redis-cache.md').write_text('---\nname: redis-cache\ndescription: Redis is not used locally\ntype: environment\nkeywords: cache, valkey\n---\n'
                                       '# Local\nNo Redis locally.\n\n# Staging\nValkey on port 6380.\n')
answer = hook('prompt', 'codex', {'prompt': 'Why is the valkey cache slow?'}, NOCTURNE_PROJECT=str(work))
context = answer['hookSpecificOutput']['additionalContext']
assert 'Valkey on port 6380' in context and 'filler text' not in context and len(context) < 4000, context
assert 'is generated by Nocturne from memory: never edit it' in context, 'Agents must not hand-edit the index (seen in a real run)'
# Words every coding request uses (repo, app, file, project) never make an entry relevant (seen in a real run).
(memory / 'naming.md').write_text('---\nname: naming\ndescription: Tiger means the invoicing API\ntype: naming\n---\nTiger is the invoicing API (repo invoice-api, app_rw user).\n')
generic = hook('prompt', 'codex', {'prompt': 'Add a docstring in app.py and count the files in the repo project'}, NOCTURNE_PROJECT=str(work))
assert 'none matched' in generic['systemMessage'], generic['systemMessage']
(memory / 'naming.md').unlink()
assert '(redis-cache.md)' in answer['systemMessage'] and '42 entries, 1 matched' in answer['systemMessage'], answer['systemMessage']
contents = (work / '.nocturne/index/CONTENTS.md').read_text()
assert '## Table of contents' in contents and '## Index' in contents and 'Staging — line' in contents and '**valkey** — redis-cache' in contents, contents[:2000]
assert 'topic-7.md' not in hook('prompt', 'codex', {'prompt': 'Continue'}, NOCTURNE_PROJECT=str(work))['hookSpecificOutput']['additionalContext'].split('This session')[0]
for path in memory.iterdir():
    path.unlink()
# Turn records of sessions idle for two weeks are dropped when a new prompt arrives.
stale_turn = n.cli_dir() / 'memory-turns' / 'gone.json'
stale_turn.parent.mkdir(parents=True, exist_ok=True)
stale_turn.write_text('{}')
os.utime(stale_turn, (time.time() - 20 * 86400, time.time() - 20 * 86400))
hook('prompt', 'codex', {'session_id': 'fresh', 'prompt': 'hello'}, NOCTURNE_PROJECT=str(work))
assert not stale_turn.exists() and (n.cli_dir() / 'memory-turns' / 'fresh.json').exists()
(work / '.nocturne/reports').mkdir(parents=True)
report = work / '.nocturne/reports/20261016-000000-price.md'
report.write_text('---\nid: 20261016-000000-price\nsession: s1\ntitle: Price\n---\n- price (`lib.py:1`)\n')
os.utime(report, (time.time() - 60, time.time() - 60))
assert '/nocturne-init-feature update 20261016-000000-price; changed: lib.py' in hook('stop', 'claude', {'session_id': 's1'})['reason']
assert hook('stop', 'claude', {'session_id': 's1'}) == {}, 'A report must be asked about once per change'
# Memory upkeep: a prompt that corrects the agent or states a rule asks it to save that, inside the prompt's context, which
# neither CLI prints. Nothing is added at the turn's end: a Stop continuation shows in the conversation and breaks the flow.
(memory / 'cache.md').write_text('---\nname: cache\ndescription: Valkey cache on staging\ntype: environment\nkeywords: valkey, cache, staging\n---\n'
                                 'Valkey runs on port 6380 on staging.\n')
ask = lambda session, text, provider='codex', **env: hook('prompt', provider, {'session_id': session, 'prompt': text}, NOCTURNE_PROJECT=str(work), **env)
stop = lambda session, provider='codex', **extra: hook('stop', provider, dict({'session_id': session}, **extra), NOCTURNE_PROJECT=str(work))
tool = lambda session: hook('post-tool', 'codex', {'session_id': session, 'tool_name': 'shell', 'tool_input': {'command': ['bash', '-lc', 'ls']}})
context_of = lambda out: out.get('hookSpecificOutput', {}).get('additionalContext', '')
assert 'Memory:' not in context_of(ask('m1', 'Why is the valkey cache slow?')), 'An ordinary request gets no memory instruction'
tool('m1')
assert stop('m1') == {}, 'Memory never continues a turn'
out = ask('m1', 'Actually the valkey cache is on port 6390 now. Fix the slow lookups.')
request = next(x for x in context_of(out).split('\n') if x.startswith('The user said:'))
assert '"Actually the valkey cache is on port 6390 now."' in request and '(cache.md)' in request and 'Memory: no change' in request, request
assert len(request) < 900 and 'Memory standard' not in request and 'checked the memory index' in out['systemMessage'], (request, out)
tool('m1')
assert stop('m1') == {} and stop('m1', 'claude') == {}, 'Memory never continues a turn'
assert 'Memory:' not in context_of(ask('m2', 'actually we use valkey 8 now', 'nocturne')), 'Nocturne Agent keeps its own turn loop'
# Two entries on one subject are merged, once per change of either.
(memory / 'cache-notes.md').write_text('---\nname: cache-notes\ndescription: Valkey cache on staging\ntype: environment\nkeywords: valkey, cache, staging\n---\n'
                                       'Valkey runs on port 6380 on staging; restart it after deploys.\n')
assert 'Merge cache-notes.md into cache.md' in context_of(ask('m3', 'Remember to restart the valkey cache after deploys'))
assert 'Merge' not in context_of(ask('m3', 'Remember to restart the valkey cache after deploys, always')), 'An overlap is raised once until one of the entries changes'
# Flow reports still come at the turn's end, alone.
os.utime(work / 'lib.py', (time.time() + 5, time.time() + 5))
ask('s1', 'Change the price rounding; we always round half up')
tool('s1')
due = stop('s1', 'claude')
assert '/nocturne-init-feature update 20261016-000000-price' in due['reason'] and 'Memory:' not in due['reason'], due
assert due['systemMessage'] == 'Nocturne: keeping flow reports current', due
(temp / 'memory-upkeep-off.json').write_text(json.dumps({'hooks': {'memory-upkeep': False}}))
assert 'Memory:' not in context_of(ask('m4', 'actually it is valkey 8', NOCTURNE_TOOLS=str(temp / 'memory-upkeep-off.json')))
# The user's own correction and rule sentences are quoted back, so none is overlooked.
request = context_of(ask('m5', 'Fix the cache warmup. Actually we use valkey 8, not redis. From now on run it with make warm.\nThanks!'))
assert '"Actually we use valkey 8, not redis." / "From now on run it with make warm."' in request and 'Thanks' not in request, request
# Every entry is checked against the standard when it is written: a good entry passes, a bad one gets each problem named.
hooks = n.hooklib()
good = memory / 'local-postgres.md'
good.write_text('---\nname: local-postgres\ndescription: Local PostgreSQL runs natively on port 5544, never in Docker\ntype: environment\n'
                'keywords: postgres, database, db, port, docker\n---\n\nLocally, PostgreSQL runs natively on port `5544`, not in Docker.\n'
                'Its password is in `backend/.env` (`DB_PASSWORD`).\nCheck: `systemctl status postgresql`\n')
assert hooks.lint_memory_entry(good) == [], hooks.lint_memory_entry(good)
bad = memory / 'Notes.md'
bad.write_text('---\nname: notes\ndescription: DB stuff\ntype: misc\n---\nToday I found the db is probably on 5544, password: hunter2xyz\nUpdate: TODO\n')
problems = ' | '.join(hooks.lint_memory_entry(bad))
for expected in ('lowercase words', '`name` must equal', '`description`', '`type` must be', '`keywords`', 'secret', 'relative time ("Today")',
                 'hedges ("probably")', 'task progress', 'appends an "Update" line'):
    assert expected in problems, (expected, problems)
assert hooks.lint_memory_entry(memory / 'cache.md') == [] and 'no frontmatter' in hooks.lint_memory_entry(work / 'lib.py')[0]
bad.unlink()
good.unlink()
# A bad entry written during a task is reported right after the tool that wrote it (Claude Edit/Write), once per version.
ask('m6', 'Remember that local postgres is on 5544', 'claude')
entry = memory / 'pg.md'
entry.write_text('---\nname: pg\ndescription: Postgres\ntype: environment\nkeywords: postgres\n---\nPostgres is probably on 5544.\n')
write = {'session_id': 'm6', 'tool_name': 'Write', 'tool_input': {'file_path': str(entry), 'content': entry.read_text()}}
note = hook('post-tool', 'claude', write)['hookSpecificOutput']['additionalContext']
assert '.nocturne/memory/pg.md breaks the memory standard' in note and 'hedges ("probably")' in note and 'Fix this entry now' in note, note
assert 'breaks the memory standard' not in json.dumps(hook('post-tool', 'claude', write)), 'Each version of an entry is reported once'
assert stop('m6', 'claude') == {}
# Entries written by a shell command (Codex) are checked too, and a fixed entry is not reported again.
ask('m7', 'Note that postgres is on 5544')
entry.write_text(entry.read_text().replace('probably ', ''))
note = tool('m7')['hookSpecificOutput']['additionalContext']
assert 'pg.md breaks the memory standard' in note and 'hedges' not in note and '`keywords`' in note, note
entry.write_text('---\nname: pg\ndescription: Local PostgreSQL listens on port 5544\ntype: environment\nkeywords: postgres, database, port\n---\n'
                 'PostgreSQL listens on port `5544` locally.\n')
assert 'memory standard' not in json.dumps(tool('m7')), 'A fixed entry raises nothing'
for path in memory.iterdir():
    path.unlink()
out = hook('session-start', 'claude', {'session_id': 's1', 'source': 'compact'})['hookSpecificOutput']['additionalContext']
assert 'just compacted' in out and '20261016-000000-price.md' in out and 'nocturne-debugging' not in out and 'Project memory' not in out, out
assert hook('session-start', 'claude', {'session_id': 'other', 'source': 'compact'}) == {}, "Only the session's own reports come back"
n.save_agent_tools({})
day = temp / 'codex/sessions/2026/10/16'
day.mkdir(parents=True)

def rollout(session_id, cwd, started):
    stamp = time.strftime('%Y-%m-%dT%H:%M:%S.000Z', time.gmtime(started))
    (day / f'rollout-{session_id}.jsonl').write_text(json.dumps({'type': 'session_meta', 'payload': {'id': session_id, 'cwd': cwd, 'timestamp': stamp}}) + '\n')

app = n.App()
app.register(None)
app.activate()
w = app.get_windows()[0]
errors = []

def text_of(t):
    return t.get_text_range_format(n.Vte.Format.TEXT, 0, 0, 200, 200)[0] if hasattr(t, 'get_text_range_format') else t.get_text(None, None)[0]

def iter_labels(widget):
    if isinstance(widget, n.Gtk.Label):
        yield widget
    if isinstance(widget, n.Gtk.Container):
        for child in widget.get_children():
            yield from iter_labels(child)


def verify():
    try:
        # The window opens on the plain Terminal view; agent sessions wait until the Agent view is opened.
        assert w.mode == 'terminal' and w.views.get_visible_child_name() == 'terminal', 'Terminal view is not the default'
        assert [t.kind for t in w.terminal_view.terminals()] == ['shell'] and not w.scratch.started, 'Terminal view should start one shell and nothing else'
        assert not w.sidebar_toggle.get_visible() and not w.explorer_button.get_visible()
        assert not w.screen_buttons['gateway'].get_visible()
        w.terminal_view.show_screen('gateway')
        assert w.terminal_view.screen is None and w.terminal_view.new_session('claude') is None
        w.terminal_view.new_session('shell')
        assert len(w.terminal_view.terminals()) == 2 and w.terminal_view.session_records() == []
        w.set_mode('agents')
        assert w.views.get_visible_child_name() == 'agents' and w.explorer_button.get_visible()
        assert w.current_workspace() is w.scratch and w.stack.get_visible_child_name() == 'scratch', 'Home not shown without projects'
        assert [t.kind for t in w.scratch.terminals()] == list(n.SCRATCH_SESSIONS), 'Home shell not started'
        w.scratch_claude = w.scratch.new_session('claude')
        assert len(w.scratch.terminals()) == 2, f'Home sessions: {[t.kind for t in w.scratch.terminals()]}'
        a = w.open_project(str(alpha))
        assert a.terminals() == [] and a.view_name() == 'empty', 'Project should open without sessions'
        a.new_session('claude')
        b = w.open_project(str(beta))
        b.new_session('codex')
        assert w.current_workspace() is b and len(w.project_list.get_children()) == 3
        assert w.open_project(str(alpha)) is a and len(w.project_list.get_children()) == 3, 'Duplicate import'
        w.shell_a = a.new_session('shell')
        w.shell_b = b.new_session('shell')
        assert len(a.terminals()) == 2 and len(b.terminals()) == 2 and len(w.scratch.terminals()) == 2, 'Sessions leaked between projects'
        assert [p['path'] for p in w.load_state()['projects']] == [str(alpha), str(beta)]
    except Exception as e:
        errors.append(repr(e) + "\n" + __import__("traceback").format_exc(limit=-2))
    n.GLib.timeout_add(1500, run_commands)
    return False

def run_commands():
    try:
        w.shell_a.feed_child(b'printf "\\nCWD=%s\\n" "$PWD"\n')
        w.shell_b.feed_child(b'printf "\\nCWD=%s\\n" "$PWD"\n')
        w.scratch.terminals()[0].feed_child(b'printf "\\nCWD=%s\\n" "$PWD"\n')
    except Exception as e:
        errors.append(repr(e) + "\n" + __import__("traceback").format_exc(limit=-2))
    n.GLib.timeout_add(1000, finish)
    return False

def finish():
    try:
        assert f'CWD={alpha}' in text_of(w.shell_a), 'alpha shell not in alpha'
        assert f'CWD={beta}' in text_of(w.shell_b), 'beta shell not in beta'
        assert f'CWD={Path.home()}' in text_of(w.scratch.terminals()[0]), 'Home shell not in ~'
        w.project_list.select_row(w.scratch.row)
        assert w.load_state()['active'] is None and len(w.load_state()['projects']) == 2, 'Home leaked into saved projects'
        # Two Codex tabs in one project each adopt the rollout that began after they started.
        a = w.workspaces[str(alpha)]
        first, second = a.new_session('codex'), a.new_session('codex')
        first.spawned_at, second.spawned_at = time.time() - 30, time.time() - 10
        rollout('codex-first', str(alpha), time.time() - 25)
        rollout('codex-second', str(alpha), time.time() - 5)
        rollout('codex-elsewhere', str(beta), time.time() - 5)
        w.match_codex()
        assert (first.session['id'], second.session['id']) == ('codex-first', 'codex-second'), (first.session, second.session)
        claude = a.terminals()[0]
        claude.session['name'] = 'feature a'
        w.persist()
        saved = {p['path']: p['sessions'] for p in json.loads(n.STORE.read_text())['projects']}[str(alpha)]
        assert [(r['kind'], r['name']) for r in saved] == [('claude', 'feature a'), ('shell', None), ('codex', None), ('codex', None)], saved
        assert saved[0]['id'] == claude.session['id'] and saved[2]['id'] == 'codex-first'
        # A fresh window restores the tabs lazily, with names and ids.
        restored = n.Workspace(w, dict(a.project, sessions=saved))
        assert restored.session_records() == saved and restored.terminals() == []
        blocked_record = {'kind': 'codex', 'name': 'preserve me', 'id': 'existing-conversation', 'gateway': 'missing-route'}
        blocked = n.Workspace(w, dict(a.project, sessions=[blocked_record]))
        blocked.start()
        assert blocked.terminals() == [] and blocked.session_records() == [blocked_record]
        assert blocked.pending_gateway_sessions == [blocked_record], 'A failed gateway restore lost the saved conversation'
        blocked.destroy()
        # Git tabs run once and are not saved.
        git_tab = a.new_session('shell', record={'kind': 'shell', 'name': 'git · diff', 'id': None}, command='true', transient=True)
        assert git_tab.transient and git_tab not in [t for t in a.terminals() if t.session in a.session_records()], 'Git tab was saved'
        assert n.git_state(str(alpha))['repo'] is False
        # The Changes dock (Atelier's git panel) is a screen.
        a.show_screen('changes')
        assert a.view_name() == 'changes' and isinstance(a.screens['changes'], n.ChangesScreen)
        a.show_screen(None)
        a.remove_session(git_tab)
        # Git screens replace the terminals until closed, and the header buttons follow the workspace.
        w.project_list.select_row(a.row)
        a.show_screen('gitkraken')
        assert a.view_name() == 'gitkraken' and w.screen_buttons['gitkraken'].get_active()
        a.show_screen(None)
        assert a.view_name() == 'tabs' and not w.screen_buttons['gitkraken'].get_active()
        a.show_screen('gateway')
        assert isinstance(a.screens['gateway'], n.GatewayScreen) and w.screen_buttons['gateway'].get_active()
        gateway_screen = a.screens['gateway']
        assert len(gateway_screen.rows) == 2 and not gateway_screen.token.get_visibility()
        assert all(not r['fields']['key'].get_visibility() for r in gateway_screen.rows)
        assert gateway_screen.save() == gateway
        captured = []
        original_new_session = a.new_session
        a.new_session = lambda kind, **kwargs: captured.append((kind, kwargs))
        # The new session is opened from +, while gateway setup has no vendor-client picker.
        rows = a.menu.get_child().get_children()
        assert [r.get_child().get_children()[1].get_text() for r in rows[1:]] == ['Nocturne Agent', 'Claude', 'Codex', 'Shell']
        rows[1].clicked()
        a.new_session = original_new_session
        assert captured[0][0] == 'nocturne' and 'client' not in gateway_screen.rows[0]
        a.show_screen(None)
        # Skills & Hooks is a screen too; what it switches is what the hooks read.
        a.show_screen('agent-tools')
        assert a.view_name() == 'agent-tools' and w.screen_buttons['agent-tools'].get_active()
        screen = a.screens['agent-tools']
        # Only Nocturne's own skills, each example once, written for the chosen CLI.
        names = [r.skill['name'] for r in screen.listing.get_children()]
        assert names == [s['name'] for s in n.hooklib().list_skills(n.APP_SKILLS)] and all(x.startswith('nocturne-') for x in names), names
        screen.listing.select_row(screen.listing.get_children()[names.index('nocturne-init-feature')])
        page = screen.detail.get_child().get_child()
        commands = [w.get_text() for w in iter_labels(page) if w.get_text().startswith(('/nocturne-init-feature ', '$nocturne-init-feature '))]
        assert commands == ['/nocturne-init-feature ' + x for x in n.SKILL_SAMPLES['nocturne-init-feature']], commands
        screen.config['hooks']['packages'] = False
        screen.save()
        assert n.agent_tools()['hooks'] == {'packages': False}
        a.show_screen(None)
        # A Codex hook waiting for approval is answered from a Nocturne dialog.
        request = n.cli_dir() / 'approvals' / 'check.json'
        request.write_text(json.dumps({'id': 'check', 'cli': 'codex', 'project': str(alpha), 'command': 'npm install', 'title': 'Packages', 'reason': 'r'}))
        w.confirm = lambda text: 'npm install' in text and 'alpha' in text
        w.poll_agent_tools()
        del w.confirm
        assert request.with_suffix('.answer').read_text() == 'yes'
        # A turn that ends in a tab the user is not looking at marks it until it is opened.
        a.select_page(claude.page)
        (n.cli_dir() / 'turns').mkdir(exist_ok=True)
        (n.cli_dir() / 'turns' / first.term_id).write_text('{}')
        w.poll_agent_tools()
        a.refresh_strip()
        w.sync_group_buttons(a)
        assert first.turn_done and w.group_buttons['agents'].count.get_style_context().has_class('attention'), 'Finished turn not marked'
        assert first.page.tab_dot_label.get_style_context().has_class('attention'), "The finished tab's dot should turn pink"
        a.select_page(first.page)
        assert not first.turn_done
        # Explorer and editor: open, lint (built-in Python check), format JSON, save, reload from disk, unsaved tracking.
        (alpha / 'bad.py').write_text('def f(:\n')
        (alpha / 'data.json').write_text('{"b":1}')
        a.toggle_explorer(True)
        assert a.explorer.find(str(alpha / 'bad.py')) and a.explorer.get_visible() and w.explorer_button is w.group_buttons['files']
        py = a.open_file(str(alpha / 'bad.py'))
        py.lint()
        assert py.problems and py.problems[0]['severity'] == 'error', py.problems
        doc = a.open_file(str(alpha / 'data.json'))
        assert a.open_file(str(alpha / 'data.json')) is doc and doc.term is None and doc not in a.terminals()
        doc.replace_text('{"b":1,  "c":[1]}')
        assert a.unsaved() == [doc]
        doc.write()
        assert not a.unsaved() and (alpha / 'data.json').read_text().startswith('{')
        (alpha / 'data.json').write_text('{"from": "disk"}\n')
        doc.check_disk()
        assert doc.text() == '{"from": "disk"}\n', doc.text()
        # Agent view: header icons pick Explorer (open files), Agents or Terminals, and the tab row shows only that group.
        a.refresh_strip()
        assert list(w.group_buttons) == ['files', 'agents', 'terminals', 'notes'] and a.sub_row.get_parent() is a.tabs_area
        handles = a.strip.get_children()
        assert a.strip_group == 'files' and handles == [py.handle, doc.handle] and w.group_buttons['files'].get_active(), handles
        assert doc.handle.get_child().get_style_context().has_class('active')
        # Title notifications must not rebuild the tab widgets (that would break double-click and inline rename).
        doc.tab_title.set_text('renamed.json')
        a.refresh_strip()
        assert a.strip.get_children() == handles
        doc.tab_title.set_text('data.json')
        for name in ('terminals', 'agents'):
            w.group_buttons[name].set_active(True)
            shown = [p for p in a.pages() if p.handle in a.strip.get_children()]
            assert a.strip_group == name and shown and all(a.page_group(p) == name for p in shown), (name, shown)
            assert [g for g, b in w.group_buttons.items() if b.get_active()] == [name]
        # The Agents group has its own side panel: open tabs, past sessions of this folder, and a search box.
        assert a.side.get_visible() and a.side.get_visible_child() is a.sessions
        panel = a.sessions
        claude_dir = temp / 'claude' / 'projects' / re.sub(r'[^A-Za-z0-9]', '-', str(alpha))
        claude_dir.mkdir(parents=True, exist_ok=True)
        (claude_dir / 'old-one.jsonl').write_text(json.dumps({'type': 'user', 'message': {'content': '<command>x</command>'}}) + '\n'
                                                  + json.dumps({'type': 'user', 'message': {'content': [{'type': 'text', 'text': 'Fix the  login\nbug'}]}}) + '\n')
        (claude_dir / 'named.jsonl').write_text(json.dumps({'type': 'user', 'message': {'content': 'hello'}}) + '\n'
                                                + json.dumps({'type': 'ai-title', 'aiTitle': 'Auto'}) + '\n' + json.dumps({'type': 'custom-title', 'customTitle': 'Payments'}) + '\n')
        rollouts = temp / 'codex' / 'sessions' / '2026' / '01' / '02'
        rollouts.mkdir(parents=True, exist_ok=True)
        (rollouts / 'rollout-a.jsonl').write_text(json.dumps({'type': 'session_meta', 'payload': {'id': 'cx1', 'cwd': str(alpha)}}) + '\n' + json.dumps(
            {'type': 'response_item', 'payload': {'type': 'message', 'role': 'user', 'content': [{'type': 'input_text', 'text': '# AGENTS.md rules'}]}}, separators=(',', ':')) + '\n' + json.dumps(
            {'type': 'response_item', 'payload': {'type': 'message', 'role': 'user', 'content': [{'type': 'input_text', 'text': 'Write docs'}]}}, separators=(',', ':')) + '\n')
        (rollouts / 'rollout-b.jsonl').write_text(json.dumps({'type': 'session_meta', 'payload': {'id': 'cx2', 'cwd': '/elsewhere'}}) + '\n')
        titles = {h['id']: h['title'] for h in n.agent_history(str(alpha))}
        assert titles.get('old-one') == 'Fix the login bug' and titles.get('named') == 'Payments' and titles.get('cx1') == 'Write docs' and 'cx2' not in titles, titles
        panel.history = n.agent_history(str(alpha))
        panel.render()
        texts = [l.get_text() for l in iter_labels(panel.list)]
        assert 'Payments' in texts and 'Write docs' in texts and any(t.startswith('OPEN') for t in texts), texts
        panel.search.set_text('payme')
        texts = [l.get_text() for l in iter_labels(panel.list)]
        assert 'Payments' in texts and 'Write docs' not in texts, texts
        panel.search.set_text('')
        assert n.ago(time.time() - 7200) == '2h' and n.ago(time.time()) == 'now'
        agent_tabs = [p for p in a.pages() if a.page_group(p) == 'agents']
        assert len(agent_tabs) > 1 and a.strip.get_children() == [a.tabs.get_nth_page(a.tabs.get_current_page()).handle], 'Only the active agent tab shows beside the list'
        a.select_page(agent_tabs[-1])
        a.refresh_strip()
        assert a.strip.get_children() == [agent_tabs[-1].handle], 'Picking a session replaces the shown tab'
        # Open sessions are listed latest active first: the tab just shown, then one whose turn just ended goes above it.
        names = [t.tab_title.get_text() for t in agent_tabs]
        for i, t in enumerate(agent_tabs):
            t.tab_title.set_text(f'agent {i}')

        def open_order():
            panel.render()
            order = []
            for child in panel.list.get_children():
                if isinstance(child, n.Gtk.Label) and child.get_text().startswith('HISTORY'):
                    break
                order += [l.get_text() for l in iter_labels(child) if l.get_style_context().has_class('session-title')]
            return order
        assert open_order()[0] == f'agent {len(agent_tabs) - 1}', open_order()
        w.turn_ended(agent_tabs[0].term.term_id)
        assert open_order()[:2] == ['agent 0', f'agent {len(agent_tabs) - 1}'], open_order()
        for t, name in zip(agent_tabs, names):
            t.tab_title.set_text(name)
        w.group_buttons['agents'].set_active(False)
        assert w.group_buttons['agents'].get_active() and not a.side.get_visible(), 'Clicking Agents again hides the sessions list'
        assert a.strip.get_children() == [p.handle for p in agent_tabs], 'Without the list every agent tab shows'
        w.group_buttons['agents'].set_active(False)
        assert a.side.get_visible() and a.side.get_visible_child() is a.sessions and len(a.strip.get_children()) == 1
        w.group_buttons['terminals'].set_active(True)
        assert not a.side.get_visible(), 'Terminals have no side panel'
        w.group_buttons['agents'].set_active(True)
        # Appearance follows the desktop: Light tones every palette color and restyles what is open; fonts come from the system.
        term = a.terminals()[0]
        assert term.get_font().to_string() == n.MONO_FONT.to_string(), 'Terminals use the system monospace font'
        os.environ['NOCTURNE_APPEARANCE'] = 'light'
        app.apply_appearance()
        assert n.LIGHT and n.tone('#292b36') == '#f7f6fa' and n.tone('#ef819533') == '#c93c5633' and n.tone('#3fb6e8') == '#3fb6e8'
        css = n.themed_css().decode()
        assert '#292b36' not in css and '#dedee8' not in css and 'rgba(255,255,255' not in css.replace(' ', ''), 'Light CSS keeps dark colors'
        assert all(e.buffer.get_style_scheme().get_id() == 'nocturne-light' for e in a.editors()) and a.editors()
        assert n.icon_pixbuf('home', 16).get_width() == 16
        os.environ['NOCTURNE_APPEARANCE'] = 'dark'
        app.apply_appearance()
        assert not n.LIGHT and all(e.buffer.get_style_scheme().get_id() == 'nocturne' for e in a.editors())
        saved_fonts = n.UI_FONT, n.MONO_FONT
        n.UI_FONT, n.MONO_FONT = n.Pango.FontDescription.from_string('Cantarell 22'), n.Pango.FontDescription.from_string('Fira Code 14')
        css = n.themed_css().decode()
        assert 'font-size: 20px' in css and 'font-family: "Fira Code", monospace' in css and 'font-size: 14pt' in css, 'CSS should follow the system fonts'
        n.UI_FONT, n.MONO_FONT = saved_fonts
        w.group_buttons['files'].set_active(True)
        assert a.strip_group == 'files' and a.explorer.get_visible()
        w.group_buttons['files'].set_active(False)  # Clicking Explorer again hides the tree but keeps the files shown.
        assert not a.explorer_open and a.strip_group == 'files' and w.group_buttons['files'].get_active()
        a.toggle_explorer(True)
        a.select_page(doc)
        assert a.editor() is doc
        a.show_group('terminals')
        assert a.editor() is None and a.active_group == 'terminals'
        a.show_group('files')
        assert a.editor() is doc, 'Explorer group should return to the last file used'
        a.select_page(py)
        a.show_group('agents')
        assert a.terminal().kind in ('claude', 'codex') and a.active_group == 'agents'
        a.show_group('files')
        assert a.editor() is py and a.content.get_visible_child_name() == 'pages'
        # Notes: a group after Terminals with a list like the sessions panel; text saves itself, outside the project.
        assert n.split_note('# Plan\n\nship it') == ('Plan', 'ship it') and n.split_note('just text') == ('', 'just text')
        assert n.join_note(' Plan ', 'ship it') == '# Plan\n\nship it' and n.note_name('', '\n  first line\nsecond') == 'first line'
        w.group_buttons['notes'].set_active(True)
        assert a.strip_group == 'notes' and a.side.get_visible() and a.side.get_visible_child() is a.notes_panel
        assert a.content.get_visible_child_name() == 'none' and not a.actions.get_visible(), 'Notes open from their list, not the + menu'
        note = a.new_note('line one\nline two')
        assert a.note() is note and a.page_group(note) == 'notes' and note.term is None and note.title.get_text() == 'line one'
        note.title_entry.set_text('Snippets')
        note.save()
        saved_note = Path(note.path)
        assert saved_note.read_text() == '# Snippets\n\nline one\nline two' and saved_note.parent == n.notes_dir(a.project)
        assert not saved_note.is_relative_to(alpha), 'Notes never go inside the project'
        a.refresh_strip()
        assert a.strip.get_children() == [note.handle] and note.title.get_text() == 'Snippets' and a.view_state()['notes'] == [note.path]
        texts = [l.get_text() for l in iter_labels(a.notes_panel.list)]
        assert 'Snippets' in texts and 'OPEN  ·  1' in texts, texts
        saved_note.write_text('# Snippets\n\nedited elsewhere')
        note.check_disk()
        assert note.body() == 'edited elsewhere' and not note.dirty, 'A clean note follows its file'
        a.close_note(note)
        a.notes_panel.render()
        texts = [l.get_text() for l in iter_labels(a.notes_panel.list)]
        assert 'Snippets' in texts and 'NOTES  ·  1' in texts and 'OPEN  ·  0' in texts, texts
        a.notes_panel.search.set_text('ELSEWHERE')
        assert 'Snippets' in [l.get_text() for l in iter_labels(a.notes_panel.list)], 'Search looks inside notes'
        a.notes_panel.search.set_text('nothing like it')
        assert 'Snippets' not in [l.get_text() for l in iter_labels(a.notes_panel.list)]
        a.notes_panel.search.set_text('')
        reopened = a.open_note(str(saved_note))
        assert reopened.title_entry.get_text() == 'Snippets' and reopened.body() == 'edited elsewhere'
        empty = a.new_note()
        assert empty.path != reopened.path
        a.close_note(empty)
        assert not os.path.exists(empty.path), 'An empty note leaves no file'
        w.group_buttons['notes'].set_active(False)
        assert w.group_buttons['notes'].get_active() and not a.side.get_visible(), 'Clicking Notes again hides the list'
        w.group_buttons['notes'].set_active(False)
        assert a.side.get_visible_child() is a.notes_panel and a.side.get_visible()
        a.close_note(reopened)
        # Renaming an agent from its ▾ asks for the name; cancelling keeps the old one.
        a.show_group('agents')
        agent = a.terminal()
        ask = w.ask_text
        w.ask_text = lambda *_: '  login flow  '
        rename_mapped = agent.page.handle.get_mapped
        agent.page.handle.get_mapped = lambda: False  # Off screen, rename asks for the name.
        a.rename_session(agent)
        agent.page.handle.get_mapped = rename_mapped
        assert agent.session['name'] == 'login flow' and agent.page.tab_title.get_text() == 'login flow'
        w.ask_text = lambda *_: None
        a.rename_session(agent)
        assert agent.session['name'] == 'login flow', 'Cancel should keep the old name'
        w.ask_text = ask
        assert {r['name'] for r in json.loads(n.STORE.read_text())['projects'][0]['sessions']} >= {'login flow'}
        b = w.workspaces[str(beta)]
        b.show_group('files')
        assert b.content.get_visible_child_name() == 'none' and b.active_group == 'files', 'An empty group should show its empty page'
        b.show_group('terminals')
        assert b.content.get_visible_child_name() == 'pages' and b.terminal().kind == 'shell'
        a.close_editor(py)
        assert [e.path for e in a.editors()] == [str(alpha / 'data.json')] and a.view_state()['files'] == [str(alpha / 'data.json')]
        a.close_editor(doc)
        a.toggle_explorer(False)
        # Per-file change counts against HEAD, including untracked files and names with spaces.
        repo = temp / 'repo'
        repo.mkdir()
        git = lambda *args: n.subprocess.run(['git', '-C', str(repo), '-c', 'user.name=t', '-c', 'user.email=t@t', *args], check=True, capture_output=True)
        git('init', '-q')
        (repo / 'a b.txt').write_text('1\n2\n3\n')
        git('add', '.')
        git('commit', '-qm', 'init')
        (repo / 'a b.txt').write_text('1\nx\n')
        (repo / 'new.txt').write_text('n\nm\n')
        changes = {c['path']: (c['status'], c['added'], c['deleted']) for c in n.git_changes(str(repo))}
        assert changes == {'a b.txt': ('M', 1, 2), 'new.txt': ('?', 2, 0)}, changes
        assert '+x' in n.git_file_diff(str(repo), {'path': 'a b.txt', 'old': None, 'status': 'M'})
        # A branch that was never pushed counts its commits that aren't on any remote, so the dock offers Publish.
        origin = temp / 'origin.git'
        n.subprocess.run(['git', 'init', '-q', '--bare', str(origin)], check=True, capture_output=True)
        git('checkout', '-q', '.')
        (repo / 'new.txt').unlink()
        git('remote', 'add', 'origin', str(origin))
        git('push', '-q', 'origin', 'HEAD')
        git('fetch', '-q', 'origin')
        git('checkout', '-qb', 'unpushed')
        (repo / 'a b.txt').write_text('1\n2\n3\n4\n')
        git('commit', '-qam', 'work')
        status = n.repo_status(str(repo))
        assert status['unpublished'] and status['ahead'] == 1, status
        # After a push the flow links the forge's own "new pull request" page; a local remote has none to offer.
        printed = "remote: Create a pull request for 'unpushed' on GitHub by visiting:\nremote:      https://github.com/o/r/pull/new/unpushed\n"
        assert n.pull_link(str(repo), 'unpushed', printed) == 'https://github.com/o/r/pull/new/unpushed'
        assert n.pull_link(str(repo), 'unpushed', '') is None
        assert n.LOG_PATH is None  # Tests never write to the user's app log.
        # Terminal view tab names and branch pills.
        assert n.shell_title('akrizu@host:~/Documents/site', '/') == 'site' and n.shell_title('akrizu@host:~', '/') == '~'
        assert n.shell_title('vim notes.md', '/') == 'vim notes.md' and n.shell_title('', str(Path.home())) == '~'
        assert n.git_head(str(repo / 'a b.txt')) == 'unpushed' and n.git_head('/') is None
        b = w.workspaces[str(beta)]
        b.remove_session(w.shell_b)
        assert len(b.terminals()) == 1
        window = w.get_window()
        pixbuf = n.Gdk.pixbuf_get_from_window(window, 0, 0, w.get_allocated_width(), w.get_allocated_height())
        if pixbuf:
            pixbuf.savev(str(Path(__file__).with_name('preview.png')), 'png', [], [])
    except Exception as e:
        errors.append(repr(e) + "\n" + __import__("traceback").format_exc(limit=-2))
    w.closing = True
    w.destroy()
    n.Gtk.main_quit()
    return False

n.GLib.timeout_add(500, verify)
n.Gtk.main()
if errors:
    raise SystemExit('; '.join(errors))
print('PASS: Session start loads nothing; impact check before edits (tree-sitter, built-in fallback), reports after a compact and at turn end, Skills screen (only Nocturne skills, examples once), launch flags, Codex skill links, hook guards and rules, impact check, report upkeep, Codex approvals, turn-end marks), Group tabs with ▾ session lists, renaming agents renames their Claude / Codex sessions, Group tabs (Agents, Terminals, Notes, Explorer), Terminal view by default with Agent view switch, Explorer and editor (lint, format, save, reload), Git screens, per-file change counts and diffs, Home workspace without a project, project import, projects open empty, named sessions saved and resumed (Claude ids, Codex rollout matching), per-project terminal isolation, real PTY output, persistence')
