"""Synthetic ordinary Codex credential/HTTP edges; never calls a live provider.

Installed only by the isolated native subscription fixture. The ordinary router,
SDK, Responses adapter, one-shot helper and gateway stay real. Setup credentials
and the dedicated setup transport remain separate.
"""
import json


def install(edges):
    import httpx
    from agent import auxiliary_client as auxiliary
    from tests.eidolon_cli.subscription_native_fixture import SUGGESTIONS

    edges.patch(auxiliary, '_read_codex_access_token', lambda: 'synthetic-ordinary-codex-never-real')
    original = httpx.Client.send

    def send(client, request, **kwargs):
        if request.url.host != 'chatgpt.com':
            return original(client, request, **kwargs)
        assert request.method == 'POST' and request.url.path.endswith('/responses')
        assert request.headers['authorization'] == 'Bearer synthetic-ordinary-codex-never-real'
        value = json.loads(request.content)
        assert value['model'] == 'synthetic-codex-model'
        assert not value.get('tools') and value['store'] is False
        assert value['instructions'].startswith('Improve only the selected field,')
        assert len(value['input']) == 1 and value['input'][0]['role'] == 'user'
        content = value['input'][0]['content']
        text = content if isinstance(content, str) else content[0]['text']
        fields = json.loads(text)
        assert set(fields) == {'field', 'jobs', 'goals', 'namingStyle'}
        edges.record('ordinary-responses', field=fields['field'], model=value['model'], tools=False,
                     historyMessages=0, store=False)
        response = {'id': 'resp_ordinary_synthetic', 'status': 'completed', 'output': [
            {'id': 'msg_ordinary', 'type': 'message', 'role': 'assistant', 'status': 'completed',
             'content': [{'type': 'output_text', 'annotations': [], 'text': json.dumps({
                 'suggestion': SUGGESTIONS[fields['field']], 'assumptions': []})}]}],
            'usage': {'input_tokens': 10, 'output_tokens': 20, 'total_tokens': 30}}
        item = {'type': 'response.output_item.done', 'sequence_number': 1, 'output_index': 0,
                'item': response['output'][0]}
        event = {'type': 'response.completed', 'sequence_number': 2, 'response': response}
        return httpx.Response(200, request=request, headers={'content-type': 'text/event-stream'},
                              content=('data: ' + json.dumps(item) + '\n\ndata: ' + json.dumps(event) + '\n\n').encode())

    edges.patch(httpx.Client, 'send', send)
