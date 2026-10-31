"""Run the real request/prefetch functions with deterministic browser mocks."""
from pathlib import Path
import shutil
import subprocess

import pytest


def test_frontend_priorities_cancellation_and_power_budget():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node is not installed')
    script = r'''
const fs = require('fs'), vm = require('vm'), assert = require('assert/strict');
const source = fs.readFileSync('web/app.js', 'utf8');
const functions = source.slice(source.indexOf('function nativeFor('), source.indexOf('async function loadNativePreview'));
let mode = 'battery', cancelled = false, calls = [];
const state = {native: new Map(), nativePending: new Map()};
const sandbox = {state, Date, setTimeout, Image: class {async decode() {}}, fetch: async (url, options) => {
  if(url === '/api/power') {
    if(mode === 'failure') throw Error('offline');
    return {ok:true, json:async()=>({on_battery: mode === 'battery'})};
  }
  const body = JSON.parse(options.body); calls.push(body);
  return {ok:true,json:async()=>({id:body.candidate_id,status:cancelled?'cancelled':'done',preview:'/image'})};
}};
vm.createContext(sandbox); vm.runInContext(functions,sandbox);
const list = [0,1,2,3].map(id=>({id:String(id),source_available:true}));
(async()=>{
  for(const [power,count] of [['battery',1],['ac',2],['failure',2]]) {
    mode=power; calls=[];state.native.clear();
    await sandbox.prefetchAfter(list,0,()=>true);
    assert.equal(calls.length,count);
    assert(calls.every(c=>c.priority==='prefetch'));
  }
  calls=[];state.native.clear();
  await sandbox.prefetchAfter(list,0,()=>false);
  assert.equal(calls.length,0);
  cancelled=true;
  assert.equal(await sandbox.nativeFor(list[0]),null);
  assert.equal(state.nativePending.size,0);
  cancelled=false;
  await sandbox.nativeFor(list[0]);
  calls=[];
  await sandbox.nativeFor(list[0]);
  assert.equal(calls[0].priority,'current'); // cached current still clears server queue
  assert.equal(calls.length,1);
})().catch(error=>{console.error(error);process.exitCode=1;});
'''
    result = subprocess.run([node, '-e', script], cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
