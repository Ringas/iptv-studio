const $=id=>document.getElementById(id);
const esc=x=>String(x??'').replace(/[<>&"']/g,v=>({'<':'&lt;','>':'&gt;','&':'&amp;','"':'&quot;',"'":'&#39;'}[v]));
let state, editing, wasRunning=false, busy=false, noticeTimer;
function formatTime(value){
 if(value==null||value==='')return '未知';
 const date=new Date(typeof value==='number'?value*1000:value);
 if(!Number.isFinite(date.getTime()))return '未知';
 let timezone=state?.timezone||'Asia/Shanghai',parts;
 const options={year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hourCycle:'h23'};
 try{parts=new Intl.DateTimeFormat('en-GB',{...options,timeZone:timezone}).formatToParts(date);}
 catch{parts=new Intl.DateTimeFormat('en-GB',{...options,timeZone:'Asia/Shanghai'}).formatToParts(date);}
 const p=Object.fromEntries(parts.map(({type,value})=>[type,value]));
 return `${p.year}-${p.month}-${p.day} ${p.hour}:${p.minute}:${p.second}.${String(date.getUTCMilliseconds()).padStart(3,'0')}`;
}
// Normalize legacy diagnostic wording at display time; stored records stay unchanged.
function diagnosticText(value){
 return String(value??'').replace(/测速/g,'检测').replace(/测试时间/g,'检测时间').replace(/秒后测试/g,'秒后检测').replace(/测试目标/g,'检测目标').replace(/要测试的线路/g,'要检测的线路');
}
function notice(text){
 text=diagnosticText(text);
 const box=$('notice');
 if(box.matches(':popover-open'))box.hidePopover();
 box.setAttribute('popover','manual');box.textContent=text;
 box.classList.remove('notice-leaving');
 if(text)box.showPopover();
 clearTimeout(noticeTimer);
 noticeTimer=setTimeout(()=>{
  box.classList.add('notice-leaving');
  noticeTimer=setTimeout(()=>{if(box.matches(':popover-open'))box.hidePopover();box.textContent='';box.classList.remove('notice-leaving');},160);
 },Math.min(8000,Math.max(3200,String(text).length*75)));
}
async function api(path,data){
 const r=await fetch('/api/'+path,data?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)}:{});
 if(!r.ok){
   const text=await r.text();let detail;
   if(r.headers.get('content-type')?.includes('application/json')){try{detail=JSON.parse(text);}catch{}}
   const error=Error(typeof detail?.error==='string'?detail.error:text);
   if(detail)error.upstream={httpStatus:detail.httpStatus,retryAfter:detail.retryAfter,retryable:detail.retryable,stage:detail.stage};
   throw error;
 } return r.json();
}
function safely(fn){return async(...args)=>{try {await fn(...args);}catch(e){notice(e.message);}};}
function applyHelp(root=document){
 const help=[
  ['#viewCards','按频道浏览，点击查看线路和频道设置。'],
  ['#viewLines','逐条查看检测结果、播放和调整启停。'],
  ['#group','仅显示所选分组；选择全部分组恢复完整目录。'],
  ['#query','按频道名、直播平台或地址筛选。'],
  ['#clearPicks','清空已选项目，保留批量模式。'],
  ['#defaultHours','单个订阅可在源管理中另设间隔或关闭更新。'],
  ['#autoProbe','仅在定时更新成功后自动检测。'],
  ['#strictMode','关闭后，检测只更新结果，不改变启停。超时、限流等未能验证的结果不重置手动选择。'],
  ['#retryPlayer','重新连接当前线路并尝试播放。'],
  ['#logKind','切换要查看的记录类型，清除操作也只作用于该类型。'],
  ['#channelEdit','展开频道改名、分组、台标和节目单设置。'],
  ['#chooseLogo','上传 PNG、JPEG 或 WebP 台标，最大 2 MB，上传后立即生效。'],
  ['#removeLogo','移除上传的台标，恢复自动匹配。'],
  ['#fields [name=group]','将频道及其线路移到所选分组；选自动分组可恢复智能分类。'],
  ['#fields [name=epg]','选择与本频道对应的节目表；保留自动匹配可由程序识别。'],
  ['#fields [name=target]','将本频道的线路归入所选频道；选保持独立可取消合并。'],
  ['#fields [name=frequency]','跟随默认间隔、单独指定小时数，或关闭定时更新。'],
  ['#fields [name=kind]','直播提供线路，节目单提供节目，台标订阅提供频道图片。'],
  ['#deleteSource','删除订阅及其已保存内容，原订阅文件不受影响。'],
  ['[data-unmerge]','将这个成员拆回独立频道，保留它的线路。'],
  ['[data-address],[data-hidden-address]','展开完整直播地址，可选中复制。'],
  ['[data-guide]','查看此频道已匹配的节目单。'],
  ['.feed-guides summary','查看直播清单内附带的节目单地址，更新订阅时会一并获取。'],
  ['.export-list a[href="/live.m3u"]','打开 M3U 文本；把同一地址填入播放器即可订阅。'],
  ['.export-list a[href="/live.txt"]','以 TXT 格式发布频道及多条线路。'],
  ['.export-list a[href="/live.json"]','查看结构化频道和线路数据，供其他工具读取。'],
  ['.export-list a[href="/guide.xml"]','打开 XMLTV 节目单，供播放器匹配节目。']
 ];
 for(const [selector,text] of help)for(const el of root.querySelectorAll(selector))if(!el.title)el.title=text;
 for(const button of root.querySelectorAll('[data-fold]'))button.title=button.getAttribute('aria-expanded')==='true'?'收起本组频道或线路。':'展开本组频道或线路。';
 for(const button of root.querySelectorAll('[data-focus]'))button.title=$('group').value===button.dataset.focus?'恢复显示全部分组。':'仅显示这个分组，再次点击恢复全部分组。';
}
let revision=null,liveJob=null,liveLogoJob=null,cardChannels=new Map();
let stateRequest=0,appliedRequest=0;
async function reload(live=false){
 const request=++stateRequest,next=await api('state');
 if(request<appliedRequest)return;
 appliedRequest=request;state=next;
 document.body.dataset.logoBeauty=state.settings.logo_beauty||'off';
 if(!live)failedLogos.clear();
 syncLogoPreview();
 cardChannels=new Map(state.channels.map(c=>[c.name,c]));
 $('edition').textContent=state.preview?'预览模式':'本地工作区';
 $('edition').title=state.preview?'独立预览版：定时更新暂未运行，手动更新和检测仍可使用。':'当前运行的是正式工作区，定时更新按设置执行。';
 if($('dialog').open)updateTitlePreview?.();
 if(!live)guideCache.clear();
 $('stats').innerHTML=[[state.exported,'已发布频道'],[state.lines,'已发布线路'],[state.channels.filter(c=>c.published_routes===1).length,'单线路频道'],[state.channels.filter(c=>c.published_routes>1).length,'多线路频道']].map(([v,k])=>`<div>${k}<strong>${v}</strong></div>`).join('');
 const groups=directoryGroups(),old=$('group').value;
 $('group').innerHTML='<option value="">全部分组</option>'+groups.map(g=>`<option value="${esc(g)}">${esc(g)}</option>`).join('');
 $('group').value=groups.includes(old)?old:'';
 $('librarySummary').textContent=`${state.channels.length} 个频道　/　${state.channels.reduce((n,c)=>n+c.lines.length,0)} 条线路　/　${groups.length} 个分组　/　${state.exported} 个已发布`;
 if(healthFilter&&!groups.includes(healthFilter.group)){healthFilter=null;picked=selections[view];}
 if(drill&&!state.channels.some(c=>c.name===drill))drill='';
 $('hiddenCount').textContent=(state.hidden||[]).length;
 if($('hiddenDialog').open)renderHidden();
 render(live);refreshDetailResults();
 if(live)return;
 renderFeeds();
 const cfg=state.settings,f=$('schedule').elements;
 renderAccess(state.password_mode);
 if(!settingsDirty){
   f.probe_mode.value=probeModeChoice(cfg);f.criteria.value=cfg.criteria;f.min_height.value=cfg.min_height;f.min_speed.value=cfg.min_speed;
   f.logo_beauty.value=cfg.logo_beauty||'off';f.hours.value=cfg.minutes/60;f.mode.value=cfg.mode;f.strict.checked=cfg.strict;f.auto_probe.checked=cfg.auto_probe;
   updateCriteriaFields();
 }
 $('next').textContent=state.preview?'预览中暂停定时任务':cfg.next?'下次计划更新　'+formatTime(cfg.next):'定时更新已关闭';
}
const selections={lines:new Set(),channels:new Set()};
function renderAccess(mode){
 const locked=mode==='deployment',enabled=mode==='enabled';
 $('accessPassword').disabled=locked;
 $('accessPassword').placeholder=locked?'已由部署配置设置':enabled?'输入新密码，留空可关闭':'留空表示无密码';
 $('savePassword').disabled=locked||(!enabled&&!$('accessPassword').value);
 $('accessHint').textContent=locked?'密码由部署配置管理。登录账号为 admin。':(enabled?'已开启。':'当前免密码。')+'设置后用 admin 登录管理台，播放器订阅仍可直接使用。';
}
$('accessPassword').oninput=()=>renderAccess(state.password_mode);
$('accessForm').onsubmit=safely(async e=>{
 e.preventDefault();
 const input=$('accessPassword'),password=input.value;
 if(!password&&!confirm('关闭访问密码后，局域网用户可直接管理频道。确定关闭吗？'))return;
 $('savePassword').disabled=true;
 try{
   await api('access-password',{password});
   input.value='';
   state.password_mode=password?'enabled':'off';
   notice(password?'密码已保存，请使用 admin 和新密码登录。':'已关闭管理台访问密码。');
   if(password)location.reload();
 }finally{renderAccess(state.password_mode);}
});
function directoryGroups(){return [...new Set(state.channels.map(c=>c.group))];}
function hideButton(kind,name,url='',compact=false){
 const label={channel:'隐藏频道',route:'隐藏线路',group:'隐藏分组'}[kind];
 const icon='<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m3 3 18 18M10.6 10.6a2 2 0 0 0 2.8 2.8M6.5 6.5C4.4 8 3 10 2 12c2.2 4.5 5.5 7 10 7 1.9 0 3.5-.4 5-1.2M10 5.2c.7-.1 1.3-.2 2-.2 4.5 0 7.8 2.5 10 7a16 16 0 0 1-3 4"/></svg>';
 return `<button type="button" class="hide-item${compact?' card-hide':''}" aria-label="${label}" title="${label}后，播放器订阅将排除该项，可在隐藏管理中恢复。" data-hide="${esc(JSON.stringify([kind,name,url]))}">${compact?icon:label}</button>`;
}
function shortStreamAddress(address){
 try{
   const url=new URL(address),parts=url.pathname.split('/').filter(Boolean);
   if(!url.host)return address;
   return url.host+(parts.length>1?'/…':'')+(parts.length?'/'+parts.at(-1):'')+(url.search?'?…':'');
 }catch{return address;}
}
function hiddenRoute(r,restore){
 const test=r.test||{};
 const addressKey=JSON.stringify(restore);
 const sources=r.sources?.join('、')||'未匹配到订阅';
 return `<div class="hidden-row hidden-route"><div>
   <div class="hidden-heading"><div class="hidden-name"><input type="checkbox" data-hidden-pick="${esc(addressKey)}" aria-label="选择 ${esc(r.label||r.name)} 的线路${esc(r.url)}"><b>${esc(r.label||r.name)}</b></div>
   <dl class="hidden-metrics"><div><dt>分辨率</dt><dd title="${esc(sizeText(test))}">${resolutionResult(test)}</dd></div>
   <div><dt>速率</dt><dd class="metric">${speedResult(r.name,r)}</dd></div></dl></div>
   <div class="hidden-meta"><small class="hidden-source">来源订阅：${esc(sources)}</small>
   <button type="button" class="address-link hidden-url" data-hidden-address="${esc(r.url)}" aria-label="展开完整地址：${esc(r.url)}" aria-expanded="false" aria-controls="infoPopup">${esc(shortStreamAddress(r.url))}</button></div>
   </div><button type="button" title="仅恢复这条线路，沿用原有启停和检测规则。" data-restore="${esc(addressKey)}">恢复此线路</button></div>`;
}
const restoringOwners=new Set();
function syncHiddenSelection(group){
 const boxes=[...group.querySelectorAll('[data-hidden-pick]')];
 const count=boxes.filter(box=>box.checked).length;
 const toolbar=group.querySelector('.hidden-selection');
 if(!toolbar)return;
 const all=toolbar.querySelector('[data-hidden-all]'),busy=restoringOwners.has(group.dataset.owner);
 all.checked=count>0&&count===boxes.length;
 all.indeterminate=count>0&&count<boxes.length;
 all.disabled=busy;
 toolbar.querySelector('output').textContent=`已选 ${count} 条线路`;
 const submit=toolbar.querySelector('[data-hidden-submit]');
 submit.hidden=count===0;
 toolbar.querySelector('[data-hidden-clear]').disabled=submit.disabled=!count||busy;
 for(const box of boxes){
   box.disabled=busy;
   box.closest('.hidden-route').classList.toggle('marked',box.checked);
 }
}
$('hiddenList').addEventListener('change',e=>{
 const group=e.target.closest('[data-restore-scope]');
 if(!group)return;
 if(e.target.matches('[data-hidden-all]')){
   for(const box of group.querySelectorAll('[data-hidden-pick]'))box.checked=e.target.checked;
 }
 syncHiddenSelection(group);
});
$('hiddenList').addEventListener('click',safely(async e=>{
 const group=e.target.closest('[data-restore-scope]');
 if(!group||!e.target.matches('[data-hidden-clear],[data-hidden-submit]'))return;
 const selected=[...group.querySelectorAll('[data-hidden-pick]:checked')];
 if(e.target.matches('[data-hidden-clear]')){
   for(const box of selected)box.checked=false;
   syncHiddenSelection(group);return;
 }
 const key=group.dataset.owner;
 if(!selected.length||restoringOwners.has(key))return;
 const [scope,owner]=JSON.parse(key);
 const targets=selected.map(box=>{
   const [,name,url]=JSON.parse(box.dataset.hiddenPick);
   return {name,url};
 });
 restoringOwners.add(key);syncHiddenSelection(group);
 try{
   await api('restore-routes',{scope,owner,targets});
   await reload();
   notice(`已恢复 ${targets.length} 条线路`);
 }catch(error){
   notice(error.message);
   await reload();
 }finally{
   restoringOwners.delete(key);
   for(const el of $('hiddenList').querySelectorAll('[data-restore-scope]'))syncHiddenSelection(el);
 }
}));
function renderHidden(){
 if($('hiddenList').contains(popupAnchor))closePopup();
 const picked=new Set([...$('hiddenList').querySelectorAll('[data-hidden-pick]:checked')].map(el=>el.dataset.hiddenPick));
 const expanded=new Set([...$('hiddenList').querySelectorAll('details[open]')].map(el=>el.dataset.owner));
 const items=state.hidden||[];
 const toolbar='<div class="hidden-selection"><label><input type="checkbox" data-hidden-all> 全选</label><output></output><button type="button" data-hidden-clear>清空选择</button><button type="button" class="primary" title="恢复本列表中勾选的线路，沿用原有启停和检测规则。" data-hidden-submit>恢复所选线路</button></div>';
 const standalone=items.filter(item=>item.kind==='route');
 const individual=standalone.length?`<section class="hidden-parent standalone-routes" data-restore-scope data-owner='["route"]'>
   <h3>单独隐藏的线路（${standalone.length}）</h3>${toolbar}
   ${standalone.map(r=>hiddenRoute(r,['route',r.name,r.url])).join('')}</section>`:'';
 const parents=items.filter(item=>item.kind!=='route').map(({kind,name,url})=>{
   const restore=[kind,name,url];
   const group=kind==='group',label=group?'分组':'频道';
   const inventory=(group?state.hidden_groups:state.hidden_channels)||{};
   const routes=Object.hasOwn(inventory,name)?inventory[name]:[];
   const owner=JSON.stringify([kind,name]);
   return `<section class="hidden-parent"><div class="hidden-row"><div class="hidden-name"><span class="tag">${label}</span><b>${esc(name)}</b></div>
     <button type="button" title="解除整个${label}的隐藏，单独隐藏的线路仍可在隐藏管理中恢复。" data-restore="${esc(JSON.stringify(restore))}">恢复整个${label}</button></div>
     <details class="hidden-group" data-restore-scope data-owner="${esc(owner)}" ${expanded.has(owner)?'open':''}><summary>${group?'组内':'频道内'}隐藏线路（${routes.length}）</summary>
     ${routes.length?toolbar:''}
     ${routes.map(r=>hiddenRoute(r,['route',r.name,r.url,{[kind]:name}])).join('')||'<p>当前没有可单独恢复的线路。</p>'}</details></section>`;
 }).join('');
 $('hiddenList').innerHTML=(individual+parents)||'<p class="hidden-empty">暂无隐藏项目</p>';
 for(const box of $('hiddenList').querySelectorAll('[data-hidden-pick]'))box.checked=picked.has(box.dataset.hiddenPick);
 for(const group of $('hiddenList').querySelectorAll('[data-restore-scope]'))syncHiddenSelection(group);
}
const scopedSelections={lines:new Set(),channels:new Set()};
let healthFilter=null;
let picked=selections.channels;
let bulkSelectStep=0;
let bulkSelectGroup='';
function filtered(){
 const q=$('query').value.trim().toUpperCase(),g=$('group').value;
 return state.channels.filter(c=>(!drill||c.name===drill)&&(!g||c.group===g)&&(!healthFilter||c.group===healthFilter.group)&&(!q||[c.label,c.name,...c.lines.flatMap(r=>[r.url,r.platform])].join(' ').toUpperCase().includes(q)));
}

