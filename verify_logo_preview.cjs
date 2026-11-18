const fs=require('fs'),vm=require('vm'),assert=require('assert/strict');
const source=fs.readFileSync('ui.js','utf8');
const code=source.slice(source.indexOf('function logoPreviewSize('),source.indexOf("$('closeLogoPreview').onclick"));
const image={attrs:{},style:{},naturalWidth:300,naturalHeight:180,getAttribute(k){return this.attrs[k]??null},removeAttribute(k){delete this.attrs[k]},set src(v){this.attrs.src=v},get src(){return this.attrs.src}};
const dialog={open:false,dataset:{},style:{},showModal(){this.open=true},close(){this.open=false}};
const elements={logoPreviewImage:image,logoPreviewDialog:dialog,logoPreviewTitle:{},logoPreviewStatus:{}};
const ctx=vm.createContext({$:id=>elements[id],state:{channels:[{name:'fixture',label:'样本',logo_candidates:['/light.png']}]},usableLogo:c=>c?.logo_candidates?.[0]});vm.runInContext(code,ctx);
// A pending thumbnail must open a loading preview rather than silently ignore the click.
ctx.openLogoPreview({dataset:{logoOwner:'fixture'},querySelector:()=>({alt:'样本台标',getAttribute:()=>'/off.png',complete:false,naturalWidth:0})});
assert.equal(dialog.open,true);assert.equal(dialog.dataset.owner,'fixture');assert.equal(image.hidden,true);assert.match(elements.logoPreviewStatus.textContent,/加载/);
image.onload();assert.equal(image.hidden,false);assert.equal(image.style.width,'300px');
// Already-open preview tracks a new mode and resizes after the replacement finishes loading.
ctx.syncLogoPreview();assert.equal(image.src,'/light.png');assert.equal(image.hidden,true);image.naturalWidth=80;image.naturalHeight=40;image.onload();assert.equal(image.style.width,'96px');
const onload=image.onload;ctx.syncLogoPreview();assert.equal(image.onload,onload,'same URL must not restart loading');
ctx.state.channels[0].logo_candidates=['/dark.png'];ctx.syncLogoPreview();assert.equal(image.src,'/dark.png');image.onerror();assert.match(elements.logoPreviewStatus.textContent,/无法加载/);
ctx.state.channels=[];ctx.syncLogoPreview();assert.equal(dialog.open,false,'missing logo must not retain a stale preview');
console.log('通过：加载中可预览、加载状态、原尺寸缩放、跨模式更新、相同地址不重载、失败提示和无图清理。');
