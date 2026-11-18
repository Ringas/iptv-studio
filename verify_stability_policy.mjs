import assert from 'node:assert/strict';
import {recoveryDecision} from './playback.mjs';
const good={pipeline:true,managed:true,rechecking:false,stalls:1,stall_seconds:.42,gaps:0,retries:0,advanced:true,decoded:true,decoded_frames:6,height:720,speed:101};
assert(recoveryDecision(good).eligible);
for(const bad of [{pipeline:false},{managed:false},{rechecking:true},{stalls:0},{stalls:2},{stall_seconds:1.01},{gaps:1},{retries:1},{advanced:false},{decoded:false},{decoded_frames:2},{decoded_frames:NaN},{speed:Infinity},{height:Infinity},{buffer_unsettled:true},{height:719},{speed:100},{speed:0}])assert(!recoveryDecision({...good,...bad}).eligible,JSON.stringify(bad));
console.log('PASS: only isolated <=1s buffer shortage with qualified decoded live media enters same-session recovery; strict speed/height/mode/fault boundaries');