function routeKey(name,url){return JSON.stringify([name,url]);}
function lineView(){return view==='lines';}
function routeRows(list){
 const q=$('query').value.trim().toUpperCase();
 return list.flatMap(c=>c.lines.map((r,i)=>({c,r,i})).filter(({r})=>!q||[c.label,c.name,r.url,r.platform].join(' ').toUpperCase().includes(q)));
}
const healthNames={pass:'可用',fail:'不可用',unmeasured:'未测',uncertain:'待确认'};
function healthOf(r){return r.assessment==='保留'?'pass':r.assessment==='未测'?'unmeasured':['待复测','未能验证','暂时失败'].includes(r.assessment)?'uncertain':'fail';}
function routeHealthCounts(lines){
 const counts={pass:0,fail:0,unmeasured:0,uncertain:0};
 for(const line of lines)counts[healthOf(line)]++;
 return counts;
}
function routeHealthSummary(lines){
 const counts=routeHealthCounts(lines);
 return `${lines.length}条线路`+Object.entries(counts).filter(([,n])=>n).map(([key,n])=>` · ${healthNames[key]}${n}`).join('');
}
function matchingRows(rows){return healthFilter?rows.filter(({c,r})=>c.group===healthFilter.group&&healthOf(r)===healthFilter.status):rows;}
function updateProbeAction(){
 const button=$('probe');
 button.title=healthFilter?`只检测「${healthFilter.group}」当前筛出的${healthNames[healthFilter.status]}线路。`:'检测全部可见线路，含未测和不达标线路；不受搜索或分组筛选限制。';
 button.textContent=healthFilter?'检测筛出线路':'检测全部线路';
 button.disabled=busy||Boolean(healthFilter&&!matchingRows(routeRows(filtered())).length);
}
function visibleKeys(list){
 const rows=matchingRows(routeRows(list));
 return lineView()?rows.map(({c,r})=>routeKey(c.name,r.url)):[...new Set(rows.map(({c})=>c.name))];
}
function selectionStatus(list){
 const keys=visibleKeys(list),count=keys.filter(k=>picked.has(k)).length;
 $('pickedCount').textContent=`已选 ${picked.size} ${lineView()?'条线路':'个频道'}`+(picked.size>count?`（另有 ${picked.size-count} 项在筛选外）`:'');$('clearPicks').disabled=!picked.size;
 const checked=bulkSelectStep===2&&count>0?'mixed':count>0&&count===keys.length?'true':count>0?'mixed':'false';
 $('selectVisible').setAttribute('aria-checked',checked);
 $('selectVisible').title=bulkSelectStep===1?'已选全部筛选结果；再次点击只选当前滚动所在分组。':bulkSelectStep===2?`已选「${bulkSelectGroup}」当前结果；再次点击清空。`:'点击选全部筛选结果；再次点击仅选当前分组；第三次清空。';
 $('selectVisible').disabled=!keys.length;
 const selectedKeys=new Set(actionKeys());
 const flags=[];let blockedChannel=false;
 if(selectedKeys.size)for(const c of state.channels){
   if(lineView()){
     for(const r of c.lines)if(selectedKeys.has(routeKey(c.name,r.url)))flags.push(r.enabled);
   }else if(selectedKeys.has(c.name)){for(const r of c.lines)flags.push(r.enabled);}
 }
 const anyOn=flags.some(Boolean),allOn=anyOn&&flags.every(Boolean)&&!blockedChannel;
 const actions=$('bulkActions');
 actions.setAttribute('aria-label',lineView()?'所选线路操作':'所选频道操作');
 actions.hidden=!flags.length;
 $('bulkPrompt').hidden=flags.length>0;
 for(const button of actions.querySelectorAll('button')){
   const action=button.dataset.bulk,target=lineView()?'线路':'频道';
   button.textContent=(action==='hide'?'隐藏':toggleText(action==='on'))+'所选';
   button.title=action==='hide'?`从目录和播放器订阅中隐藏所选${target}，可在隐藏管理中恢复。`:
     lineView()?`手动${toggleText(action==='on')}所选线路，开启自动启停后，下次首测明确结果可替代此选择。`:`手动${toggleText(action==='on')}所选频道内的全部可见线路，开启自动启停后，下次首测明确结果可替代此选择。`;
   button.hidden=(action==='on'&&allOn)||(action==='off'&&!anyOn);
   button.disabled=changingBulk;
 }
 $('probePicked').hidden=!lineView();$('probePicked').disabled=busy||!picked.size;
 syncBatchPresentation();
}
function groupSelectionKeys(group){return visibleKeys(filtered().filter(c=>c.group===group));}
function syncBatchPresentation(){
 const batch=$('channels').classList.contains('batch-open');
 for(const el of $('catalog').querySelectorAll('[data-selection-key]')){
   const selected=picked.has(el.dataset.selectionKey);
   el.classList.toggle('marked',selected);
   if(batch){
     el.tabIndex=0;
     if(el.matches('article')){el.setAttribute('role','button');el.setAttribute('aria-pressed',String(selected));}
     else el.setAttribute('aria-selected',String(selected));
   }else{el.removeAttribute('tabindex');el.removeAttribute('aria-selected');el.removeAttribute('aria-pressed');if(el.matches('article'))el.removeAttribute('role');}
   for(const button of el.querySelectorAll('button')){
     if(batch){button.tabIndex=-1;button.title='批量模式：点击'+(el.matches('article')?'频道卡片':'线路')+'选中，再次点击取消。';}
     else button.removeAttribute('tabindex');
   }
 }
}
function toggleBatchItem(el){
 const key=el.dataset.selectionKey;
 if(picked.has(key))picked.delete(key);else picked.add(key);
 bulkSelectStep=0;selectionStatus(filtered());
}
function currentCatalogGroup(){
 const edge=workspaceBar.getBoundingClientRect().bottom+headerVisible;
 return [...$('catalog').querySelectorAll('.catalog-group')].find(el=>el.getBoundingClientRect().bottom>edge)?.dataset.group;
}
function sizeText(test){const z=test.resolution;return z?`${z.width} × ${z.height}${z.source?' · '+z.source:''}`:('resolution' in test?'未识别':test.at?'待复测':'未测');}
let changingBulk=false;
function toggleText(on){return on?'启用':'停用';}
function setToggle(button,on){
 const label=toggleText(!on);
 button.value=on?'1':'0';
 setSwitch(button,on,button.dataset.formToggle);
 button.title=button.dataset.formToggle+'当前已'+toggleText(on)+'，点击'+label+'，保存后生效。';
}
function setSwitch(button,on,context='线路'){
 button.classList.add('state-switch');
 button.setAttribute('role','switch');
 button.setAttribute('aria-checked',String(Boolean(on)));
 button.setAttribute('aria-label',context+'启用状态');
 button.textContent='已'+toggleText(on);
}
function routeControl(r){
 const action=r.enabled?'off':'on',label=toggleText(action==='on');
 const source=r.mode==='auto'?'检测自动控制':'手动选择；开启自动启停时，下次首测明确结果可替代此选择';
 const publication=r.publication_reason==='channel_disabled'?'频道已关闭发布，此线路未加入订阅。':r.publication_reason==='not_selected'?'仅发布最优线路，此线路作为备用。':'';
 return {action,label,title:`线路当前已${toggleText(r.enabled)}，点击${label}此线路。${source}。${publication}`};
}
function routeToggle(c,r){
   const {action,title}=routeControl(r);
 return `<button type="button" class="state-switch" role="switch" aria-checked="${Boolean(r.enabled)}" aria-label="${esc(c.label)}线路启用状态" data-policy="${action}" data-owner="${esc(c.name)}" data-url="${esc(r.url)}" title="${esc(title)}">已${toggleText(r.enabled)}</button>`;
}
function defaultPlayRoute(c){return c.lines.find(r=>r.enabled)||c.lines[0];}
function playButton(c,r){
 const number=c.lines.indexOf(r)+1,action=r.enabled?'播放':'试播';
 return `<button type="button" class="play-route" data-play="${esc(r.url)}" data-owner="${esc(c.name)}" title="${action}线路${number} · ${esc(r.platform||'来源未识别')}" aria-label="${action} ${esc(c.label)} · 线路${number}" aria-haspopup="dialog" aria-controls="playerDialog"><svg viewBox="0 0 20 20" aria-hidden="true"><path d="M6 4.4c0-.7.7-1.1 1.3-.7l8 5.6a.9.9 0 0 1 0 1.4l-8 5.6c-.6.4-1.3 0-1.3-.7Z"/></svg></button>`;
}
let playerVersion=0,playerSession=null,playerEngine=null,playerRoute=null,hlsLibrary=null,playerRetryTimer=null,playerReturnFocus=null;
function loadHls(){
 if(window.Hls)return Promise.resolve(window.Hls);
 if(!hlsLibrary)hlsLibrary=new Promise((resolve,reject)=>{
   const script=document.createElement('script');script.src='/hls.min.js';
   script.onload=()=>resolve(window.Hls);
   script.onerror=()=>{script.remove();hlsLibrary=null;reject(Error('播放器组件加载失败，请重试'));};
   document.head.append(script);
 });
 return hlsLibrary;
}
function releasePlayer(){
 playerVersion++;
 clearTimeout(playerRetryTimer);playerRetryTimer=null;
 if(playerEngine){playerEngine.destroy();playerEngine=null;}
 const video=$('previewVideo');
 video.onplaying=video.onwaiting=video.onerror=null;
 video.pause();video.removeAttribute('src');video.load();
 if(playerSession){api('close-play',{session:playerSession}).catch(()=>{});playerSession=null;}
}
async function openPlayer(name,url,attempt=0){
 releasePlayer();
 const version=playerVersion,c=state.channels.find(c=>c.name===name),r=c?.lines.find(r=>r.url===url);
 if(!r)return;
 playerRoute={name,url};
 closePopup();
 $('playerTitle').textContent=c.label;
 $('playerSource').textContent='线路'+(c.lines.indexOf(r)+1)+' · '+(r.platform||'来源未识别')+' · '+sourceNames(r)+(r.enabled?'':' · 已停用，仅试播');
 $('playerTitle').title=c.label+' · '+$('playerSource').textContent;
 $('playerLine').innerHTML=c.lines.map((line,i)=>`<option value="${esc(line.url)}">线路${i+1} · ${esc(line.platform||'来源未识别')} · ${line.enabled?'已启用':'已停用'} · ${esc(line.speed_display||'未测')}</option>`).join('');
 $('playerLine').value=r.url;
 $('playerLineControl').hidden=c.lines.length<2;
 $('playerStatus').textContent=attempt?'正在重新连接原始入口（'+attempt+'/2）…':'正在连接此线路…';$('retryPlayer').hidden=true;
 if(!$('playerDialog').open)$('playerDialog').showModal();
 const video=$('previewVideo');
 let failureHandled=false;
 function retryInfo(value){
   const raw=value?.upstream||value||{};
   const message=typeof raw==='string'?raw:raw.error||raw.message||'';
   const httpStatus=Number(raw.httpStatus)||Number(message.match(/HTTP\s*(\d{3})/)?.[1])||0;
   return {error:message,httpStatus,retryable:raw.retryable,stage:raw.stage,retryAfter:Number(raw.retryAfter)||0};
 }
 let recentFailure=null;
 function recoveryWatch(info){
   recentFailure=info;
   if(playerRetryTimer)return;
   const at=video.currentTime||0;
   playerRetryTimer=setTimeout(()=>{
     playerRetryTimer=null;
     if(version!==playerVersion||failureHandled)return;
     if(!video.paused&&video.readyState>=3&&video.currentTime>at+.2){recentFailure=null;return;}
     failed(info.error||'播放暂时中断',info,true);
   },3000);
 }
 function failed(message,metadata={},networkFailure=false){
   if(version!==playerVersion||failureHandled)return;
   failureHandled=true;
   clearTimeout(playerRetryTimer);playerRetryTimer=null;
   if(playerEngine){playerEngine.destroy();playerEngine=null;}
   video.onplaying=video.onwaiting=video.onerror=null;
   video.pause();video.removeAttribute('src');video.load();
   if(playerSession){api('close-play',{session:playerSession}).catch(()=>{});playerSession=null;}
   const info=retryInfo({...metadata,error:message}),code=info.httpStatus;
   const retryable=code!==401&&info.retryable!==false&&(info.retryable===true||[408,425,429,500,502,503,504,607].includes(code)||(code===403&&info.stage==='media')||(networkFailure&&(!code||(code>=200&&code<300))));
   const seconds=Math.max(info.retryAfter,code===429?6:code===607?12:8);
   if(attempt<2&&retryable&&seconds<=60){
     $('playerStatus').textContent=message+'；等待 '+Math.ceil(seconds)+' 秒后重新打开原始入口（'+(attempt+1)+'/2）…';
     playerRetryTimer=setTimeout(()=>{playerRetryTimer=null;if(version===playerVersion)openPlayer(name,url,attempt+1);},seconds*1000);
     return;
   }
   $('playerStatus').textContent=message+(retryable?(seconds>60?'；上游冷却较长，请稍后手动重试':'；已达到本次自动恢复上限，请稍后重试'):'');$('retryPlayer').hidden=false;
 }
 try{
   const session=await api('play',{name,url});
   if(version!==playerVersion){api('close-play',{session:session.session}).catch(()=>{});return;}
   playerSession=session.session;
   const start=()=>video.play().catch(error=>{
     if(version===playerVersion&&error.name==='NotAllowedError')$('playerStatus').textContent='已就绪，点击画面中的播放按钮开始';
   });
   video.onplaying=()=>{clearTimeout(playerRetryTimer);playerRetryTimer=null;recentFailure=null;$('playerStatus').textContent='正在播放';};
   video.onwaiting=()=>{$('playerStatus').textContent='正在缓冲…';if(recentFailure)recoveryWatch(recentFailure);};
   video.onerror=async()=>{
     let info={};
     try{info=await api('play-status?session='+encodeURIComponent(session.session));}catch{}
     if(version!==playerVersion)return;
     failed(info.error||'浏览器无法解码此线路的音视频格式，可更换线路或使用电视播放器',info,video.error?.code===2);
   };
   if(session.kind==='native'){
     video.src=session.url;start();
   }else{
     const Hls=await loadHls();
     if(version!==playerVersion)return;
     if(!Hls.isSupported()){
       if(video.canPlayType('application/vnd.apple.mpegurl')){video.src=session.url;start();return;}
       throw Error('当前浏览器不支持此直播格式，请使用支持 HLS 的浏览器');
     }
     const engine=new Hls({backBufferLength:30,maxBufferLength:20,maxMaxBufferLength:40});
     playerEngine=engine;
     engine.on(Hls.Events.MANIFEST_PARSED,start);
     engine.on(Hls.Events.ERROR,async(_,data)=>{
       if(version!==playerVersion||failureHandled)return;
       const code=data.response?.code;
       let info={},reason=typeof data.response?.data==='string'?data.response.data:'';
       if(code>=400){
         try{info=await api('play-status?session='+encodeURIComponent(session.session));}catch{}
         if(!info.error){try{info=JSON.parse(reason);}catch{}}
       }
       if(version!==playerVersion||failureHandled)return;
       info=retryInfo({...info,error:info.error||(code?'播放请求失败，HTTP '+code:'播放连接暂时中断'),httpStatus:info.httpStatus||code,stage:info.stage||(data.frag?'media':'manifest')});
       if(info.httpStatus===401||(info.httpStatus===403&&info.stage!=='media'&&info.retryable===false)){failed(info.error,info);return;}
       if(!data.fatal){
         if(info.retryAfter>0||[429,607].includes(info.httpStatus)){failed(info.error,info,true);return;}
         if(data.type===Hls.ErrorTypes.NETWORK_ERROR||code>=400)recoveryWatch(info);
         return;
       }
       failed(info.error,info,data.type===Hls.ErrorTypes.NETWORK_ERROR);
     });
     engine.loadSource(session.url);engine.attachMedia(video);
   }
 }catch(error){failed(error.message,error.upstream||{});}
}
$('closePlayer').onclick=()=>{releasePlayer();$('playerDialog').close();};
$('playerDialog').addEventListener('close',()=>{
 releasePlayer();
 // Explicitly restore the trigger, not the surrounding scrollable table (Safari).
 const original=playerReturnFocus;
 const target=original?.isConnected?original:[...document.querySelectorAll('button[data-play]')].find(button=>button.dataset.owner===original?.dataset.owner&&button.dataset.play===original?.dataset.play);
 if(target&&!target.disabled)target.focus({preventScroll:true});
 playerReturnFocus=null;
});
$('playerDialog').addEventListener('cancel',releasePlayer);
$('playerLine').onchange=()=>{if(playerRoute)openPlayer(playerRoute.name,$('playerLine').value);};
$('retryPlayer').onclick=()=>{if(playerRoute)openPlayer(playerRoute.name,playerRoute.url);};
window.addEventListener('pagehide',()=>{
 if(playerSession)fetch('/api/close-play',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({session:playerSession}),keepalive:true}).catch(()=>{});
 releasePlayer();
});
function formToggle(context,on,includeLabel=true){
 const button=document.createElement('button');
 button.type='button';button.name='enabled';button.dataset.formToggle=context;
 setToggle(button,on);
 return (includeLabel?esc(context)+'　':'')+button.outerHTML;
}

