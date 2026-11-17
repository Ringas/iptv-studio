import assert from 'node:assert/strict';
import {PlaybackBuffer,recoveryDecision} from './playback.mjs';
const qualified={pipeline:true,managed:true,rechecking:false,gaps:0,retries:0,advanced:true,decoded:true,decoded_frames:6,height:1080,speed:984.2};
const decision=b=>{const s=b.snapshot();return recoveryDecision({...qualified,stalls:s.stalls,stall_seconds:s.stall_seconds,buffer_unsettled:s.is_starving||s.available_seconds<=0});};
// AnDo-like 0.15s gap: a ready segment closes the gap; the gate must accept it.
let b=new PlaybackBuffer();b.append(1,0);b.append(1,1.15);
assert.equal(b.snapshot().stall_seconds,.15);assert.equal(b.snapshot().stalls,1);assert(decision(b).eligible);
// Repeated tail sampling and then refilling must not count the same gap twice.
b=new PlaybackBuffer();b.append(1,0);b.advance(1.15);assert(!decision(b).eligible);assert(decision(b).reasons.includes('buffer_gap_not_closed'));
b.advance(1.2);b.advance(1.2);assert.equal(b.snapshot().stalls,1);assert.equal(b.snapshot().stall_seconds,.2);
b.append(1,1.25);assert.equal(b.snapshot().stalls,1);assert.equal(b.snapshot().stall_seconds,.25);assert(decision(b).eligible);
// Extending the same gap beyond 1s is ineligible, even though it is one episode.
b=new PlaybackBuffer();b.append(1,0);b.advance(1.15);b.append(1,2.01);
assert.equal(b.snapshot().stalls,1);assert.equal(b.snapshot().stall_seconds,1.01);assert(!decision(b).eligible);
// Refilling separates two genuine stalls. Do not merge them to force recovery.
b=new PlaybackBuffer();b.append(1,0);b.append(1,1.15);b.append(1,2.3);
assert.equal(b.snapshot().stalls,2);assert.equal(b.snapshot().stall_seconds,.3);assert(!decision(b).eligible);
// No wall-time charge before the first verified ready segment. Exact 0.1s
// threshold and 1s recovery limit use microseconds, not rounded result fields.
b=new PlaybackBuffer();b.advance(99);b.append(1,100);b.advance(101.1);
assert.equal(b.snapshot().stalls,0);b.advance(101.100001);assert.equal(b.snapshot().stalls,1);
b=new PlaybackBuffer();b.append(1,0);b.append(1,2);assert(decision(b).eligible);
b=new PlaybackBuffer();b.append(1,0);b.append(1,2.000001);assert(!decision(b).eligible);
// Validation CPU time is still charged to readiness, as before; no hiding it.
b=new PlaybackBuffer();b.append(1,0);b.append(1,1.15);assert.equal(b.snapshot().stall_seconds,.15);
assert(!recoveryDecision({...qualified,stalls:1,stall_seconds:.15,decoded:false}).eligible);
assert(!recoveryDecision({...qualified,stalls:1,stall_seconds:.15,decoded_frames:2}).eligible);
assert.throws(()=>b.append(0,3));assert.throws(()=>b.advance(-1));assert.throws(()=>b.advance(0));
console.log('PASS: continuous gap counted once, open tail cannot start clean recovery, closed 0.15s qualifies, two gaps/>1s rejected, exact thresholds, verified readiness clock retained');
