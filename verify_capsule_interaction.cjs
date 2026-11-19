const fs=require('fs'),vm=require('vm'),assert=require('assert/strict');
const source=fs.readFileSync('ui.js','utf8');
const code=source.slice(source.indexOf('function capsuleHit('),source.indexOf("document.addEventListener('click',safely(async e=>{"));
const listeners={},timers=[];vm.runInNewContext(code,{WeakSet,Math,setTimeout:fn=>timers.push(fn),document:{addEventListener:(kind,fn)=>listeners[kind]=fn}});
function control(native=false){return {disabled:false,checked:false,count:0,matches:s=>s==='input'&&native,getBoundingClientRect:()=>({left:100,top:100,width:native?36:88,height:native?22:32}),getAttribute:()=>String(this?.checked),click(){this.count++;this.checked=!this.checked;},closest(){return this;}};}
function event(c,x,y,detail=1){return {target:c,clientX:x,clientY:y,detail,pointerId:1,preventDefault(){this.prevented=true;},stopImmediatePropagation(){this.stopped=true;}};}
for(const native of [false,true]){
 const c=control(native);c.getAttribute=()=>String(c.checked);
 const x=native?110:112,y=native?110:116;
 let e=event(c,175,y);listeners.click(e);assert(e.prevented&&e.stopped,'text/blank click allowed');
 e=event(c,x,y);listeners.click(e);assert(!e.prevented,'capsule click blocked');
 e=event(c,0,0,0);listeners.click(e);assert(!e.prevented,'keyboard blocked');
 listeners.pointerdown(event(c,x,y));listeners.pointerup(event(c,x+15,y));assert.equal(c.count,1);assert(c.checked);
 e=event(c,x+15,y);listeners.click(e);assert(e.prevented,'swipe followed by duplicate click');
 listeners.pointerdown(event(c,x+15,y));listeners.pointerup(event(c,x,y));assert.equal(c.count,2);assert(!c.checked);
 timers.splice(0).forEach(fn=>fn());
 listeners.pointerdown(event(c,175,y));listeners.pointerup(event(c,195,y));assert.equal(c.count,2,'outside swipe toggled');
 listeners.pointerdown(event(c,x,y));listeners.pointerup(event(c,x+12,y+30));assert.equal(c.count,2,'vertical scroll toggled');
 listeners.pointerdown(event(c,x,y));listeners.pointercancel();listeners.pointerup(event(c,x+15,y));assert.equal(c.count,2,'cancelled gesture toggled');
 c.disabled=true;listeners.pointerdown(event(c,x,y));listeners.pointerup(event(c,x+15,y));assert.equal(c.count,2,'disabled switch toggled');
}
console.log('通过：线路/设置胶囊本体点击、文字空白阻止、左右滑动去重、纵向滚动、取消、禁用及键盘操作。');

for(const native of [false,true]){
 const c=control(native),attrs={title:'胶囊解释'};c.dataset={};
 c.getAttribute=k=>attrs[k]??null;c.setAttribute=(k,v)=>{attrs[k]=v;};c.removeAttribute=k=>{delete attrs[k];};
 const y=native?110:116;
 listeners.pointerover(event(c,175,y));assert(!attrs.title,'状态文字显示解释');
 listeners.pointermove(event(c,112,y));assert.equal(attrs.title,'胶囊解释');
 listeners.pointermove(event(c,175,y));assert(!attrs.title,'移至文字后解释未清除');
 listeners.pointermove(event(c,112,y));assert.equal(attrs.title,'胶囊解释');
 listeners.pointerout(event(c,112,y));assert(!attrs.title,'离开后解释未清除');
}
console.log('通过：解释仅在胶囊本体悬停时出现，状态文字、空白与离开后均无提示。');
