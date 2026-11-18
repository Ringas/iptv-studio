import fs from 'node:fs';
import assert from 'node:assert/strict';
import {upstreamFailure} from './media-http.mjs';
const rows=JSON.parse(fs.readFileSync(new URL('./fixtures/upstream-errors.json',import.meta.url)));
for(const row of rows){
 const value=upstreamFailure(row.body,row.headers,row.stage,row.httpStatus);
 const actual=value?Object.fromEntries(Object.keys(row.expected||{}).map(k=>[k,value[k]??null])):null;
 assert.deepEqual(actual,row.expected,row.name);
 if(row.name==='redacted message')assert(!value.note.includes('secret-value')&&!value.note.includes('sign=private'));
}
const old=Date.now;try{Date.now=()=>Date.parse('2026-01-01T00:00:00Z');assert.equal(upstreamFailure('HTTP 503',{'retry-after':'Thu, 01 Jan 2026 00:00:35 GMT'}).retryAfter,35);}finally{Date.now=old;}
console.log(`PASS: ${rows.length} upstream error fixtures, HTTP-date cooldown and credential redaction.`);
