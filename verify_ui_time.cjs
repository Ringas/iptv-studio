const fs=require('fs'),vm=require('vm'),assert=require('assert/strict');
const js=fs.readFileSync('ui.js','utf8');
const context=vm.createContext({state:{timezone:'Asia/Shanghai'}});
vm.runInContext(js.slice(js.indexOf('function formatTime('),js.indexOf('// Normalize legacy')),context);
const format=value=>context.formatTime(value);
assert.equal(format('2026-11-17T09:21:40.377Z'),'2026-11-17 17:21:40.377');
assert.equal(format('2026-11-17T17:21:40.377+08:00'),format('2026-11-17T09:21:40.377Z'));
assert.equal(format(0),'1970-01-01 08:00:00.000');
assert.equal(format('2026-11-17T16:00:00Z'),'2026-11-18 00:00:00.000');
for(const x of [null,undefined,'','invalid'])assert.equal(format(x),'未知');
context.state.timezone='America/New_York';
assert.equal(format('2026-07-01T12:00:00Z'),'2026-07-01 08:00:00.000');
assert.equal(format('2026-01-01T12:00:00Z'),'2026-01-01 07:00:00.000');
context.state.timezone='invalid';assert.equal(format(0),'1970-01-01 08:00:00.000');
context.state={};assert.equal(format(0),'1970-01-01 08:00:00.000');
for(const expression of ['formatTime(t.at)','formatTime(r.test.at)','formatTime(cfg.next)','formatTime(s.updated)','formatTime(s.next_update)','formatTime(p.timestamp)'])assert(js.includes(expression),expression);
// Task log rendering remains deliberately unchanged.
assert(js.includes('new Date(row.ts*1000).toLocaleString()'));
console.log('通过：统一界面时间、毫秒、上海默认、跨日、时区覆盖与夏令时、空值；任务日志保留原样');