const collapsed=new Set();
let drill='',view='channels';
function displayMode(next){
 if(next!==view)captureDirectoryScroll();
 directorySwitching=next!==view;
 view=next;
 if(!lineView())healthFilter=null;
 picked=(healthFilter?scopedSelections:selections)[next];
 $('viewLines').setAttribute('aria-pressed',String(lineView()));
 $('viewCards').setAttribute('aria-pressed',String(!lineView()));
 if(!lineView())drill='';
 try{render();}finally{directorySwitching=false;}
}
function setHealth(filter,next=view){
 healthFilter=filter;
 for(const selection of Object.values(scopedSelections))selection.clear();
 if(filter)collapsed.delete(filter.group);
 displayMode(next);
}
function actionKeys(){
 return healthFilter?visibleKeys(filtered()).filter(key=>picked.has(key)):Array.from(picked);
}
function syncRouteToggle(box,r){
 const button=box.querySelector('[data-policy]');
 const {action,label,title}=routeControl(r);
 button.dataset.policy=action;
 setSwitch(button,r.enabled);
 button.title=title;
}
const failedLogos=new Set();
function usableLogo(c){return c?.logo_candidates?.find(url=>!failedLogos.has(url));}
function logoCell(c){
 const url=usableLogo(c);
 if(!url)return `<small class="secondary">${c.logo?'加载失败':'未匹配'}</small>`;
 return `<button type="button" class="logo-frame logo-preview-trigger" data-logo-preview data-logo-owner="${esc(c.name)}" aria-label="预览 ${esc(c.label)}台标" title="点击预览台标"><img src="${esc(url)}" data-channel-logo="${esc(c.name)}" alt="${esc(c.label)}台标" loading="lazy" decoding="async" referrerpolicy="no-referrer"></button>`;
}
document.addEventListener('error',e=>{
 const img=e.target;
 if(!img.matches?.('img[data-channel-logo]')||!img.isConnected)return;
 failedLogos.add(img.getAttribute('src'));
 const c=state.channels.find(x=>x.name===img.dataset.channelLogo),next=usableLogo(c);
 if(next){img.src=next;return;}
 const note=document.createElement('small');note.className='secondary';note.textContent='加载失败';
 if(img.closest('.station-mark')){const fallback=document.createElement('span');fallback.className='station-mark';fallback.setAttribute('aria-hidden','true');fallback.textContent=c?.label?.slice(0,2)||'台标';img.closest('.station-mark').replaceWith(fallback);}
 else img.parentElement.replaceWith(note);
},true);
function sourceNames(r){return [...new Set(r.from||[])].join('、')||'未知';}
function detailSource(r){return '线路来源：'+(r.platform||'来源未识别');}
function addressLink(c,r,i){
 return `<button type="button" class="address-link" data-address="${esc(r.url)}" data-owner="${esc(c.name)}" data-number="${i+1}" aria-expanded="false" aria-controls="infoPopup">${esc(r.url)}</button>`;
}
function guideButton(c,label){
 return `<button type="button" class="tiny-link" data-guide="${esc(c.name)}" aria-expanded="false" aria-controls="infoPopup">${label}</button>`;
}
function metricText(text,tone='value',title=''){
 return `<span class="metric-result ${tone}"${title?` title="${esc(title)}"`:''}>${esc(text)}</span>`;
}
const speedTones=new Map([
 ['通过','pass'],['未测','unmeasured'],['待复测','retest'],['未能验证','unverified'],
 ['暂时失败','temporary'],['未通过','fail']
]);
function speedStatus(assessment){
 return assessment==='保留'?'通过':speedTones.has(assessment)?assessment:'未通过';
}
function resolutionResult(test,includeSource=false){
 const size=test.resolution;
 const text=size&&!includeSource?`${size.width} × ${size.height}`:sizeText(test);
 return metricText(text);
}
function playbackLabel(t){
 const quality=t.quality==='可播但不稳'&&t.stalls>0?'易卡顿':t.quality;
 return [t.opening,quality].filter(Boolean).join(' · ');
}
function testBadge(owner,r,passLabel){
 const status=speedStatus(r.assessment),tone=speedTones.get(status);
 if(status==='未测')return metricText(status,tone);
 const label=status==='通过'?(passLabel||playbackLabel(r.test)||status):status;
 return `<button type="button" class="metric-result ${tone}" title="点击查看检测详情" data-failure="${esc(r.url)}" data-owner="${esc(owner)}" aria-expanded="false" aria-controls="infoPopup">${esc(label)}</button>`;
}
function speedResult(owner,r){
 return testBadge(owner,r,r.test.speed==null?'速率未知':r.speed_display);
}
function routeTableHead(group=''){
 const headers=[['group','分组名称'],['channel','频道名称'],['logo','台标'],['url','直播地址'],['guide','节目单'],['size','分辨率'],['speed','速率'],['control','线路状态']];
 const head=headers.map(([k,t])=>`<th scope="col" class="col-${k}">${t}</th>`).join('');
 return `<thead><tr>${head}</tr></thead>`;
}
function routeTable(rows,head){
 const body=rows.map(({c,r,i})=>{
   const key=routeKey(c.name,r.url);
   return `<tr data-selection-key="${esc(key)}" aria-label="${esc(c.label)} 线路${i+1}" class="${picked.has(key)?'marked':''}">
   <td><span class="group-tag">${esc(c.group)}</span></td><td><button class="channel-link" data-channel="${esc(c.name)}">${esc(c.label)}</button><small class="secondary route-platform" title="${esc(r.platform_host?'识别域名：'+r.platform_host:'地址尚未提供可确认平台的域名')}">${esc(r.platform||'来源未识别')}</small></td>
   <td class="logo-cell">${logoCell(c)}</td><td>${addressLink(c,r,i)}<small class="source-name">来源订阅：${esc(sourceNames(r))}</small></td><td>${guideButton(c,'查看节目单')}</td>
   <td class="metric" title="${esc(sizeText(r.test))}">${resolutionResult(r.test)}</td>
   <td class="metric" data-probe-cell="${esc(r.url)}"><span class="last-probe">${speedResult(c.name,r)}</span><span class="live-probe metric-result"></span></td><td class="control-cell"><div class="policies">${routeToggle(c,r)}<button type="button" data-test="${esc(r.url)}" ${busy?'disabled':''}>检测</button>${hideButton('route',c.name,r.url)}${playButton(c,r)}</div></td></tr>`;
 }).join('');
 return `<div class="table-scroll" role="region" aria-label="频道线路表" tabindex="0"><table>${head}<tbody>${body}</tbody></table></div>`;
}
function cardDetection(c){
 const preferred=defaultPlayRoute(c);
 const available=preferred?.enabled&&healthOf(preferred)==='pass'&&Number.isFinite(preferred.test?.speed);
 return `<div class="card-line-metric"><small>首选速率</small>${available?speedResult(c.name,preferred):metricText('暂无可用','unmeasured')}</div>`;
}
function channelCard(c){
 const preferred=defaultPlayRoute(c);
 const published=c.published??Boolean(c.enabled&&c.lines.some(r=>r.published??r.enabled));
 const publicationHint=published?'已加入播放器订阅。':!c.enabled?'没有启用的线路，播放器订阅中不可见。':'没有启用的线路，播放器订阅中不可见。';
 const logo=usableLogo(c);
 const initials=esc(c.label.match(/^[a-z]+/i)?.[0].slice(0,4)||c.label.slice(0,2));
 return `<article data-card-name="${esc(c.name)}" data-selection-key="${esc(c.name)}" data-publication="${published?'published':'unpublished'}" aria-label="${esc(c.label)}" aria-description="${esc(publicationHint)}" title="${esc(publicationHint)}" class="channel-card ${picked.has(c.name)?'marked':''}">
 <div class="card-name">
 ${logo?`<button type="button" class="station-mark logo-preview-trigger" data-logo-preview data-logo-owner="${esc(c.name)}" aria-label="预览 ${esc(c.label)}台标" title="点击预览台标"><img data-channel-logo="${esc(c.name)}" src="${esc(logo)}" loading="lazy" referrerpolicy="no-referrer" alt="${esc(c.label)}台标"></button>`:`<span class="station-mark" aria-hidden="true">${initials}</span>`}
 <div class="card-identity"><button type="button" data-channel="${esc(c.name)}" title="点击查看详情：【${esc(c.label)}】">${esc(c.label)}</button><div class="card-caption" aria-label="线路启停状态">${c.lines.map((r,i)=>{const label='线路'+(i+1)+' · '+(r.platform||'来源未识别')+' · '+(r.enabled?'已启用':'已停用');return `<span class="route-state-dot" data-enabled="${Boolean(r.enabled)}" role="img" aria-label="${esc(label)}" title="${esc(label)}"></span>`;}).join('')}</div></div>
 ${hideButton('channel',c.name,'',true)}
 </div><div class="card-data"><div class="card-quality">${cardDetection(c)}</div>${c.lines.length?playButton(c,preferred):''}</div></article>`;
}
const workspaceBar=document.querySelector('.workspace-bar');
const catalogTools=$('catalogTools'),directoryHeader=document.createElement('div'),headerBody=document.createElement('div');
directoryHeader.className='directory-header';headerBody.className='directory-header-body';
const heading=$('channels').querySelector('.page-heading'),bulk=$('channels').querySelector('.bulk');
heading.before(directoryHeader);directoryHeader.append(headerBody);headerBody.append(heading,catalogTools);$('channels').append(bulk);
const filterContext=document.createElement('span');filterContext.className='search-context';
catalogTools.querySelector('.search-box').append(filterContext);filterContext.append($('exitDrill'),$('exitHealth'));
const filterSizeObserver=new ResizeObserver(()=>filterContext.parentElement.style.setProperty('--search-context-width',(filterContext.offsetWidth?filterContext.offsetWidth+18:12)+'px'));
filterSizeObserver.observe(filterContext);
const barAnchor=document.createElement('div');barAnchor.setAttribute('aria-hidden','true');workspaceBar.before(barAnchor);
const viewScroll={channels:0,lines:0};
let headerProgress=0,headerMax=0,headerLeading=0,headerHeight=0,headerVisible=0,lastNativeY=window.scrollY,directorySwitching=false,toolsFrame=0;
let paintedHeaderShift=null,paintedHeaderVisible=null;
function paintDirectoryHeader(){
 const shift=Math.max(0,headerProgress-headerLeading);
 headerVisible=Math.max(0,headerHeight-shift);
 if(shift!==paintedHeaderShift){headerBody.style.transform=`translate3d(0,${-shift}px,0)`;paintedHeaderShift=shift;}
 if(headerVisible!==paintedHeaderVisible){$('channels').style.setProperty('--catalog-tools-height',headerVisible+'px');paintedHeaderVisible=headerVisible;}
}
function captureDirectoryScroll(){
 const y=window.scrollY,delta=y-lastNativeY;lastNativeY=y;
 if($('channels').hidden||!headerHeight||!delta)return;
 if(delta>0){const top=Math.min(delta,Math.max(0,headerMax-headerProgress));headerProgress+=top;viewScroll[view]+=delta-top;}
 else{const content=Math.min(-delta,viewScroll[view]);viewScroll[view]-=content;headerProgress=Math.max(0,headerProgress+delta+content);}
 paintDirectoryHeader();
}
function restoreDirectoryScroll(){
 if($('channels').hidden||!headerHeight)return;
 const top=headerProgress+viewScroll[view];
 if(Math.abs(window.scrollY-top)>.5)window.scrollTo({top,behavior:'instant'});
 lastNativeY=window.scrollY;
 viewScroll[view]=Math.max(0,lastNativeY-headerProgress);
 paintDirectoryHeader();
}
function compactCatalogTools(){
 if($('channels').hidden)return;
 if(!directorySwitching)captureDirectoryScroll();
 const pinned=headerMax>0&&headerProgress>=headerMax-1;
 catalogTools.classList.add('is-compact');
 const leading=Math.round(Math.max(0,barAnchor.getBoundingClientRect().top+window.scrollY)),height=headerBody.offsetHeight;
 const maximum=leading+Math.max(0,height-catalogTools.offsetHeight);
 const dimensionsChanged=headerHeight!==height||headerLeading!==leading||headerMax!==maximum;
 headerLeading=leading;headerHeight=height;headerMax=maximum;
 headerProgress=pinned?headerMax:Math.min(headerProgress,headerMax);
 directoryHeader.style.height=headerHeight+'px';
 $('channels').style.setProperty('--task-bar-height',workspaceBar.offsetHeight+'px');
 const start=$('channels').getBoundingClientRect().top+window.scrollY,padding=parseFloat(getComputedStyle($('workspace')).paddingBottom)||0;
 $('channels').style.minHeight=Math.max(0,innerHeight+headerMax-start-padding)+'px';
 if(dimensionsChanged)restoreDirectoryScroll();else paintDirectoryHeader();
}
function scheduleDirectoryScroll(){if(!toolsFrame)toolsFrame=requestAnimationFrame(()=>{toolsFrame=0;captureDirectoryScroll();});}
window.addEventListener('scroll',scheduleDirectoryScroll,{passive:true});
const catalogSizeObserver=new ResizeObserver(()=>compactCatalogTools());
catalogSizeObserver.observe(workspaceBar);catalogSizeObserver.observe(headerBody);
window.addEventListener('resize',()=>compactCatalogTools());
$('catalog').removeAttribute('tabindex');
$('catalog').setAttribute('role','region');$('catalog').setAttribute('aria-label','频道内容区域');

