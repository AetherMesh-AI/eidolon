"""Output protocol unit tests use synthetic bytes, never claim compiler execution."""
import base64
import copy
import hashlib

import pytest

from eidolon_cli.organization_project_build_outputs import expected_outputs, validate_build_outputs
from eidolon_cli.organization_project_validation import digest


def files(*names):
    return [{'path': 'root0/' + name} for name in names]


def record(values):
    outputs=[]
    for path, text in sorted(values.items()):
        data=text.encode('utf-8')
        outputs.append({'path':path,'sources':['/source/root0/'+path[:-3]+'.ts'],
                        'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest(),
                        'base64':base64.b64encode(data).decode('ascii')})
    manifest=[{key:item[key] for key in ('path','sources','bytes','sha256')} for item in outputs]
    return {'outputs':outputs,'outputManifestSha256':digest(manifest)}


def test_unicode_output_preserves_exact_bytes():
    value=record({'nested/雪.js':'export const snow = "❄️";\n'})
    result=validate_build_outputs(value,files('nested/雪.ts','tsconfig.json'),'root0')
    assert result[0]['content']=='export const snow = "❄️";\n'
    assert result[0]['bytes']==len(result[0]['content'].encode())


@pytest.mark.parametrize('names',[
 (),('x.d.ts',),('../x.ts',),('node_modules/x.ts',),('a//x.ts',),('a\\x.ts',),
 ('a.ts','a.ts'),('a.ts','a.js/b.ts'),tuple(f'{i}.ts' for i in range(65)),
])
def test_invalid_source_mapping(names):
    with pytest.raises(ValueError):expected_outputs(files(*names),'root0')


@pytest.mark.parametrize('change',[
 'missing','extra','duplicate','order','path','source','bytes_bool','bytes_large',
 'hash','base64','noncanonical','fields','manifest','bom','invalid_utf8','aggregate','frame',
])
def test_invalid_output_rejected(change):
    value=record({'a.js':'one','b.js':'two'})
    item=value['outputs'][0]
    if change=='missing':value['outputs'].pop()
    elif change=='extra':value['outputs'].append({**item,'path':'extra.js'})
    elif change=='duplicate':value['outputs'].append(copy.deepcopy(item))
    elif change=='order':value['outputs'].reverse()
    elif change=='path':item['path']='../a.js'
    elif change=='source':item['sources']=['/source/root0/b.ts']
    elif change=='bytes_bool':item['bytes']=True
    elif change=='bytes_large':item['bytes']=262145
    elif change=='hash':item['sha256']='0'*64
    elif change=='base64':item['base64']='!!!!'
    elif change=='noncanonical':
        value=record({'a.js':'a','b.js':'two'});value['outputs'][0]['base64']='YR=='
    elif change=='fields':item['unexpected']=True
    elif change=='manifest':value['outputManifestSha256']='0'*64
    elif change=='bom':value=record({'a.js':'\ufeffone','b.js':'two'})
    elif change=='invalid_utf8':
        item.update(bytes=3,base64='////',sha256=hashlib.sha256(b'\xff'*3).hexdigest())
    elif change=='aggregate':value=record({'a.js':'x'*262144,'b.js':'y'*262144,'c.js':'z'})
    elif change=='frame':value['unexpectedPadding']='x'*1048576
    with pytest.raises(ValueError):validate_build_outputs(value,files('a.ts','b.ts','c.ts') if change=='aggregate' else files('a.ts','b.ts'),'root0')


def test_exact_limits_and_data_only_dependency_declarations():
    value=record({'a.js':'x'*262144,'b.js':'y'*262144})
    result=validate_build_outputs(value,files('a.ts','b.ts','node_modules/pkg/index.d.ts'),'root0')
    assert sum(item['bytes'] for item in result)==524288
