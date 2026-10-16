"""Official pinned compiler against synthetic files only, never project imports.

The external compiler package must be explicitly supplied and byte-verified.
These API behavior checks do not establish prepared-workspace native support.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from eidolon_cli.organization_project_compiler_bundle import copy_verified_compiler


PROTOCOL = (Path(__file__).resolve().parents[2] / 'eidolon_cli'
            / 'project_workspace_runtime' / 'protocol.mjs')
PROGRAM = r"""
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
const {createWorkspaceTypecheckProgram}=await import(process.argv[1]);
const compiler=createRequire(import.meta.url)(process.argv[2]);
assert.equal(compiler.version,'6.0.3');
const configuration=process.argv[3], selected=JSON.parse(process.argv[4]);
const parsed=compiler.getParsedCommandLineOfConfigFile(configuration,{},
  {...compiler.sys,onUnRecoverableConfigFileDiagnostic(){}});
try {
  const {program,diagnostics}=createWorkspaceTypecheckProgram(compiler,configuration,selected);
  console.log(JSON.stringify({parsedRoots:parsed?.fileNames??[],hasProgram:!!program,
    roots:program?.getRootFileNames()??[],files:program?.getSourceFiles().map(source=>source.fileName)??[],
    options:program?.getCompilerOptions(),configFileName:program?.getCompilerOptions().configFile?.fileName,
    diagnostics:diagnostics.map(value=>({
      code:value.code,file:value.file?.fileName,message:compiler.flattenDiagnosticMessageText(value.messageText,'\n')}))}));
} catch(error) {
  console.log(JSON.stringify({rejected:error.message}));
}
"""


@pytest.fixture(scope='module')
def compiler(tmp_path_factory):
    directory = os.environ.get('EIDOLON_TEST_TYPESCRIPT_BUNDLE')
    if directory is None:
        pytest.skip('Exact external TypeScript package not explicitly supplied for synthetic API tests')
    node = shutil.which('node')
    if node is None:
        pytest.fail('Node is required when the exact synthetic compiler bundle is explicitly supplied')
    destination = tmp_path_factory.mktemp('compiler-api') / 'verified'
    copy_verified_compiler(directory, destination)
    return node, destination / 'lib' / 'typescript.js'


@pytest.fixture
def project(tmp_path):
    files = {
        'project/tsconfig.base.json': json.dumps({'compilerOptions': {
            'target': 'ES2022', 'module': 'ESNext', 'moduleResolution': 'Bundler',
            'strict': True, 'jsx': 'preserve', 'types': [], 'skipLibCheck': True,
            'paths': {'@shared': ['../shared/value.ts']}}}),
        'project/tsconfig.json': json.dumps({'extends': './tsconfig.base.json',
            'compilerOptions': {'noEmit': False, 'noCheck': True, 'incremental': True},
            'include': ['src', 'ambient.d.ts', 'extra/config-only.ts']}),
        'project/ambient.d.ts': 'declare const ambientAnswer: string;',
        'project/extra/config-only.ts': 'export const configured: string = ambientAnswer;',
        'project/src/collision.ts': 'export const plain: string = "plain";',
        'project/src/collision.tsx': 'export const hidden: string = "hidden";',
        'project/src/main.ts': 'import { shared } from "@shared"; export const value: string = shared + ambientAnswer;',
        'shared/value.ts': 'export const shared: string = "shared";',
    }
    for path, content in files.items():
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
    return tmp_path


def compile_fixture(compiler, project):
    node, entry = compiler
    selected = [str(project / 'project/src/collision.ts'), str(project / 'project/src/collision.tsx')]
    before = {str(path): hashlib.sha256(path.read_bytes()).hexdigest()
              for path in project.rglob('*') if path.is_file()}
    result = subprocess.run([node, '--input-type=module', '-e', PROGRAM, PROTOCOL.as_uri(),
        str(entry), str(project / 'project/tsconfig.json'), json.dumps(selected)],
        capture_output=True, text=True, timeout=30, check=False)
    assert result.returncode == 0, result.stderr
    after = {str(path): hashlib.sha256(path.read_bytes()).hexdigest()
             for path in project.rglob('*') if path.is_file()}
    assert after == before, 'Compiler API must neither emit files nor alter the synthetic inputs'
    return json.loads(result.stdout)


def test_actual_compiler_keeps_same_stem_roots_ambient_aliases_and_original_options(compiler, project):
    result = compile_fixture(compiler, project)
    hidden = str(project / 'project/src/collision.tsx')
    assert hidden not in result['parsedRoots'], 'Fixture must reproduce TypeScript glob shadowing'
    assert result['hasProgram'] and result['diagnostics'] == []
    assert set(result['parsedRoots']) <= set(result['roots'])
    assert hidden in result['roots'] and hidden in result['files']
    assert str(project / 'project/extra/config-only.ts') in result['roots']
    assert str(project / 'project/ambient.d.ts') in result['files']
    assert str(project / 'shared/value.ts') in result['files']
    assert result['options']['configFilePath'] == str(project / 'project/tsconfig.json')
    assert result['configFileName'] == str(project / 'project/tsconfig.json')
    assert result['options']['strict'] is True
    assert result['options']['paths'] == {'@shared': ['../shared/value.ts']}
    assert result['options']['noEmit'] is True
    assert result['options']['noCheck'] is False
    assert result['options']['incremental'] is False


def test_actual_compiler_finds_hidden_sibling_semantic_error_despite_config_no_check(compiler, project):
    hidden = project / 'project/src/collision.tsx'
    hidden.write_text('export const hidden: string = 123;')
    result = compile_fixture(compiler, project)
    assert str(hidden) not in result['parsedRoots'] and str(hidden) in result['files']
    assert any(item['code'] == 2322 and item['file'] == str(hidden) for item in result['diagnostics'])


@pytest.mark.parametrize('configuration', [
    '{',
    '{"compilerOptions":{"strict":"not-boolean"},"include":["src"]}',
    '{"extends":"./missing-base.json","include":["src"]}',
])
def test_actual_compiler_rejects_configuration_errors_before_program_creation(compiler, project, configuration):
    (project / 'project/tsconfig.json').write_text(configuration)
    result = compile_fixture(compiler, project)
    assert result['hasProgram'] is False and result['diagnostics']
    assert result['roots'] == [] and result['files'] == []


def test_actual_compiler_rejects_project_references_explicitly(compiler, project):
    path = project / 'project/tsconfig.json'
    value = json.loads(path.read_text())
    value['references'] = [{'path': '../shared'}]
    path.write_text(json.dumps(value))
    result = compile_fixture(compiler, project)
    assert 'project references are unsupported' in result['rejected']