// Vertical pinning is native; only the two horizontal scroll positions need synchronizing.
$('catalog').addEventListener('scroll',event=>{
 const source=event.target;
 if(!source.matches?.('.table-scroll,.route-column-head'))return;
 const peer=source.closest('.catalog-group').querySelector(source.matches('.table-scroll')?'.route-column-head':'.table-scroll');
 if(peer&&peer.scrollLeft!==source.scrollLeft)peer.scrollLeft=source.scrollLeft;
}, {capture:true,passive:true});
function render(live=false){
 if(!state)return;
 if(!directorySwitching)captureDirectoryScroll();
 const oldAnchor=popupAnchor&&$('catalog').contains(popupAnchor)?popupAnchor:null;
 if(oldAnchor&&!live)closePopup();
 const list=filtered(),visible=new Set(healthFilter?visibleKeys(list):lineView()?state.channels.flatMap(c=>c.lines.map(r=>routeKey(c.name,r.url))):state.channels.map(c=>c.name));
 for(const key of picked)if(!visible.has(key))picked.delete(key);
 selectionStatus(list);
 const rows=routeRows(list);
 const matches=matchingRows(rows);
 updateProbeAction();
 $('resultCount').textContent=`${new Set(matches.map(x=>x.c.name)).size} 个频道 · ${matches.length} 条线路`+(healthFilter?` · ${healthFilter.group} / ${healthNames[healthFilter.status]}`:'');
 $('exitDrill').hidden=!drill;$('exitDrill').textContent='← 返回频道卡片 · '+drill;
 $('exitHealth').hidden=!healthFilter;
 if(healthFilter)$('exitHealth').textContent=healthFilter.group+' · '+healthNames[healthFilter.status]+' ×';
 const groups=new Map();
 for(const c of list){
   if(!groups.has(c.group))groups.set(c.group,[]);
   groups.get(c.group).push(c);
 }
 const groupOrder=directoryGroups().filter(g=>groups.has(g));
 const offsets=new Map();
 for(const table of $('catalog').querySelectorAll('.table-scroll')){
   if(table.scrollLeft)offsets.set(table.parentElement.dataset.group,table.scrollLeft);
 }
 const head=lineView()?routeTableHead():'';
 $('catalog').innerHTML=groupOrder.map(group=>{
   const channels=groups.get(group)||[];
   const groupRows=rows.filter(x=>x.c.group===group);
   const shown=matchingRows(groupRows);
   const shownChannels=[...new Set(shown.map(x=>x.c))];
   const folded=collapsed.has(group);
   const counts={pass:0,fail:0,unmeasured:0,uncertain:0};
   for(const {r} of groupRows)counts[healthOf(r)]++;
   const buttons=Object.entries(counts).filter(([key,n])=>key!=='uncertain'||n||healthFilter?.status===key).map(([key,n])=>`<button type="button" data-health="${key}" data-group="${esc(group)}" aria-pressed="${healthFilter?.group===group&&healthFilter.status===key}">${healthNames[key]} ${n}</button>`).join('');
   const statusInfo=lineView()?`<span class="group-health" title="按检测结果筛选，再次点击取消"><small class="group-status-label">检测结果</small>${buttons}</span>`:'<span class="route-state-legend" aria-label="线路状态说明" title="每个色点代表一条线路，点开频道详情可调整启停状态"><span class="group-status-label">线路状态</span><span class="legend-item"><i class="route-state-dot" data-enabled="true" aria-hidden="true"></i>已启用</span><span class="legend-item"><i class="route-state-dot" data-enabled="false" aria-hidden="true"></i>已停用</span></span>';
   return `<div class="catalog-group" data-group="${esc(group)}"><div class="sticky-group"><div class="group-head"><div class="group-identity"><button class="group-title" data-fold="${esc(group)}" aria-expanded="${!folded}"><span class="caret">${folded?'▸':'▾'}</span>${esc(group)}</button><small>${channels.length} 个频道 · ${groupRows.length} 条线路</small></div><div class="group-actions"><button class="group-focus" data-focus="${esc(group)}">${$('group').value===group?'显示全部分组':'只看此组'}</button>${hideButton('group',group)}</div>${statusInfo}</div>
   ${head&&!folded&&shown.length?`<div class="route-column-head" role="group" aria-label="${esc(group)}线路列表表头"><table>${routeTableHead(group)}</table></div>`:''}</div>
   ${folded?'':!shown.length?'<p class="empty">当前分组没有符合该状态的线路。</p>':lineView()?routeTable(shown,head):`<div class="card-grid">${shownChannels.map(channelCard).join('')}</div>`}</div>`;
 }).join('')||(state.channels.length
   ?'<div class="empty">没有匹配的频道或线路。<button type="button" id="resetSearch">清除筛选</button></div>'
   :'<div class="empty">频道库暂无可见频道。请在源管理中检查订阅，或在隐藏管理中恢复频道。</div>');
 if(offsets.size)for(const table of $('catalog').querySelectorAll('.table-scroll')){
   const left=offsets.get(table.parentElement.dataset.group);
   if(left){table.scrollLeft=left;table.previousElementSibling.querySelector('.route-column-head').scrollLeft=table.scrollLeft;}
 }
 if(oldAnchor&&live){
   const d=oldAnchor.dataset;
   const replacement=[...$('catalog').querySelectorAll('[data-failure],[data-guide],[data-address]')].find(el=>el.dataset.owner===d.owner&&el.dataset.failure===d.failure&&el.dataset.guide===d.guide&&el.dataset.address===d.address);
   if(replacement){popupAnchor=replacement;replacement.setAttribute('aria-expanded','true');positionPopup();}else closePopup();
 }
 restoreDirectoryScroll();
 syncBatchPresentation();applyHelp($('catalog'));
 paintProgress();
}
const guideCache=new Map();
async function programmeList(key){
 if(!guideCache.has(key))guideCache.set(key,api('guide?channel='+encodeURIComponent(key)));
 let rows;
 try{rows=await guideCache.get(key);}catch(err){guideCache.delete(key);throw err;}
 return rows.length?rows.map(p=>{
   const time=formatTime(p.timestamp!=null?p.timestamp:p.start);
   return `<div class="programme"><time title="${esc(p.start)}">${esc(time)}</time><b>${esc(p.title)}</b></div>`;
 }).join(''):'暂无匹配节目。可在频道详情调整节目单 ID。';
}
let popupAnchor=null;
function closePopup(focus=false){
 const anchor=popupAnchor;
 if(!anchor)return;
 popupAnchor=null;anchor.setAttribute('aria-expanded','false');
 if($('infoPopup').matches(':popover-open'))$('infoPopup').hidePopover();
 if(focus&&anchor.isConnected)anchor.focus();
}
function positionPopup(){
 if(!popupAnchor)return;
 const box=$('infoPopup'),rect=popupAnchor.getBoundingClientRect(),gap=8;
 if(!popupAnchor.isConnected||rect.bottom<=0||rect.top>=innerHeight){closePopup();return;}
 if($('catalog').contains(popupAnchor)){
   const top=workspaceBar.getBoundingClientRect().bottom+headerVisible;
   if(rect.bottom<=top||rect.top>=innerHeight){closePopup();return;}
 }
 const left=Math.max(gap,Math.min(rect.left,innerWidth-box.offsetWidth-gap));
 const below=innerHeight-rect.bottom-gap,above=rect.top-gap;
 box.style.maxHeight=Math.min(innerHeight-gap*2,Math.max(105,below-gap,above-gap))+'px';
 const top=below>=box.offsetHeight||below>=above?rect.bottom+gap:rect.top-box.offsetHeight-gap;
 box.style.left=left+'px';box.style.top=Math.max(gap,Math.min(top,innerHeight-box.offsetHeight-gap))+'px';
}
async function showPopup(button){
 if(popupAnchor===button){closePopup(true);return;}
 closePopup();
 const {address,hiddenAddress,owner,guide,number,failure}=button.dataset;
 const scope=button.closest('[data-restore-scope]');
 let hiddenLine;
 if(scope&&failure){
   const [kind,name]=JSON.parse(scope.dataset.owner);
   const rows=kind==='route'?state.hidden:kind==='group'?state.hidden_groups[name]:state.hidden_channels[name];
   hiddenLine=rows?.find(r=>r.url===failure&&r.name===owner);
 }
 const c=hiddenLine||state.channels.find(x=>x.name===(guide||owner));
 if(!c&&!hiddenAddress)return;
 const box=$('infoPopup'),body=$('popupBody');
 (button.closest('dialog[open]')||document.body).append(box);
 popupAnchor=button;button.setAttribute('aria-expanded','true');
 box.classList.toggle('address-only',!!(address||hiddenAddress));
 $('popupTitle').textContent=address||hiddenAddress?'直播地址':guide?c.label+' · 节目单':c.label+' · 线路 '+number;
 if(hiddenAddress){
   body.innerHTML=`<code>${esc(hiddenAddress)}</code>`;
 }else if(failure){
   const r=hiddenLine||c.lines?.find(line=>line.url===failure);
   if(!r){closePopup();return;}
   $('popupTitle').textContent=(c.label||c.name)+' · 检测结果';
   const cfg=r.detection?.policy||state.settings,t=r.test,notes=(t.note||[]).filter(Boolean);
   let reason=r.assessment==='保留'?'通过':r.assessment;
   if(reason==='速率未达标')reason+=`：${r.speed_display}，要求严格高于 ${cfg.min_speed} KB/s`;
   if(reason==='分辨率未达标')reason+=`：${sizeText(t)}，要求高度至少 ${cfg.min_height} 像素`;
   if(reason==='分辨率未知')reason+=`：未能识别尺寸，要求高度至少 ${cfg.min_height} 像素`;
   const result=metricText(reason,speedTones.get(speedStatus(r.assessment)));
   const details=[t.speed!=null?r.speed_display:'',playbackLabel(t),t.fallback?'已降档':'',t.httpStatus?'HTTP '+t.httpStatus:''].filter(Boolean);
   body.innerHTML=`${result}${details.length?`<p>${esc(details.join(' · '))}</p>`:''}${notes.length?`<p>${esc(diagnosticText(notes.join('；')))}</p>`:''}<small>检测时间</small><p>${esc(formatTime(t.at))}</p><small>线路地址</small><code>${esc(r.url)}</code>`;
 }else if(address){
   const line=c.lines.find(r=>r.url===address);
   if(!line){closePopup();return;}
   const attrs=line.attrs||{};
   body.innerHTML=`<code>${esc(line.url)}</code>`+
     (attrs.catchup&&attrs.catchup!=='none'?`<details class="address-catchup"><summary>回看设置</summary><small>回看模式</small><code>${esc(attrs.catchup)}</code>${attrs['catchup-source']?`<small>回看地址模板</small><code>${esc(attrs['catchup-source'])}</code>`:''}</details>`:'');
 }else body.textContent='正在读取节目单…';
 box.showPopover();positionPopup();
 if(guide){
   let html;
   try{html=await programmeList(guide);}catch(err){html='加载失败：'+esc(err.message);}
   if(popupAnchor===button){body.innerHTML=html;positionPopup();}
 }
}
document.addEventListener('pointerdown',e=>{
 if(popupAnchor&&!$('infoPopup').contains(e.target)&&!popupAnchor.contains(e.target))closePopup();
});
document.addEventListener('keydown',e=>{
 if(e.key==='Escape'&&popupAnchor){e.preventDefault();e.stopPropagation();closePopup(true);}
},true);
document.addEventListener('scroll',e=>{if(popupAnchor&&!$('infoPopup').contains(e.target))positionPopup();},true);
window.addEventListener('resize',positionPopup);
const field=(label,name,value='')=>`<label>${esc(label)}<input name="${name}" value="${esc(value)}"></label>`;
let updateTitlePreview=null;
function show(title,html,handler){$('dialog').classList.remove('source-editor');$('channelEdit').hidden=true;$('apply').hidden=false;updateTitlePreview=null;$('titleLogo').hidden=true;$('titleLogo').replaceChildren();$('title').textContent=title;$('fields').innerHTML=html;editing=handler;applyHelp($('dialog'));if(!$('dialog').open)$('dialog').showModal();$('dialog').scrollTop=0;}
function sourceIcon(kind){
 if(kind==='logo')return '<span class="source-icon logo-icon" aria-hidden="true"><svg viewBox="0 0 24 24"><rect x="3" y="3" width="18" height="18" rx="3"/><circle cx="8" cy="8" r="1.5"/><path d="m3 17 5-5 4 4 3-3 6 6"/></svg></span>';
 return `<span class="source-icon ${kind==='epg'?'guide-icon':''}" aria-hidden="true"><svg viewBox="0 0 24 24">${kind==='epg'?'<rect x="4" y="5" width="16" height="15" rx="3"/><path d="M8 3v4m8-4v4M4 10h16M8 14h3m-3 3h7"/>':'<rect x="3" y="4" width="18" height="14" rx="3"/><path d="M8 21h8m-4-3v3m-3-9 5-3-5-3z"/>'}</svg></span>`;
}
const changingSources=new Set();
const refreshingSources=new Set();
function sourceToggle(s){
 const button=document.createElement('button');
 button.type='button';button.dataset.sourceToggle=s.id;
 setSwitch(button,Boolean(s.enabled),'订阅 '+s.name);
 button.disabled=changingSources.has(s.id);
 button.title='点击'+toggleText(!s.enabled)+'订阅，立即生效。'+(s.kind==='logo'?'停用后不再更新或用于台标匹配。':'停用后不再更新或发布其内容。');
 return button.outerHTML;
}
function renderFeeds(){
 const sources=state.sources;
 $('sourceSummary').innerHTML=`<span><b>${sources.length}</b> 个订阅</span><span><i class="source-dot"></i>${sources.filter(s=>s.enabled).length} 个已启用</span><span>${sources.filter(s=>s.kind==='playlist').length} 个直播源 · ${sources.filter(s=>s.kind==='epg').length} 个节目单 · ${sources.filter(s=>s.kind==='logo').length} 个台标订阅</span>`;
 $('feeds').innerHTML=sources.map(s=>`<article class="feed">
   <div class="feed-top">${sourceIcon(s.kind)}<div class="feed-title"><span class="feed-kind">${s.kind==='epg'?'节目单 · XMLTV':s.kind==='logo'?'台标 · JSON':'直播源 · M3U / TXT'}</span><h2>${esc(s.name)}</h2></div>${sourceToggle(s)}</div>
   <div class="feed-address"><small>订阅地址</small><p class="url">${esc(s.url)}</p></div>
   <dl class="feed-facts"><div><dt>定时更新</dt><dd>${esc(sourceSchedule(s))}</dd></div><div><dt>资料优先级</dt><dd>${s.priority}</dd></div></dl>
   ${s.guide_urls?.length?`<details class="feed-guides"><summary>自带节目单 <span>${s.guide_urls.length}</span></summary>${s.guide_urls.map(url=>`<p class="url">${esc(url)}</p>`).join('')}</details>`:''}
   <div class="feed-bottom"><small><span class="feed-result-label">最近更新${s.updated?' · '+esc(formatTime(s.updated)):''}</span>${s.pending_update?'<span data-source-pending>等待更新 · 当前任务结束后执行</span>':esc(s.last||'尚未更新，保存后自动更新')}</small><div class="feed-actions"><button type="button" class="source-refresh" data-source-refresh="${s.id}" data-pending-update="${s.pending_update?1:0}" aria-label="更新 ${esc(s.name)}" title="更新 ${esc(s.name)} · 仅更新此订阅，不检测线路。" ${s.pending_update||refreshingSources.has(s.id)?'disabled':''}><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M20 7v5h-5M4 17v-5h5M6.1 7a7 7 0 0 1 11.6-1L20 9M4 15l2.3 3a7 7 0 0 0 11.6-1"/></svg></button><button type="button" data-feed="${s.id}" aria-label="编辑 ${esc(s.name)}" >编辑订阅</button></div></div>
 </article>`).join('')||'<div class="source-empty"><h2>添加你的第一个订阅</h2><p>接入直播清单、节目单或台标，统一管理订阅。</p><button type="button" id="emptyAddSource" class="primary">添加订阅</button></div>';
 if($('emptyAddSource'))$('emptyAddSource').onclick=()=>editSource();
 applyHelp($('feeds'));syncSourceActions(liveJob||{running:busy});
}
function sourceSchedule(s){
 const hours=s.interval_hours??state.settings.minutes/60;
 const plan=(s.interval_hours==null?'跟随默认 · ':'')+(hours?'每 '+Number(hours.toFixed(4))+' 小时':'定时更新已关闭');
 if(state.preview&&s.next_update)return plan+' · 预览中暂停调度';
 return plan+(s.next_update?' · 下次更新 '+formatTime(s.next_update):'');
}
function editSource(id){
 const s=state.sources.find(x=>x.id===id)||{enabled:1,priority:10,kind:'playlist'};
 const mode=s.interval_hours==null?'inherit':s.interval_hours===0?'off':'custom';
 const html=`<div class="source-intro">${sourceIcon(s.kind)}<p>连接直播、节目单或台标；保存后更新当前订阅；有任务运行时排队。</p></div>
 <section class="source-section"><h3>订阅信息</h3><div class="source-form-grid">
 <label>订阅名称<input name="name" required placeholder="例如：家庭直播源" value="${esc(s.name||'')}"></label>
 <label>内容类型<select name="kind"><option value="playlist">直播 · M3U / TXT</option><option value="epg" ${s.kind==='epg'?'selected':''}>节目单 · XMLTV / GZIP</option><option value="logo" ${s.kind==='logo'?'selected':''}>台标 · JSON</option></select></label>
 <label class="source-wide">订阅地址<input name="url" type="url" required placeholder="https://example.com/channels.m3u" value="${esc(s.url||'')}" spellcheck="false" autocomplete="off"><small id="sourceFormatHint"></small></label>
 </div></section>
 <section class="source-section"><h3>更新与优先级</h3><div class="source-form-grid">
 <label>定时更新<select name="frequency"><option value="inherit">跟随默认设置</option><option value="custom">按指定间隔更新</option><option value="off">关闭定时更新</option></select><small id="sourcePlanHint"></small></label>
 <label>资料优先级<input name="priority" type="number" step="1" required value="${esc(s.priority)}"><small>数值越小，频道资料越优先采用。</small></label>
 <label id="sourceHours" class="source-wide">更新间隔<span class="source-hours"><input name="hours" type="number" min="0.016666666666666666" max="720" step="any" required value="${esc(s.interval_hours||state.settings.minutes/60||6)}"><span>小时</span></span></label>
 </div></section>
 <footer class="source-editor-footer">${id?'<button type="button" id="deleteSource" class="source-delete">删除订阅</button>':'<span></span>'}<div><button type="button" id="cancelSource">取消</button><button type="submit" class="primary">保存订阅</button></div></footer>`;
 show(id?'编辑订阅':'添加订阅',html,async form=>{
   const f=form.elements;
   return api('source',{id,name:f.name.value,kind:f.kind.value,url:f.url.value,priority:+f.priority.value,interval_hours:f.frequency.value==='inherit'?null:f.frequency.value==='off'?0:+f.hours.value});
 });
 $('dialog').classList.add('source-editor');$('apply').hidden=true;
 $('cancelSource').onclick=()=>$('dialog').close();
 const f=$('editor').elements;
 function showSourceFormat(){
   $('fields').querySelector('.source-intro .source-icon').outerHTML=sourceIcon(f.kind.value);
   f.url.placeholder=f.kind.value==='logo'?'https://example.com/logos.json':f.kind.value==='epg'?'https://example.com/guide.xml':'https://example.com/channels.m3u';
   $('sourceFormatHint').textContent=f.kind.value==='logo'?'JSON 名称映射，例如 {"CCTV1":"https://example.com/CCTV1.png"}；缺失时使用源台标。':f.kind.value==='epg'?'填写 XMLTV 或 GZIP 节目单的完整下载地址。':'填写 M3U 或 TXT 直播清单的完整下载地址。';
 }
 f.kind.onchange=showSourceFormat;showSourceFormat();
 f.priority.title='同频道资料冲突时，优先使用数值较小的订阅。';
 f.frequency.value=mode;
 function showHours(){
   f.hours.disabled=f.frequency.value!=='custom';$('sourceHours').hidden=f.hours.disabled;
   const hours=state.settings.minutes/60;
   $('sourcePlanHint').textContent=f.frequency.value==='inherit'?(hours?'当前默认每 '+hours+' 小时更新，可在设置页调整。':'默认定时更新已关闭。'):f.frequency.value==='off'?'关闭定时更新；保存或点击刷新仍会更新。':'按下方设置的小时数自动更新。';
 }
 f.frequency.onchange=showHours;showHours();
 if(id)$('deleteSource').onclick=safely(async()=>{if(!confirm('删除这个订阅及其已保存内容？'))return;await api('delete',{id});$('dialog').close();await reload();});
}
function bindTitleLogo(key){
 const slot=$('titleLogo');
 slot.dataset.logoOwner=key;
 let lastPreview='';
 slot.hidden=false;
 function preview(){
   const c=state.channels.find(ch=>ch.name===key);
   if(!c)return;
   const custom=Boolean(state.rules[key]?.logo);
   $('logoMode').textContent=custom?'自定义台标':'自动匹配';
   $('removeLogo').hidden=!custom;
   const candidates=(c.logo_candidates||[]).filter(url=>!failedLogos.has(url));
   const signature=JSON.stringify([candidates,c.label]);
   if(signature===lastPreview)return;
   lastPreview=signature;
   slot.replaceChildren();
   $('editLogoPreview').innerHTML=logoCell(c);
   slot.disabled=!candidates.length;
   if(!candidates.length){slot.textContent='无台标';return;}
   const img=new Image();img.referrerPolicy='no-referrer';img.alt=c.label+'台标';
   let i=0;
   img.onerror=()=>{
     if(slot.firstChild!==img)return;
     if(++i<candidates.length){img.src=candidates[i];return;}
     slot.disabled=true;slot.textContent='加载失败';
   };
   slot.append(img);img.src=candidates[0];
 }
 updateTitlePreview=preview;preview();
}
function editOptions(pairs,selected,defaultLabel){
 const options=new Map(pairs);
 if(selected&&!options.has(selected))options.set(selected,selected+'（当前设置）');
 return [[ '',defaultLabel ],...options].map(([value,label])=>`<option value="${esc(value)}" ${value===(selected||'')?'selected':''}>${esc(label)}</option>`).join('');
}
function editChannel(key){
 const c=state.channels.find(x=>x.name===key), r=state.rules[key]||{};
 const routes=c.lines.map((line,i)=>`<div class="route"><div class="route-heading"><b>线路${i+1} · ${esc(line.from.join(' / '))}</b><span data-result-badge>${testBadge(c.name,line)}</span></div><p class="url">${addressLink(c,line,i)}<small class="source-name">${esc(detailSource(line))}</small></p><small data-probe-cell="${esc(line.url)}"><span class="last-probe">${detailMetrics(line)}</span><span class="live-probe metric-result"></span></small><div class="route-controls">${routeToggle(c,line)}<button type="button" data-test="${esc(line.url)}" ${busy?'disabled':''}>检测此线路</button>${hideButton('route',key,line.url,true)}${playButton(c,line)}</div></div>`).join('');
 const groups=[...new Set([...directoryGroups(),...Object.keys(state.hidden_groups||{})])].map(g=>[g,g]);
 const members=c.members.filter(m=>m!==key);
 const targets=state.channels.filter(ch=>ch.name!==key).map(ch=>[ch.name,ch.label===ch.name?ch.label:`${ch.label}（${ch.name}）`]);
 const autoGuide='自动匹配（'+(c.auto_epg||key)+'）';
 const settings=`<fieldset id="channelSettings" hidden disabled><legend>编辑频道</legend>
   <section class="edit-block"><h3>频道资料</h3><div class="settings-grid">
   <label>频道名称<input name="label" value="${esc(r.label)}" placeholder="${esc(key)}"><small>留空使用原名「${esc(key)}」。</small></label>
   <label>所属分组<select name="group">${editOptions(groups,r.group,'自动分组（'+c.auto_group+'）')}</select><small>选择后，频道和它的线路一起移入该组。</small></label></div></section>
   <section class="edit-block"><h3>频道台标 <span id="logoMode"></span></h3><div class="edit-logo-row"><div id="editLogoPreview"></div><div><div class="route-controls"><button type="button" id="chooseLogo">上传台标</button><button type="button" id="removeLogo" hidden>移除台标</button></div><small>PNG / JPEG / WebP，最大 2 MB。上传立即生效；移除后自动匹配。</small></div></div><input type="file" id="logoFile" accept="image/png,image/jpeg,image/webp" hidden><small id="logoFeedback" role="status"></small></section>
   <section class="edit-block"><h3>节目单与线路</h3><div class="settings-grid">
   <label>节目单对应频道<select name="epg">${editOptions([],r.epg,autoGuide)}</select><small id="epgHint">从已获取的节目单中选择。</small></label>
   <label>将线路合并到<select name="target" ${members.length?'disabled':''}>${editOptions(targets,r.target,'保持独立频道')}</select><small>${members.length?'先拆出下列成员，才能合并到其他频道。':'保存后，本频道的线路会出现在所选频道下。'}</small></label></div>
   ${members.length?`<div class="edit-members">已合并的频道　${members.map(m=>`<button type="button" data-unmerge="${esc(m)}">拆出 ${esc(m)}</button>`).join(' ')}</div>`:''}</section>

   <div class="detail-edit-actions"><button type="button" id="discardEdit">取消</button><button type="submit" class="primary">保存设置</button></div></fieldset>`;
 const overview=`<details class="detail-guide" open><summary>节目单</summary><div id="detailGuide" aria-live="polite">正在读取节目单…</div></details>
   <p id="detailPublication" class="detail-publication">${esc(publicationSummary(c))}</p><p id="detailDetection" class="detail-detection">${esc(routeHealthSummary(c.lines))}</p><section class="detail-routes" data-detail-owner="${esc(key)}"><h3>线路状态 <small>${c.lines.length} 条</small></h3>${routes}</section>`;
 show(c.label,settings+overview,async f=>{
   const values=Object.fromEntries(new FormData(f));
   values.epg=f.elements.epg.value;
   await api('channel',{...values,name:key,bound_epg:values.epg!==r.epg?!!values.epg:!!r.bound_epg});
   return key;
 });
 const panel=$('channelSettings'),edit=$('channelEdit'),form=$('editor');
 $('apply').hidden=true;edit.hidden=false;edit.textContent='编辑';edit.setAttribute('aria-expanded','false');
 const guideSelect=form.elements.epg,guideHint=$('epgHint');
 let guidesLoaded=false;
 async function loadGuides(){
   if(guidesLoaded||guideSelect.disabled)return;
   guideSelect.disabled=true;
   guideHint.textContent='正在读取节目单频道…';
   try{
     const entries=await api('guide-options');
     guideSelect.innerHTML=editOptions(entries.map(([id,name])=>[id,name===id?name:`${name}（${id}）`]),r.epg,autoGuide);
     guideHint.textContent=entries.length?'选择对应频道；选「自动匹配」由系统识别。':'暂无节目单频道。更新订阅后可在这里选择。';
     guidesLoaded=true;
   }catch{guideHint.textContent='节目单列表读取失败，请重新打开编辑重试。';}
   finally{guideSelect.disabled=false;}
 }
 const fileInput=$('logoFile'),choose=$('chooseLogo'),remove=$('removeLogo'),feedback=$('logoFeedback');
 async function changeLogo(file){
   choose.disabled=remove.disabled=true;
   try{
     let image=null;
     if(file){
       if(file.size>2*1024*1024)throw Error('请选择不超过 2 MB 的图片');
       image=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(reader.result.split(',')[1]);reader.onerror=()=>reject(Error('文件无法读取'));reader.readAsDataURL(file);});
     }
     feedback.textContent=file?'正在上传…':'正在恢复自动台标…';
     await api('channel-logo',{name:key,image});
     await reload(true);feedback.textContent=file?'台标已更新':'已恢复自动匹配';
   }catch(err){feedback.textContent=err.message;}
   finally{choose.disabled=remove.disabled=false;fileInput.value='';}
 }
 choose.onclick=()=>fileInput.click();remove.onclick=()=>changeLogo(null);
 fileInput.onchange=()=>{if(fileInput.files[0])changeLogo(fileInput.files[0]);};
 function expand(open){
   if(open)loadGuides();
   if(!open){
     form.reset();updateTitlePreview();
   }
   panel.disabled=!open;panel.hidden=!open;
   edit.textContent=open?'取消编辑':'编辑';edit.setAttribute('aria-expanded',String(open));
   edit.title=open?'收起设置并放弃尚未保存的修改；已上传台标仍保留。':'展开频道改名、分组、台标和节目单设置。';
   $('dialog').scrollTop=0;
   if(open)form.elements.label.focus();else edit.focus();
 }
 edit.onclick=()=>expand(panel.hidden);$('discardEdit').onclick=()=>expand(false);
 bindTitleLogo(key);paintProgress();
 const guide=$('detailGuide');
 programmeList(key).then(html=>{guide.innerHTML=html;}).catch(err=>{guide.textContent='节目单加载失败：'+err.message;});

}
function detailMetrics(r){
 const metrics=[];
 if(r.test.opening&&r.assessment!=='保留')metrics.push(esc(playbackLabel(r.test)));
 if(r.test.speed!=null)metrics.push(metricText(r.speed_display,r.assessment==='保留'?'pass':'value'));
 metrics.push('分辨率：'+resolutionResult(r.test,true));
 if(r.test.startup!=null)metrics.push(`${r.test.direct_history?'本轮总起播耗时':'首帧验证'} ${r.test.startup} 秒`);
 if(r.test.entry_retries!=null)metrics.push(`本轮入口重连 ${r.test.entry_retries} 次`);
 const history=r.test.direct_history;
 if(history?.length)metrics.push(`近 ${history.length} 次检测直连成功 ${history.reduce((n,x)=>n+x,0)}/${history.length}`);
 if(r.test.stalls!=null)metrics.push(`播放观察 ${r.test.play_seconds??r.test.seconds} 秒 · ${r.test.segments} 片 · 预计卡顿 ${r.test.stalls} 次（${r.test.stall_seconds} 秒）`);
 if(r.test.bitrate)metrics.push(`${r.test.fallback?'已降档 · ':''}所测档位 ${(r.test.bitrate/1e6).toFixed(2)} Mbps`);
 if(r.test.at)metrics.push(esc(formatTime(r.test.at)));
 metrics.push(...(r.test.note||[]).map(note=>esc(diagnosticText(note))));
 return metrics.join('　');
}
function publicationSummary(c){
 const count=c.published_routes??c.lines.filter(r=>r.published).length;
 return count?`播放器订阅可见 · 已发布 ${count} 条线路`:
   !c.enabled?'播放器订阅不可见 · 已关闭频道发布':'播放器订阅不可见 · 暂无启用的线路';
}
function refreshDetailResults(){
 if(!$('dialog').open)return;
 const list=$('fields').querySelector('.detail-routes');
 if(!list)return;
 const boxes=new Map([...list.querySelectorAll('.route')].map(box=>[box.querySelector('[data-address]').dataset.address,box]));
 const c=cardChannels.get(list.dataset.detailOwner);
 if(!c)return;
 $('detailPublication').textContent=publicationSummary(c);
 $('detailDetection').textContent=routeHealthSummary(c.lines);

 list.querySelector('h3 small').textContent=c.lines.length+' 条';
 let previous=list.querySelector('h3');
 for(const [index,r] of c.lines.entries()){
   const box=boxes.get(r.url);
   if(!box)continue;
   if(previous.nextElementSibling!==box)previous.after(box);
   previous=box;
   box.querySelector('[data-address]').dataset.number=index+1;
   box.querySelector('.source-name').textContent=detailSource(r);
   box.querySelector('.route-heading b').textContent=`线路${index+1} · ${r.from.join(' / ')}`;
   const badge=box.querySelector('[data-result-badge]');
   if(!badge.contains(popupAnchor))badge.innerHTML=testBadge(c.name,r);
   box.querySelector('.last-probe').innerHTML=detailMetrics(r);
   syncRouteToggle(box,r);
 }
}
function logoProgressText(progress){
 const mode={off:'关闭',light:'浅色',dark:'深色',transparent:'透明'}[progress.mode]||'';
 if(progress.phase==='waiting')return `台标优化等待检测完成 · ${progress.done}/${progress.total}`;
 if(progress.running)return `台标优化 · ${mode} · ${progress.done}/${progress.total}`;
 if(!progress.total)return '台标优化完成 · 暂无可处理台标';
 return `台标优化完成 · 已就绪 ${progress.ready}/${progress.total}${progress.failed?' · 未能处理 '+progress.failed:''}`;
}
function paintLogoProgress(){
 const progress=liveLogoJob,secondary=$('logoTaskProgress');
 secondary.hidden=true;secondary.textContent='';
 if(!progress)return;
 if(liveJob?.running){
   if(progress.running){secondary.textContent=logoProgressText(progress);secondary.hidden=false;}
 }else if(progress.running||(progress.phase==='complete'&&Date.now()/1000-progress.finished_at<15)){
   $('taskMessage').textContent=logoProgressText(progress);
   $('taskProgress').textContent='';
 }
}
function paintProgress(){
 const url=liveJob?.active_url;
 const channels=url?(state?.channels||[]).filter(c=>c.lines.some(r=>r.url===url)):[];
 const links=channels.map(c=>`<button type="button" class="task-channel" data-locate="${esc(c.name)}" title="定位到检测中的${lineView()?'线路':'频道卡片'}">${esc(c.label)}</button>`).join(' / ');
 const names=$('taskChannels');
 if(names.dataset.markup!==links){
   names.innerHTML=links;
   names.dataset.markup=links;
 }
 const route=channels.length===1?' · 线路 '+(channels[0].lines.findIndex(r=>r.url===url)+1):'';
 const sample=liveJob?.sample?(liveJob.sample_seconds?`${liveJob.sample_seconds} 秒 · `:'')+diagnosticText(liveJob.sample):'正在连接线路';
 if(liveJob){
   $('taskMessage').textContent=url?'正在检测 '+(channels.length?'':liveJob.active_channel||'当前线路'):
     (liveJob.running?'进行中　':'任务　')+diagnosticText(liveJob.message)+(liveJob.running&&liveJob.total?'　'+liveJob.done+'/'+liveJob.total:'');
   $('taskProgress').textContent=url?`${route} · 整批第 ${liveJob.done+1}/${liveJob.total} 条 · ${sample}`:'';
 }
 paintLogoProgress();
 for(const card of document.querySelectorAll('[data-card-name]')){
   const active=Boolean(url&&cardChannels.get(card.dataset.cardName)?.lines.some(r=>r.url===url));
   card.classList.toggle('testing',active);
   if(active)card.setAttribute('aria-label',card.dataset.cardName+'，正在检测');
   else card.setAttribute('aria-label',cardChannels.get(card.dataset.cardName)?.label||card.dataset.cardName);
 }
 for(const btn of document.querySelectorAll('[data-test]')){
   const active=Boolean(url&&url===btn.dataset.test);
   btn.disabled=active?(!liveJob.run_id||liveJob.stop_line):busy;
   btn.textContent=active?(liveJob.run_id?'跳过此线路':'检测中'):btn.closest('#dialog')?'检测此线路':'检测';
   btn.title=active?document.querySelector('.task-actions [data-stop-current]').title:'只检测这条直播地址，按当前通过条件更新结果。';
   btn.classList.toggle('cancel-test',active);
   if(active&&liveJob.run_id){btn.dataset.stopCurrent='';}else{delete btn.dataset.stopCurrent;}
 }
 for(const btn of document.querySelectorAll('[data-stop-current]')){
   btn.hidden=!url||!liveJob?.run_id;
   btn.disabled=Boolean(liveJob?.stop_line);
   btn.dataset.run=liveJob?.run_id||'';
   btn.dataset.url=url||'';
 }
 const stopAll=document.querySelector('[data-stop-all]');
 stopAll.hidden=!(liveJob?.probing&&liveJob.total>1);
 stopAll.disabled=Boolean(liveJob?.stop_queue);
 stopAll.dataset.run=liveJob?.run_id||'';

 for(const el of document.querySelectorAll('[data-probe-cell]')){
   const active=el.dataset.probeCell===liveJob?.active_url;
   el.classList.toggle('measuring',active);
   if(active)el.querySelector('.live-probe').textContent=liveJob.sample?'检测中 · '+diagnosticText(liveJob.sample):'检测中…';
 }
}
function locateProbe(name){
 const url=liveJob?.active_url;
 const channel=cardChannels.get(name);
 const line=channel?.lines.find(r=>r.url===url);
 if(!line)return;
 if($('channels').hidden)document.querySelector('[data-tab="channels"]').click();
 closePopup();
 const selector=lineView()
   ?`[data-selection-key="${CSS.escape(routeKey(name,url))}"]`:`[data-card-name="${CSS.escape(name)}"]`;
 let target=$('catalog').querySelector(selector);
 if(!target){
   collapsed.delete(channel.group);
   if(drill!==name)drill='';
   if($('group').value&&$('group').value!==channel.group)$('group').value=channel.group;
   if(!routeRows([channel]).some(({r})=>r.url===url))$('query').value='';
   if(healthFilter&&(healthFilter.group!==channel.group||healthFilter.status!==healthOf(line)))setHealth(null);
   else render();
   target=$('catalog').querySelector(selector);
 }
 const entry=target?.closest('tr,article')?.querySelector('[data-channel]');
 if(entry){
   entry.focus({preventScroll:true});
   const top=workspaceBar.offsetHeight+headerVisible,rect=entry.getBoundingClientRect();
   viewScroll[view]=Math.max(0,window.scrollY+rect.top-top-(innerHeight-top)/2+rect.height/2-headerProgress);
   restoreDirectoryScroll();
 }
}
function syncSourceActions(job){
 $('newSource').disabled=false;
 for(const button of document.querySelectorAll('[data-feed]'))button.disabled=false;
 for(const button of document.querySelectorAll('[data-source-refresh]')){
   const id=+button.dataset.sourceRefresh;
   const active=button.dataset.pendingUpdate!=='1'&&busy&&!job.probing&&['refresh','scheduled'].includes(job.action)&&(job.source_id===id||job.active_source_id===id);
   button.disabled=active||refreshingSources.has(id)||(job.queued_sources||[]).includes(id);
   button.classList.toggle('is-updating',Boolean(active));button.setAttribute('aria-busy',String(Boolean(active)));
 }
}
async function status(){
 const {job,logs,log_id,logo_beauty,logo_optimization,logo_cache_revision}=await api('status?compact=1');liveJob=job;liveLogoJob=logo_optimization||null;
 legacyLogs=log_id===undefined?(logs||[]):null;
 $('logKind').disabled=Boolean(legacyLogs);
 if(legacyLogs)$('logKind').value='history';
 latestLogVersion=JSON.stringify([log_id,job.log_epoch]);
 if($('logDialog').open&&logVersion!==latestLogVersion)await loadLogs(latestLogVersion);
 busy=job.running;$('sync').disabled=busy;updateProbeAction();
 syncSourceActions(job);
 for(const button of document.querySelectorAll('[data-test]'))button.disabled=busy;
 for(const button of document.querySelectorAll('[data-source-toggle]'))button.disabled=changingSources.has(+button.dataset.sourceToggle);
 $('probePicked').disabled=busy||!picked.size;
 if(wasRunning&&!job.running){await reload(job.action==='probe');}
 else if(revision!==job.revision){await reload(job.running||job.action==='probe'||revision===null);}
 else if(logo_beauty!==undefined&&state?.settings.logo_beauty!==logo_beauty){await reload(true);}
 else if(logo_cache_revision!==undefined&&state?.logo_cache_revision!==logo_cache_revision){await reload(true);}
 revision=job.revision;wasRunning=job.running;paintProgress();
}
async function launch(action,url,urls){await api('job',{action,url,urls});wasRunning=true;await status();}
document.addEventListener('click',safely(async e=>{
 const logoTrigger=e.target.closest('[data-logo-preview]');
 if(logoTrigger){e.preventDefault();openLogoPreview(logoTrigger);return;}
 const batchItem=e.target.closest('[data-selection-key]');
 if(batchItem&&$('channels').classList.contains('batch-open')){e.preventDefault();toggleBatchItem(batchItem);return;}
 const d=e.target.dataset;
 const refreshButton=e.target.closest('[data-source-refresh]');
 if(refreshButton){
   const id=+refreshButton.dataset.sourceRefresh;
   if(refreshingSources.has(id))return;
   refreshingSources.add(id);refreshButton.disabled=true;
   try{const saved=await api('job',{action:'refresh',source_id:id});wasRunning=true;await reload();await status();if(saved.refresh_queued)notice('已排队，当前任务结束后更新此订阅');}
   finally{refreshingSources.delete(id);await status();}
   return;
 }
 if(d.sourceToggle){
   const id=+d.sourceToggle,s=state.sources.find(s=>s.id===id);
   if(!s||changingSources.has(id))return;
   const before=Boolean(s.enabled),button=e.target;
   changingSources.add(id);button.disabled=true;setSwitch(button,!before,'订阅 '+s.name);
   let saved=false;
   try{
     await api('source-state',{id,enabled:!before});saved=true;await reload();
     notice('订阅已'+toggleText(!before));
   }catch(error){if(!saved)setSwitch(button,before,'订阅 '+s.name);throw error;}
   finally{
     changingSources.delete(id);
     for(const b of document.querySelectorAll('[data-source-toggle]'))b.disabled=changingSources.has(+b.dataset.sourceToggle);
   }
   return;
 }
 const play=e.target.closest('button[data-play]');
 if(play){playerReturnFocus=play;await openPlayer(play.dataset.owner,play.dataset.play);return;}
 if(d.locate){locateProbe(d.locate);return;}
 if(e.target.id==='resetSearch'){$('query').value='';$('group').value='';drill='';setHealth(null);return;}
 const tab=e.target.closest('button[data-tab]');
 if(tab){
   closePopup();captureDirectoryScroll();
   for(const b of document.querySelectorAll('button[data-tab]')){
     const current=b===tab;
     b.classList.toggle('active',current);$(b.dataset.tab).hidden=!current;
     if(current)b.setAttribute('aria-current','page');else b.removeAttribute('aria-current');
   }
   $('pageLabel').textContent=tab.textContent.trim();
   if(tab.dataset.tab==='channels'){
     directorySwitching=true;
     try{compactCatalogTools();restoreDirectoryScroll();}finally{directorySwitching=false;}
   }else{window.scrollTo(0,0);lastNativeY=0;}
 }
 if(d.formToggle)setToggle(e.target,e.target.value!=='1');
 if(d.bulk&&!changingBulk){
   const keys=actionKeys();if(!keys.length)return;
   const routes=lineView(),mode=d.bulk;
   changingBulk=true;selectionStatus(filtered());
   try{
     if(routes){
       const targets=keys.map(key=>{const [name,url]=JSON.parse(key);return {name,url};});
       if(mode==='hide')await api('hide-routes',{targets});
       else await api('routes',{targets,mode});
     }else if(mode==='hide')await api('hide-channels',{names:keys});
     else await api('channels',{names:keys,enabled:mode==='on',routes_mode:mode});
     await reload();notice('已'+(mode==='hide'?'隐藏':toggleText(mode==='on'))+' '+keys.length+(routes?' 条线路':mode==='hide'?' 个频道':' 个频道内的全部可见线路')+(mode!=='hide'?'，已保留手动启停':''));
   }finally{changingBulk=false;selectionStatus(filtered());}
 }
 if(d.hide||d.restore){
   const button=e.target;button.disabled=true;
   try{
     const [kind,name,url,parent]=JSON.parse(d.hide||d.restore);
     await api('visibility',{kind,name,url,...parent,hide:Boolean(d.hide)});
     if(d.hide&&$('dialog').open)$('dialog').close();
     await reload();notice(d.hide?'已隐藏，可在“隐藏管理”中恢复':parent?'已恢复此线路的隐藏设置':'已恢复显示规则');
   }finally{button.disabled=false;}
 }
 if(d.policy){
   const btn=e.target,mode=d.policy,previous=btn.getAttribute('aria-checked')==='true';
   btn.disabled=true;if(mode!=='auto')setSwitch(btn,mode==='on');
   let saved=false;
   try {
     await api('route',{name:d.owner,url:d.url,mode});saved=true;await reload();
     notice(mode==='auto'?'线路已恢复自动控制':'线路已手动'+toggleText(mode==='on')+'，下次有效首测按结果更新启停，异常复测保持');
   }catch(error){
     if(!saved&&mode!=='auto')setSwitch(btn,previous);
     throw error;
   }finally{btn.disabled=false;}
 }
 if(d.fold){if(collapsed.has(d.fold))collapsed.delete(d.fold);else collapsed.add(d.fold);render();}
 if(d.health){
   const cancel=healthFilter?.group===d.group&&healthFilter.status===d.health;
   setHealth(cancel?null:{group:d.group,status:d.health},cancel?view:'lines');
 }
 if(d.focus){$('group').value=$('group').value===d.focus?'':d.focus;setHealth(null);}
 if(d.drill){
   drill=d.drill;collapsed.delete(state.channels.find(c=>c.name===drill).group);displayMode('lines');
   $('exitDrill').focus();
 }
 if(d.guide||d.address||d.hiddenAddress||d.failure)await showPopup(e.target);
 if(d.channel)editChannel(d.channel);
 if(d.feed)editSource(+d.feed);
 if('stopCurrent' in d || 'stopAll' in d){
   const all='stopAll' in d;
   if(all&&!confirm('终止整批检测？已完成的结果会保留，剩余线路将不再检测。'))return;
   await api('stop-probe',{run_id:d.run,...(all?{}:{url:d.url})});
   await status();return;
 }
 if(d.test)await launch('probe',d.test);
 if(d.unmerge){await api('channel',{...state.rules[d.unmerge],name:d.unmerge,target:''});await reload();$('dialog').close();}
}));
function filterChanged(){bulkSelectStep=0;picked.clear();render();}
function toggleBulkMode(){
 const on=$('channels').classList.toggle('batch-open');
 $('bulkMode').setAttribute('aria-pressed',String(on));
 $('bulkMode').textContent=on?'退出批量管理':'批量管理';
 $('bulkMode').title=on?'点击卡片或线路选中，再次点击取消；退出时清空选择。':'进入批量模式，点击卡片或线路进行选择。';
 bulkSelectStep=0;
 if(!on){for(const sets of [selections,scopedSelections])for(const keys of Object.values(sets))keys.clear();}
 render();
 if(on)$('selectVisible').focus({preventScroll:true});
}
$('selectVisible').onclick=()=>{
 bulkSelectStep=(bulkSelectStep+1)%3;
 bulkSelectGroup=bulkSelectStep===2?currentCatalogGroup():'';
 const keys=bulkSelectStep===1?visibleKeys(filtered()):bulkSelectStep===2?groupSelectionKeys(bulkSelectGroup):[];
 picked.clear();for(const key of keys)picked.add(key);
 selectionStatus(filtered());
};
document.addEventListener('keydown',e=>{
 if(!['Enter',' '].includes(e.key)||!$('channels').classList.contains('batch-open'))return;
 const item=e.target.closest('[data-selection-key]');
 if(item){e.preventDefault();toggleBatchItem(item);}
});
$('bulkMode').onclick=toggleBulkMode;
$('bulkDone').onclick=()=>{toggleBulkMode();$('bulkMode').focus({preventScroll:true});};
function switchDirectoryView(next){
 if(next===view)return;
 displayMode(next);bulkSelectStep=0;
}
$('viewLines').onclick=()=>switchDirectoryView('lines');$('viewCards').onclick=()=>switchDirectoryView('channels');
applyHelp();
$('closePopup').onclick=()=>closePopup(true);$('dialog').addEventListener('close',()=>{closePopup();document.body.append($('infoPopup'));});$('exitDrill').onclick=()=>{
 const name=drill;displayMode('channels');
 [...$('catalog').querySelectorAll('[data-drill]')].find(button=>button.dataset.drill===name)?.focus();
};$('clearPicks').onclick=filterChanged;
$('query').oninput=()=>{bulkSelectStep=0;render();};$('group').onchange=()=>{bulkSelectStep=0;drill='';setHealth(null);};
$('exitHealth').onclick=()=>setHealth(null);
$('sync').onclick=safely(()=>launch('refresh'));
$('probe').onclick=safely(()=>{
 if(healthFilter){
   const urls=matchingRows(routeRows(filtered())).map(({r})=>r.url);
   if(urls.length&&confirm(`仅检测「${healthFilter.group}」当前筛出的 ${urls.length} 条${healthNames[healthFilter.status]}线路？`))return launch('probe',undefined,urls);
 }else if(confirm('检测已启用订阅中的全部未隐藏线路，包括未测和不达标线路；有效首测按检测结果更新启停。开始？'))return launch('probe');
});
$('probePicked').onclick=safely(()=>{const keys=actionKeys();if(lineView()&&keys.length)return launch('probe',undefined,keys.map(key=>JSON.parse(key)[1]));});
let latestLogVersion=null,logVersion=null,logRequest=0,legacyLogs=null,clearingLogs=false;
async function loadLogs(version){
 const kind=$('logKind').value,request=++logRequest;
 const rows=legacyLogs||await api('logs?kind='+kind);
 if(request!==logRequest)return;
 $('clearLogs').disabled=Boolean(legacyLogs)||clearingLogs||!rows.length;
 const open=new Set([...$('logs').querySelectorAll('details[open]')].map(e=>e.dataset.id));
 $('logs').innerHTML=rows.map(row=>{
   const time=esc(formatTime(row.ts));
   const failed=['失败','部分失败','未通过','暂时失败','未能验证','待复测'].includes(row.result);
   const reason=failed?`<details data-id="${row.id}" ${open.has(String(row.id))?'open':''}><summary>查看失败原因</summary><p>${esc(diagnosticText(row.reason||row.message))}</p></details>`:'';
   return `<article class="log-entry"><header><b>${esc(row.source||'历史任务')}${row.channel?' · '+esc(row.channel):''}</b><time>${time}</time></header>
    <div>${speedTones.has(row.result)?metricText(row.result,speedTones.get(row.result)):`<strong class="${failed?'bad':['已跳过','已终止'].includes(row.result)?'':'good'}">${esc(diagnosticText(row.result))}</strong>`} ${esc(diagnosticText(row.message))}</div>
    ${row.url?`<small class="url">${esc(row.url)}</small>`:''}${reason}</article>`;
 }).join('')||'<p>暂无此类记录</p>';
 logVersion=version;
}
$('openLogs').onclick=safely(async()=>{
 $('logDialog').showModal();
 await loadLogs(latestLogVersion);
});
$('logKind').onchange=safely(async()=>{
 $('clearLogs').disabled=true;
 $('logs').textContent='正在加载任务记录…';
 await loadLogs(latestLogVersion);
});
$('clearLogs').onclick=safely(async()=>{
 const kind=$('logKind').value,label=$('logKind').selectedOptions[0].textContent;
 if(clearingLogs||!confirm(`清除「${label}」的全部记录？此操作无法撤销。`))return;
 clearingLogs=true;$('clearLogs').disabled=true;
 try{
   await api('clear-logs',{kind});
 }finally{
   clearingLogs=false;
   await loadLogs(latestLogVersion);
 }
});
$('hiddenDialog').addEventListener('close',()=>{
 if($('hiddenDialog').contains($('infoPopup'))){closePopup();document.body.append($('infoPopup'));}
 $('hiddenList').replaceChildren();$('hiddenFeedback').textContent='';
});
$('hiddenManager').onclick=()=>{renderHidden();$('hiddenDialog').showModal();};
$('closeHidden').onclick=()=>$('hiddenDialog').close();
$('newSource').onclick=()=>editSource();$('close').onclick=()=>$('dialog').close();
$('editor').onsubmit=safely(async e=>{e.preventDefault();
 if(e.currentTarget.dataset.saving==='true')return;
 e.currentTarget.dataset.saving='true';
 try{
 if(!$('channelEdit').hidden&&$('channelSettings')?.disabled)return;
 const form=e.target,fields=$('fields').firstElementChild;
 const saved=await editing(form);
 if(saved?.refresh_started||saved?.refresh_queued){wasRunning=true;await status();}
 await reload();
 if($('dialog').open&&$('fields').firstElementChild===fields){
   if(typeof saved==='string'){
     const channel=state.channels.find(c=>c.name===saved||c.members.includes(saved));
     if(channel)editChannel(channel.name);else $('dialog').close();
   }else $('dialog').close();
 }
 notice(saved?.refresh_queued?'已保存，当前任务结束后更新此订阅':saved?.refresh_started?'已保存，已发起该订阅更新':'已保存');
 }finally{e.target.dataset.saving='false';}
});
let settingsDirty=false;
function probeModeChoice(cfg){
 return cfg.probe_mode==='pipeline'?'pipeline_dual':cfg.probe_mode==='continuous'?'continuous':'adaptive';
}
const OPTION_HELP={
 logo_beauty:{off:'保留原图样式，保存即生效。',light:'透明图案叠浅底；近白台标保留对比描边，保存即处理。',dark:'统一深色背景、留白与视觉大小，保存即处理。',transparent:'透明画布，统一留白与视觉大小；原图自带底色保留，保存即处理。'},
 probe_mode:{
  adaptive:'效率优先：先快检，异常或曾不稳定的线路再连续检测；持续稳定性观察较少。',
  pipeline_dual:'兼顾效率与验证：粗筛后精测，最多并行两条；同网关串行，不达标线路单独复核。',
  continuous:'质量优先：每条线路直接连续检测，验证解码、直播推进与卡顿，通常耗时更长。'
 },
 criteria:{
  speed:'速率须高于下方门槛，不限制画面高度。',
  resolution:'画面高度须达到下方门槛，不限制速率。',
  both:'速率须高于门槛，画面高度须达到门槛；两项缺一不可。'
 },
 mode:{merge:'发布该频道所有已启用线路。',best:'仅发布排序第一的已启用线路，优先达标、稳定、高清与高速。'}
};
function updateCriteriaFields(){
 const f=$('schedule').elements;
 for(const [field,id] of [['probe_mode','probeModeHint'],['criteria','probeCriteriaHint'],['mode','publishModeHint'],['logo_beauty','logoBeautyHint']]){
   const select=f[field];
   $(id).textContent=OPTION_HELP[field][select.value]||'';
   if(field==='probe_mode'&&state&&select.value===probeModeChoice(state.settings)){
     if(state.settings.probe_mode==='legacy')$(id).textContent+=' 保存后替换旧末片检测。';
     if(state.settings.probe_mode==='pipeline'&&state.settings.probe_workers!==2)$(id).textContent+=' 保存后启用双线路调度。';
   }
   for(const option of select.options)option.title=OPTION_HELP[field][option.value]||'';
 }
 for(const [row,input,unused] of [[$('speedRule'),f.min_speed,f.criteria.value==='resolution'],[$('heightRule'),f.min_height,f.criteria.value==='speed']]){
   if(unused&&!input.validity.valid)input.value=state.settings[input.name];
   row.hidden=unused;
   input.disabled=unused;
 }
}
$('schedule').oninput=()=>{
 settingsDirty=true;
 $('saveStatus').textContent='有未保存的更改';
 updateCriteriaFields();
};
$('schedule').onsubmit=safely(async e=>{
 e.preventDefault();
 const form=e.target,f=form.elements,button=form.querySelector('[type=submit],.primary');
 if(button.disabled)return;
 button.disabled=true;
 try{
   const fields={probe_mode:f.probe_mode.value,hours:+f.hours.value,criteria:f.criteria.value,min_speed:+f.min_speed.value,min_height:+f.min_height.value,mode:f.mode.value,logo_beauty:f.logo_beauty.value,strict:f.strict.checked,auto_probe:f.auto_probe.checked};
   settingsDirty=false;
   try{await api('settings',fields);}catch(error){settingsDirty=true;throw error;}
   await reload();
   if(!settingsDirty)$('saveStatus').textContent=state.settings.logo_beauty==='off'?'设置已保存 · 保留原图样式':'设置已保存 · 台标样式已生效';
 }finally{button.disabled=false;}
});
function setupSubscriptionShare(){
 const button=$('copySubscription');
 const url=new URL(button.dataset.path,location.href).href;
 button.title='复制 M3U 订阅地址：'+url;
 button.onclick=safely(async()=>{
  let copied=false;
  try{await navigator.clipboard.writeText(url);copied=true;}catch{}
  if(!copied){
   const field=document.createElement('textarea');field.value=url;field.readOnly=true;
   field.style.cssText='position:fixed;left:-9999px;top:0;font-size:16px';
   document.body.append(field);
   try{field.select();field.setSelectionRange(0,url.length);copied=document.execCommand('copy');}
   catch{}finally{field.remove();button.focus({preventScroll:true});}
  }
  notice(copied?'订阅地址已复制':'复制未完成，可在设置与订阅中长按 M3U 清单复制链接');
 });
}
setupSubscriptionShare();
document.querySelector('[data-tab="channels"]').classList.add('active');
async function poll(){
 try{await status();}catch(e){notice(e.message);}
 finally{setTimeout(poll,busy||liveLogoJob?.running?800:2000);}
}
document.addEventListener('visibilitychange',safely(async()=>{
 if(state&&!document.hidden)await reload(true);
}));
safely(async()=>{await reload();await poll();})();

