"""Run the real address-hash helpers with a minimal browser sandbox."""
import shutil
import subprocess

import pytest


def test_location_hash_round_trip_fallbacks_and_page_limit():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node is not installed')
    script = r'''
const fs = require('fs'), vm = require('vm'), assert = require('assert/strict');
const source = fs.readFileSync('web/app.js', 'utf8');
const functions = source.slice(source.indexOf('function locationHash('), source.indexOf('function saveLocation('));
const sandbox = {URLSearchParams, PAGE_SIZE: 120, SCOPE_NAMES: {selected: '精选', all: '全部候选', favorites: '收藏'},
  state: {filter: 'selected'}, galleryScope: {kind: null, value: ''},
  sourceOptions: kind => kind === 'live' ? ['2025 盗作', '2024 前世'] : ['春泥棒']};
vm.createContext(sandbox); vm.runInContext(functions, sandbox);
const {locationHash, parseLocation, applyLocationFilters, limitFor, state, galleryScope} = sandbox;

// Round trip keeps Chinese and spaces; empty fields are left out.
const view = {tab: '', kind: 'live', src: '2025 盗作', scope: 'all', c: 'ab12cd34ef56ab12cd34ef56', v: 'ab12cd34ef56ab12cd34ef56'};
const hash = locationHash(view);
assert.ok(hash.startsWith('#') && !hash.includes('tab='));
assert.deepEqual({...parseLocation(hash)}, {kind: 'live', src: '2025 盗作', scope: 'all', c: view.c, v: view.v});
assert.equal(locationHash({tab: '', kind: '', c: ''}), '');

applyLocationFilters(parseLocation(hash));
assert.equal(state.filter, 'all'); assert.equal(galleryScope.kind, 'live'); assert.equal(galleryScope.value, '2025 盗作');

// Unknown or hostile values fall back to the defaults instead of throwing.
state.filter = 'selected'; galleryScope.kind = null; galleryScope.value = '';
applyLocationFilters(parseLocation('#scope=constructor&kind=evil&src=x&c=zzz'));
assert.equal(state.filter, 'selected'); assert.equal(galleryScope.kind, null);
applyLocationFilters(parseLocation('#kind=live&src=%E4%B8%8D%E5%AD%98%E5%9C%A8'));
assert.equal(galleryScope.kind, 'live'); assert.equal(galleryScope.value, '');
applyLocationFilters(parseLocation('#kind=user&src=2025%20盗作'));
assert.equal(galleryScope.kind, 'user'); assert.equal(galleryScope.value, '');
assert.deepEqual({...parseLocation('#%%%garbage')}, {});
assert.deepEqual({...parseLocation('')}, {});

// A frame past the first 120 cards widens the rendered page to include it.
assert.equal(limitFor(0), 120); assert.equal(limitFor(119), 120); assert.equal(limitFor(120), 240); assert.equal(limitFor(500), 600);
'''
    subprocess.run([node, '-e', script], check=True)
