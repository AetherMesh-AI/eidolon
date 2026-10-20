"""Fail-closed receipts for the temporary readonly-store native workflow."""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import xml.etree.ElementTree as ET


def require(value, message):
    if not value:
        raise RuntimeError(message)


def load(path):
    def pairs(rows):
        out = {}
        for key, value in rows:
            require(key not in out, 'Duplicate JSON key')
            out[key] = value
        return out
    return json.loads(Path(path).read_text(encoding='utf-8'), object_pairs_hook=pairs)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def git(root, *args):
    return subprocess.check_output(['git', '-C', str(root), *args], text=True, timeout=10).strip()


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, indent=2)
        stream.write('\n')


def source(args):
    root = Path(args.source).resolve(strict=True)
    for value in (args.head, args.tree, args.parent):
        require(re.fullmatch('[0-9a-f]{40}', value) is not None, 'Unrendered source identity')
    require(git(root, 'rev-parse', 'HEAD') == args.head, 'Source head changed')
    require(git(root, 'rev-parse', 'HEAD^{tree}') == args.tree, 'Source tree changed')
    require(git(root, 'show', '-s', '--format=%P', 'HEAD') == args.parent, 'Unexpected ordered source parents')
    require(git(root, 'write-tree') == args.tree, 'Tracked index changed')
    require(git(root, 'diff', '--exit-code') == '', 'Tracked source changed')
    require(git(root, 'diff', '--cached', '--exit-code') == '', 'Tracked index changed')
    require(sha(root/'eidolon_cli/organization_store.py') == args.store_sha256, 'Wrong production store bytes')
    return root


def preflight(args):
    root = source(args)
    require(sys.platform == args.platform, 'Wrong native platform')
    require('.'.join(map(str,sys.version_info[:2])) == args.python, 'Wrong Python minor')
    require(args.platform in {'darwin','win32'}, 'Unsupported proof platform')
    if args.platform == 'darwin':
        require(os.geteuid() != 0, 'Native POSIX permission observations require unprivileged user')
    executable = root/'.venv'/('Scripts/python.exe' if sys.platform=='win32' else 'bin/python')
    require(executable.samefile(sys.executable), 'Not the actual installed candidate interpreter')
    harness = Path(args.harness).resolve(strict=True)
    manifest = harness/'manifest.json'
    require(re.fullmatch('[0-9a-f]{64}',args.harness_sha256) is not None, 'Unrendered harness identity')
    require(sha(manifest)==args.harness_sha256, 'Wrong immutable harness manifest')
    entries=load(manifest)['files']
    seen=set()
    for entry in entries:
        name=entry['path']; rel=PurePosixPath(name)
        require(name and '\\' not in name and not rel.is_absolute() and '..' not in rel.parts and str(rel)==name,'Noncanonical harness path')
        require(name not in seen,'Duplicate harness file');seen.add(name)
        path=harness.joinpath(*rel.parts)
        require(path.is_file() and not path.is_symlink(),'Missing or linked harness file')
        require(sha(path)==entry['sha256'],'Changed harness file: '+name)
    actual={p.relative_to(harness).as_posix() for p in harness.rglob('*') if p.is_file() and p != manifest}
    require(actual==seen,'Unexpected or missing immutable harness files')
    collection=load(args.collection)
    require(sha(root/collection['path'])==collection['sha256'],'Wrong source contract bytes')
    write(args.output,{'platform':sys.platform,'pythonMinor':args.python,'python':sys.version,
        'executable':str(Path(sys.executable).resolve()),'venvExecutable':str(executable),
        'sourceHead':args.head,'sourceTree':args.tree,'sourceParent':args.parent,
        'storeSha256':args.store_sha256,'harnessManifestSha256':args.harness_sha256,
        'harnessFiles':len(seen),'sourceContractSha256':collection['sha256']})


