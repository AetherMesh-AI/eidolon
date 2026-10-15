"""Real filesystem verification of the pinned compiler; never executes tsc."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import threading

import pytest

from eidolon_cli.organization_project_compiler_bundle import compiler_inventory, copy_verified_compiler


@pytest.fixture
def package():
    path = os.environ.get('EIDOLON_TEST_TYPESCRIPT_BUNDLE')
    if not path:
        if os.environ.get('EIDOLON_REQUIRE_NODE_SANDBOX') == '1':
            pytest.fail('Native compiler lane requires the exact prepared compiler package')
        pytest.skip('Exact external compiler package was not prepared for this invocation')
    return Path(path)


def test_inventory_is_independently_pinned_and_bounded():
    inventory = compiler_inventory()
    assert inventory['package'] == 'typescript' and inventory['version'] == '6.0.3'
    assert len(inventory['files']) == 140
    assert sum(item['bytes'] for item in inventory['files']) == 24346827
    assert inventory['integrity'].startswith('sha512-')
    assert any(item['path'] == 'lib/typescript.js' for item in inventory['files'])


def test_actual_package_copy_has_exact_identity_and_no_execution_authority(package, tmp_path):
    output = tmp_path / 'verified'
    receipt = copy_verified_compiler(package, output)
    assert receipt['executionAuthorized'] is False
    assert receipt['files'] == compiler_inventory()['files']
    for item in receipt['files']:
        content = (output / item['path']).read_bytes()
        assert len(content) == item['bytes']
        assert hashlib.sha256(content).hexdigest() == item['sha256']
        assert (output / item['path']).stat().st_mode & 0o222 == 0


@pytest.mark.parametrize('attack', ['replaced', 'same_size', 'manifest', 'missing', 'extra', 'symlink', 'hardlink', 'fifo', 'oversize'])
def test_real_bundle_mutations_are_rejected(package, tmp_path, attack):
    source = tmp_path / 'source'
    shutil.copytree(package, source)
    target = source / 'lib' / 'typescript.js'
    if attack == 'replaced':
        target.write_bytes(b'process.exit(0);')
    elif attack == 'same_size':
        content = bytearray(target.read_bytes())
        content[len(content) // 2] ^= 1
        target.write_bytes(content)
    elif attack == 'manifest':
        target.write_bytes(b'process.exit(0);')
        (source / 'manifest.json').write_text(json.dumps({'sha256': hashlib.sha256(target.read_bytes()).hexdigest()}))
    elif attack == 'missing':
        target.unlink()
    elif attack == 'extra':
        (source / 'extra.js').write_text('unexpected')
    elif attack in ('symlink', 'hardlink', 'fifo'):
        target.unlink()
        outside = tmp_path / 'outside'; outside.write_text('outside')
        if attack == 'symlink':
            target.symlink_to(outside)
        elif attack == 'hardlink':
            os.link(outside, target)
        else:
            os.mkfifo(target)
    else:
        with target.open('ab') as stream:
            stream.write(b'extra')
    with pytest.raises((OSError, ValueError)):
        copy_verified_compiler(source, tmp_path / 'output')
    assert not (tmp_path / 'output').exists()
    assert not list(tmp_path.glob('.compiler-verify-*'))


def test_ancestor_symlink_and_swap_cannot_redirect_copy(package,tmp_path,monkeypatch):
    source=tmp_path/'source';shutil.copytree(package,source)
    alias=tmp_path/'alias';alias.symlink_to(source,target_is_directory=True)
    with pytest.raises(OSError):
        copy_verified_compiler(alias,tmp_path/'linked-output')
    original=os.open
    def swap(name,flags,*args,**kwargs):
        if name=='lib' and kwargs.get('dir_fd') is not None and not (source/'old-lib').exists():
            (source/'lib').rename(source/'old-lib')
            (source/'lib').symlink_to(package/'lib',target_is_directory=True)
        return original(name,flags,*args,**kwargs)
    monkeypatch.setattr(os,'open',swap)
    with pytest.raises(OSError):
        copy_verified_compiler(source,tmp_path/'raced-output')


def test_cancelled_copy_never_returns_verified_receipt(package,tmp_path):
    cancel=threading.Event();cancel.set()
    with pytest.raises(ValueError,match='cancelled'):
        copy_verified_compiler(package,tmp_path/'output',cancel)
    assert not (tmp_path/'output').exists()
    assert not list(tmp_path.glob('.compiler-verify-*'))


@pytest.mark.parametrize('path',['relative','/tmp/../outside','/tmp//bundle','/tmp/bundle/','/'+'a'*4096])
def test_noncanonical_owner_path_is_rejected(tmp_path,path):
    with pytest.raises(ValueError):
        copy_verified_compiler(path,tmp_path/'output')


def test_mid_copy_cancellation_cleans_partial_tree(package,tmp_path,monkeypatch):
    cancel=threading.Event()
    original=os.read
    def read_then_cancel(descriptor,count):
        data=original(descriptor,count)
        cancel.set()
        return data
    monkeypatch.setattr(os,'read',read_then_cancel)
    with pytest.raises(ValueError,match='cancelled'):
        copy_verified_compiler(package,tmp_path/'output',cancel)
    assert not (tmp_path/'output').exists()
    assert not list(tmp_path.glob('.compiler-verify-*'))


def test_final_seal_cancellation_rolls_back_verified_bytes(package,tmp_path,monkeypatch):
    cancel=threading.Event()
    original=Path.chmod
    def seal_then_cancel(path,mode,*args,**kwargs):
        result=original(path,mode,*args,**kwargs)
        if path.name.startswith('.compiler-verify-') and mode==0o555:
            cancel.set()
        return result
    monkeypatch.setattr(Path,'chmod',seal_then_cancel)
    with pytest.raises(ValueError,match='cancelled'):
        copy_verified_compiler(package,tmp_path/'output',cancel)
    assert not (tmp_path/'output').exists()
    assert not list(tmp_path.glob('.compiler-verify-*'))


def test_existing_destination_is_preserved(package,tmp_path):
    destination=tmp_path/'output';destination.mkdir();marker=destination/'keep';marker.write_text('preserve')
    with pytest.raises(FileExistsError):
        copy_verified_compiler(package,destination)
    assert marker.read_text()=='preserve'
    assert not list(tmp_path.glob('.compiler-verify-*'))


def test_cleanup_failure_preserves_original_cancellation(package,tmp_path,monkeypatch):
    cancel=threading.Event()
    original_read=os.read
    original_remove=shutil.rmtree
    def read_then_cancel(descriptor,count):
        block=original_read(descriptor,count);cancel.set();return block
    def failed_cleanup(*args,**kwargs):
        raise OSError('injected cleanup failure')
    monkeypatch.setattr(os,'read',read_then_cancel)
    monkeypatch.setattr(shutil,'rmtree',failed_cleanup)
    with pytest.raises(ValueError,match='cancelled') as error:
        copy_verified_compiler(package,tmp_path/'output',cancel)
    assert any('cleanup failed' in note for note in error.value.__notes__)
    assert not (tmp_path/'output').exists()
    monkeypatch.setattr(shutil,'rmtree',original_remove)
    for path in tmp_path.glob('.compiler-verify-*'):
        original_remove(path)


@pytest.mark.parametrize('mode',['duplicate','cancel'])
def test_unstable_directory_enumeration_fails_closed(package,tmp_path,monkeypatch,mode):
    from contextlib import contextmanager
    from types import SimpleNamespace
    cancel=threading.Event();original=os.scandir;injected=False
    @contextmanager
    def altered():
        if mode=='cancel':
            cancel.set()
        yield iter([SimpleNamespace(name='LICENSE.txt'),SimpleNamespace(name='LICENSE.txt')])
    def scan(path):
        nonlocal injected
        if isinstance(path,int) and not injected:
            injected=True
            return altered()
        return original(path)
    monkeypatch.setattr(os,'scandir',scan)
    with pytest.raises(ValueError,match='cancelled|unstable'):
        copy_verified_compiler(package,tmp_path/'output',cancel)
    assert not (tmp_path/'output').exists()
    assert not list(tmp_path.glob('.compiler-verify-*'))