function logoPreviewSize(width,height){
 const longest=Math.max(width,height);
 const scale=Math.min(longest<96?Math.min(2,96/longest):1,300/width,220/height);
 return {width:Math.round(width*scale),height:Math.round(height*scale)};
}
function setLogoPreviewImage(url,label){
 const preview=$('logoPreviewImage');
 preview.hidden=true;preview.removeAttribute('src');
 $('logoPreviewStatus').textContent='正在加载台标…';
 preview.onload=()=>{
   const size=logoPreviewSize(preview.naturalWidth,preview.naturalHeight);
   preview.style.width=size.width+'px';
   $('logoPreviewDialog').style.width=Math.max(260,size.width+58)+'px';
   preview.hidden=false;$('logoPreviewStatus').textContent='';
 };
 preview.onerror=()=>{$('logoPreviewStatus').textContent='台标暂时无法加载，请关闭后重试。';};
 preview.referrerPolicy='no-referrer';preview.alt=label;
 $('logoPreviewTitle').textContent=label+' · 预览';
 preview.src=url;
}
function syncLogoPreview(){
 const dialog=$('logoPreviewDialog');
 if(!dialog.open)return;
 const channel=state.channels.find(c=>c.name===dialog.dataset.owner),url=usableLogo(channel);
 if(!url){dialog.close();return;}
 if($('logoPreviewImage').getAttribute('src')!==url)setLogoPreviewImage(url,channel.label+'台标');
}
function openLogoPreview(trigger){
 const img=trigger.querySelector('img');
 if(!img?.getAttribute('src'))return;
 $('logoPreviewDialog').dataset.owner=trigger.dataset.logoOwner;
 setLogoPreviewImage(img.getAttribute('src'),img.alt||'频道台标');
 $('logoPreviewDialog').showModal();
}
$('closeLogoPreview').onclick=()=>$('logoPreviewDialog').close();
$('logoPreviewDialog').addEventListener('click',e=>{
 if(e.target!==e.currentTarget)return;
 const r=e.currentTarget.getBoundingClientRect();
 if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)e.currentTarget.close();
});
$('logoPreviewDialog').addEventListener('close',()=>{const image=$('logoPreviewImage');image.onload=null;image.onerror=null;image.removeAttribute('src');$('logoPreviewStatus').textContent='';delete $('logoPreviewDialog').dataset.owner;});