def contract_receipt(collection, platform, xml, collect_log):
    require(platform in {'darwin','win32'},'Unexpected platform')
    expected=collection['orderedNodeIds']
    require(len(expected)==collection['collected']==36 and len(set(expected))==36,'Invalid frozen contract collection')
    prefix=collection['path']+'::'
    collected=[line.strip() for line in collect_log.splitlines() if line.startswith(prefix)]
    require(collected==expected,'Observed source collection differs')
    cases=list(ET.fromstring(xml).iter('testcase'))
    names=[collection['path']+'::'+case.get('name','') for case in cases]
    require(names==expected,'Executed JUnit nodes differ from exact collection')
    classname=collection['path'].removesuffix('.py').replace('/','.')
    require(all(case.get('classname')==classname for case in cases),'Wrong JUnit file identity')
    wanted={row['nodeId']:row['reason'] for row in collection['windows' if platform=='win32' else 'macos']['skipped']}
    observed={}
    for node,case in zip(names,cases):
        require(case.find('error') is None and case.find('failure') is None,'Source contract failed')
        skipped=case.findall('skipped')
        require(len(skipped)<=1,'Duplicated skip result')
        if skipped:
            require(skipped[0].get('type')=='pytest.skip','Unexpected skip type')
            observed[node]=skipped[0].get('message')
    require(observed==wanted,'Unexpected skipped source contract or reason')
    passed=len(cases)-len(observed)
    require(passed==collection['windows' if platform=='win32' else 'macos']['passed'],'Unexpected pass count')
    return {'observedOrderedNodeIds':names,'passed':passed,'skipped':observed,
        'nativeMechanismSuccessInferredFromSkips':False}


def contracts(args):
    result=contract_receipt(load(args.collection),args.platform,Path(args.junit).read_text(),Path(args.collect_log).read_text())
    result['collectionSha256']=sha(args.collection)
    result['junitSha256']=sha(args.junit)
    write(args.output,result)


def compare_runtime(args):
    before=load(args.before);after=load(args.after);pre=load(args.preflight)
    require(before==after,'Interpreter/SQLite/VFS/source identity changed across native journeys')
    require(Path(before['executable']).resolve()==Path(pre['executable']).resolve(),'Mechanism uses another interpreter')
    require(before['platform']==pre['platform'],'Native platform differs')
    require(before['python'].startswith(pre['pythonMinor']+'.'),'Python minor differs')
    require(before['candidate']['sha256']==pre['storeSha256'],'Candidate store differs')
    witness=before['sameInstanceWitness']
    require(witness['pythonOpenObserved'] is True and witness['defaultUnchanged'] is True and witness['restored'] is True,'Loaded SQLite witness incomplete')
    opens=witness['mainOpens']
    require(isinstance(opens,list) and len(opens)==1 and type(opens[0]['result']) is int and opens[0]['result']==0,'Loaded SQLite main-open witness missing or failed')
    for name in ('library','candidate'):
        image=before[name]
        require(isinstance(image,dict) and re.fullmatch('[0-9a-f]{64}',image['sha256']) is not None and type(image['bytes']) is int and image['bytes']>0 and Path(image['path']).is_absolute(),'Loaded image identity malformed')
    extension=before['extension']
    if 'sha256' in extension:
        require(re.fullmatch('[0-9a-f]{64}',extension['sha256']) is not None and type(extension['bytes']) is int and extension['bytes']>0 and Path(extension['path']).is_absolute(),'SQLite extension identity malformed')
    else:
        image=extension['executable']
        require(extension['origin']=='built-in' and re.fullmatch('[0-9a-f]{64}',image['sha256']) is not None and type(image['bytes']) is int and image['bytes']>0 and Path(image['path']).is_absolute() and Path(image['path']).resolve()==Path(before['executable']).resolve(),'Built-in SQLite identity malformed')
    require(before['vfs']==('win32' if pre['platform']=='win32' else 'unix') and before['vfsVersion']==3,'Native VFS identity differs')
    write(args.output,{'sameInstalledInterpreterAndSQLite':True,'beforeSha256':sha(args.before),'afterSha256':sha(args.after),'executable':before['executable'],'library':before['library'],'extension':before['extension'],'vfs':before['vfs'],'candidate':before['candidate']})


def main():
    parser=argparse.ArgumentParser();sub=parser.add_subparsers(dest='command',required=True)
    p=sub.add_parser('preflight')
    for name in ['source','head','tree','parent','store-sha256','harness','harness-sha256','collection','platform','python','output']:p.add_argument('--'+name,required=True)
    p=sub.add_parser('contracts')
    for name in ['collection','platform','junit','collect-log','output']:p.add_argument('--'+name,required=True)
    p=sub.add_parser('compare-runtime')
    for name in ['before','after','preflight','output']:p.add_argument('--'+name,required=True)
    args=parser.parse_args();{'preflight':preflight,'contracts':contracts,'compare-runtime':compare_runtime}[args.command](args)

if __name__=='__main__':main()
