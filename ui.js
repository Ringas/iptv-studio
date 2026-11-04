const $=id=>document.getElementById(id);
const esc=x=>String(x??'').replace(/[<>&"']/g,v=>({'<':'&lt;','>':'&gt;','&':'&amp;','"':'&quot;',"'":'&#39;'}[v]));
let state, editing, wasRunning=false, busy=false, noticeTimer;
function notice(text){
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
 if(!r.ok)throw Error(await r.text()); return r.json();
}
function safely(fn){return async(...args)=>{try {await fn(...args);}catch(e){notice(e.message);}};}
function applyHelp(root=document){
 const help=[
  ['#viewCards','浅绿卡片表示订阅中可见，浅暖灰表示未发布；顶部共享位置，内容分别记忆。'],
  ['#viewLines','逐条管理线路；顶部位置共享，内容恢复本视图上次的位置。'],
  ['#group','仅显示所选分组；选择全部分组恢复完整目录。'],
  ['#query','按频道名、直播平台或地址筛选。'],
  ['#clearPicks','清空已选项目，保留批量模式。'],
  ['#probeMode','智能检测先快检，异常线路间隔后复测一次；连续检测观察更久；传统测速只测末片传输。'],
  ['#defaultHours','仅影响跟随默认设置的订阅；单位小时，填 0 关闭自动更新。'],
  ['#autoProbe','定时更新成功后，只复测该直播订阅的线路。'],
  ['#strictMode','开启后按检测条件自动启停线路；每次测速完成会覆盖手动启停。'],
  ['#retryPlayer','重新连接当前线路并尝试播放。'],
  ['#logKind','切换要查看的记录类型，清除操作也只作用于该类型。'],
  ['#channelEdit','展开频道改名、分组、台标和节目单设置。'],
  ['#chooseLogo','上传 PNG、JPEG 或 WebP 台标，最大 2 MB，上传后立即生效。'],
  ['#removeLogo','移除上传的台标，恢复自动匹配。'],
  ['#fields [name=group]','将频道及其线路移到所选分组；选自动分组可恢复智能分类。'],
  ['#fields [name=epg]','选择与本频道对应的节目表；保留自动匹配可由程序识别。'],
  ['#fields [name=target]','将本频道的线路归入所选频道；选保持独立可取消合并。'],
  ['#fields [name=frequency]','跟随默认间隔、单独指定小时数，或关闭自动更新。'],
  ['#fields [name=kind]','直播订阅提供频道地址，节目单订阅提供节目与播出时间。'],
  ['#deleteSource','删除订阅及其频道快照，原订阅文件不受影响。'],
  ['[data-unmerge]','将这个成员拆回独立频道，保留它的线路。'],
  ['[data-address],[data-hidden-address]','展开完整直播地址，可选中复制。'],
  ['[data-guide]','查看此频道已匹配的节目单。'],
  ['.feed-guides summary','查看直播清单内附带的节目单地址，更新订阅时会一并获取。'],
  ['.export-list a[href="/live.m3u"],.subscribe','打开 M3U 文本；把同一地址填入播放器即可订阅。'],
  ['.export-list a[href="/live.txt"]','以 TXT 格式发布频道及多条线路。'],
  ['.export-list a[href="/live.json"]','查看结构化频道和线路数据，供其他工具读取。'],
  ['.export-list a[href="/guide.xml"]','打开 XMLTV 节目单，供播放器匹配节目。']
 ];
 for(const [selector,text] of help)for(const el of root.querySelectorAll(selector))if(!el.title)el.title=text;
 for(const button of root.querySelectorAll('[data-fold]'))button.title=button.getAttribute('aria-expanded')==='true'?'收起本组频道或线路。':'展开本组频道或线路。';
 for(const button of root.querySelectorAll('[data-focus]'))button.title=$('group').value===button.dataset.focus?'恢复显示全部分组。':'仅显示这个分组，再次点击恢复全部分组。';
}
let revision=null,liveJob=null,cardChannels=new Map();
let stateRequest=0,appliedRequest=0;
async function reload(live=false){
 const request=++stateRequest,next=await api('state');
 if(request<appliedRequest)return;
 appliedRequest=request;state=next;
 cardChannels=new Map(state.channels.map(c=>[c.name,c]));
 $('edition').textContent=state.preview?'预览模式':'本地工作区';
 $('edition').title=state.preview?'独立预览版：定时更新暂未运行，手动更新和测速仍可使用。':'当前运行的是正式工作区，定时更新按设置执行。';
 if($('dialog').open)updateTitlePreview?.();
 if(!live)guideCache.clear();
 $('stats').innerHTML=[[state.exported,'已发布频道'],[state.lines,'已发布线路'],[state.sources.filter(s=>s.kind==='playlist').length,'直播订阅'],[state.sources.filter(s=>s.kind==='epg').length,'EPG 订阅']].map(([v,k])=>`<div>${k}<strong>${v}</strong></div>`).join('');
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
   f.probe_mode.value=cfg.probe_mode||'adaptive';f.criteria.value=cfg.criteria;f.min_height.value=cfg.min_height;f.min_speed.value=cfg.min_speed;
   f.hours.value=cfg.minutes/60;f.mode.value=cfg.mode;f.strict.checked=cfg.strict;f.auto_probe.checked=cfg.auto_probe;
   updateCriteriaFields();
 }
 $('next').textContent=state.preview?'预览中暂停定时任务':cfg.next?'下次计划更新　'+new Date(cfg.next*1000).toLocaleString():'定时更新已关闭';
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
   <div class="hidden-heading"><div class="hidden-name"><input type="checkbox" data-hidden-pick="${esc(addressKey)}" aria-label="选择 ${esc(r.label||r.name)} 的线路 ${esc(r.url)}"><b>${esc(r.label||r.name)}</b></div>
   <dl class="hidden-metrics"><div><dt>分辨率</dt><dd title="${esc(sizeText(test))}">${resolutionResult(test)}</dd></div>
   <div><dt>速率</dt><dd class="metric">${speedResult(r.name,r)}</dd></div></dl></div>
   <div class="hidden-meta"><small class="hidden-source">来源订阅：${esc(sources)}</small>
   <button type="button" class="address-link hidden-url" data-hidden-address="${esc(r.url)}" aria-label="展开完整地址：${esc(r.url)}" aria-expanded="false" aria-controls="infoPopup">${esc(shortStreamAddress(r.url))}</button></div>
   </div><button type="button" title="仅恢复这条线路，沿用原有启停和测速规则。" data-restore="${esc(addressKey)}">恢复此线路</button></div>`;
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
 const toolbar='<div class="hidden-selection"><label><input type="checkbox" data-hidden-all> 全选</label><output></output><button type="button" data-hidden-clear>清空选择</button><button type="button" class="primary" title="恢复本列表中勾选的线路，沿用原有启停和测速规则。" data-hidden-submit>恢复所选线路</button></div>';
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
function healthOf(r){return r.assessment==='保留'?'pass':r.assessment==='未测'?'unmeasured':['待复测','未能验证'].includes(r.assessment)?'uncertain':'fail';}
function matchingRows(rows){return healthFilter?rows.filter(({c,r})=>c.group===healthFilter.group&&healthOf(r)===healthFilter.status):rows;}
function updateProbeAction(){
 const button=$('probe');
 button.title=healthFilter?`只测试「${healthFilter.group}」当前筛出的${healthNames[healthFilter.status]}线路。`:'逐条测试所有启用频道的线路，包括当前搜索或分组之外的线路。';
 button.textContent=healthFilter?'测试筛出线路':'测速全部线路';
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
     lineView()?`临时${toggleText(action==='on')}所选线路，各线路下次测速完成后恢复自动控制。`:`${toggleText(action==='on')}所选频道内的全部可见线路，下次测速完成后恢复自动控制。`;
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
 const source=r.mode==='auto'?'测速自动控制':'临时手动控制，下次取得有效测速结果后恢复自动';
 const publication=r.publication_reason==='channel_disabled'?'频道已关闭发布，此线路未加入订阅。':r.publication_reason==='not_selected'?'仅发布最优线路，此线路作为备用。':'';
 return {action,label,title:`线路当前已${toggleText(r.enabled)}，点击${label}此线路。${source}。${publication}`};
}
function routeToggle(c,r){
   const {action,title}=routeControl(r);
 return `<button type="button" class="state-switch" role="switch" aria-checked="${Boolean(r.enabled)}" aria-label="${esc(c.label)}线路启用状态" data-policy="${action}" data-owner="${esc(c.name)}" data-url="${esc(r.url)}" title="${esc(title)}">已${toggleText(r.enabled)}</button>`;
}
function playButton(c,r){
 return `<button type="button" class="play-route" data-play="${esc(r.url)}" data-owner="${esc(c.name)}" title="播放此线路" aria-label="播放 ${esc(c.label)} · ${esc(r.platform||'此线路')}" aria-haspopup="dialog" aria-controls="playerDialog"><svg viewBox="0 0 20 20" aria-hidden="true"><path d="M6 4.4c0-.7.7-1.1 1.3-.7l8 5.6a.9.9 0 0 1 0 1.4l-8 5.6c-.6.4-1.3 0-1.3-.7Z"/></svg></button>`;
}
let playerVersion=0,playerSession=null,playerEngine=null,playerRoute=null,hlsLibrary=null,playerRetryTimer=null;
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
 $('playerSource').textContent=(r.platform||'来源未识别')+' · '+sourceNames(r);
 $('playerStatus').textContent=attempt?'正在重新连接原始入口（'+attempt+'/2）…':'正在连接此线路…';$('retryPlayer').hidden=true;
 if(!$('playerDialog').open)$('playerDialog').showModal();
 const video=$('previewVideo');
 let failureHandled=false;
 function failed(message){
   if(version!==playerVersion||failureHandled)return;
   failureHandled=true;
   if(playerEngine){playerEngine.destroy();playerEngine=null;}
   video.onplaying=video.onwaiting=video.onerror=null;
   video.pause();video.removeAttribute('src');video.load();
   if(playerSession){api('close-play',{session:playerSession}).catch(()=>{});playerSession=null;}
   if(attempt<2&&/HTTP (607|429)/.test(message)){
     $('playerStatus').textContent=message+'，即将重新连接原始入口（'+(attempt+1)+'/2）…';
     playerRetryTimer=setTimeout(()=>{if(version===playerVersion)openPlayer(name,url,attempt+1);},message.includes('429')?6000:1000);
     return;
   }
   $('playerStatus').textContent=message;$('retryPlayer').hidden=false;
 }
 try{
   const session=await api('play',{name,url});
   if(version!==playerVersion){api('close-play',{session:session.session}).catch(()=>{});return;}
   playerSession=session.session;
   const start=()=>video.play().catch(error=>{
     if(version===playerVersion&&error.name==='NotAllowedError')$('playerStatus').textContent='已就绪，点击画面中的播放按钮开始';
   });
   video.onplaying=()=>{$('playerStatus').textContent='正在播放';};
   video.onwaiting=()=>{$('playerStatus').textContent='正在缓冲…';};
   video.onerror=async()=>{
     let reason='';
     try{reason=(await api('play-status?session='+encodeURIComponent(session.session))).error;}catch{}
     failed(reason||'浏览器无法解码此线路的音视频格式，可更换线路或使用电视播放器');
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
     engine.on(Hls.Events.ERROR,(_,data)=>{
       const code=data.response?.code;
       const reason=typeof data.response?.data==='string'&&!data.response.data.startsWith('#')?data.response.data.slice(0,180):'';
       if(code>=400){
         api('play-status?session='+encodeURIComponent(session.session))
           .then(result=>failed(result.error||reason||'播放请求失败，HTTP '+code))
           .catch(()=>failed(reason||'播放请求失败，HTTP '+code));
         return;
       }
       if(!data.fatal&&!/HTTP (607|429)/.test(reason))return;
       failed(reason||(code?'播放请求失败，HTTP '+code+'，请重试':'播放中断或浏览器不支持此编码，请重试或更换线路'));
     });
     engine.loadSource(session.url);engine.attachMedia(video);
   }
 }catch(error){failed(error.message);}
}
$('closePlayer').onclick=()=>{releasePlayer();$('playerDialog').close();};
$('playerDialog').addEventListener('close',releasePlayer);
$('playerDialog').addEventListener('cancel',releasePlayer);
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
function stateBadge(enabled,title='当前实际发布状态'){
 return `<strong class="publication-badge" data-published="${enabled}" title="${esc(title)}">已${enabled?'启用':'停用'}</strong>`;
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
 return `<span class="logo-frame"><img src="${esc(url)}" data-channel-logo="${esc(c.name)}" alt="${esc(c.label)}台标" loading="lazy" decoding="async" referrerpolicy="no-referrer"></span>`;
}
document.addEventListener('error',e=>{
 const img=e.target;
 if(!img.matches?.('img[data-channel-logo]')||!img.isConnected)return;
 failedLogos.add(img.getAttribute('src'));
 const c=state.channels.find(x=>x.name===img.dataset.channelLogo),next=usableLogo(c);
 if(next){img.src=next;return;}
 const note=document.createElement('small');note.className='secondary';note.textContent='加载失败';
 if(img.closest('.station-mark'))img.remove();
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
 return `<button type="button" class="metric-result ${tone}" title="点击查看测速详情" data-failure="${esc(r.url)}" data-owner="${esc(owner)}" aria-expanded="false" aria-controls="infoPopup">${esc(label)}</button>`;
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
   return `<tr data-selection-key="${esc(key)}" aria-label="${esc(c.label)} 线路 ${i+1}" class="${picked.has(key)?'marked':''}">
   <td><span class="group-tag">${esc(c.group)}</span></td><td><button class="channel-link" data-channel="${esc(c.name)}">${esc(c.label)}</button><small class="secondary route-platform" title="${esc(r.platform_host?'识别域名：'+r.platform_host:'地址尚未提供可确认平台的域名')}">${esc(r.platform||'来源未识别')}</small></td>
   <td class="logo-cell">${logoCell(c)}</td><td>${addressLink(c,r,i)}<small class="source-name">来源订阅：${esc(sourceNames(r))}</small></td><td>${guideButton(c,'查看节目单')}</td>
   <td class="metric" title="${esc(sizeText(r.test))}">${resolutionResult(r.test)}</td>
   <td class="metric" data-probe-cell="${esc(r.url)}"><span class="last-probe">${speedResult(c.name,r)}</span><span class="live-probe metric-result"></span></td><td class="control-cell"><div class="policies">${routeToggle(c,r)}<button type="button" data-test="${esc(r.url)}" ${busy?'disabled':''}>测试</button>${hideButton('route',c.name,r.url)}${playButton(c,r)}</div></td></tr>`;
 }).join('');
 return `<div class="table-scroll" role="region" aria-label="频道线路表" tabindex="0"><table>${head}<tbody>${body}</tbody></table></div>`;
}
function channelCard(c){
 const preferred=c.lines.find(r=>r.enabled&&r.assessment==='保留'&&r.test.speed!=null);
 const published=c.published??Boolean(c.enabled&&c.lines.some(r=>r.published??r.enabled));
 const publicationHint=published?'已加入播放器订阅。':!c.enabled?'没有启用的线路，播放器订阅中不可见。':'没有启用的线路，播放器订阅中不可见。';
 const logo=usableLogo(c);
 const initials=esc(c.label.match(/^[a-z]+/i)?.[0].slice(0,4)||c.label.slice(0,2));
 return `<article data-card-name="${esc(c.name)}" data-selection-key="${esc(c.name)}" data-publication="${published?'published':'unpublished'}" aria-label="${esc(c.label)}" aria-description="${esc(publicationHint)}" title="${esc(publicationHint)}" class="channel-card ${picked.has(c.name)?'marked':''}">
 <div class="card-name">
 <span class="station-mark" aria-hidden="true"><span>${initials}</span>${logo?`<img data-channel-logo="${esc(c.name)}" src="${esc(logo)}" loading="lazy" referrerpolicy="no-referrer" alt="">`:''}</span>
 <div class="card-identity"><button type="button" data-channel="${esc(c.name)}" title="点击查看详情：【${esc(c.label)}】">${esc(c.label)}</button><div class="card-caption" aria-label="线路启停状态">${c.lines.map((r,i)=>{const label='线路 '+(i+1)+' · '+(r.platform||'来源未识别')+' · '+(r.enabled?'已启用':'未启用');return `<span class="route-state-dot" data-enabled="${Boolean(r.enabled)}" role="img" aria-label="${esc(label)}" title="${esc(label)}"></span>`;}).join('')}</div></div>
 ${hideButton('channel',c.name,'',true)}
 </div><div class="card-data"><div class="card-quality"><small>首选速率</small>${preferred?speedResult(c.name,preferred):metricText('暂无可用测速','unmeasured')}</div>${c.lines.length?playButton(c,c.lines.find(r=>r.enabled)||c.lines[0]):''}</div></article>`;
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
   const statusInfo=lineView()?`<span class="group-health" title="按测速结果筛选，再次点击取消"><small class="group-status-label">测速结果</small>${buttons}</span>`:'<span class="route-state-legend" aria-label="线路状态说明" title="每个色点代表一条线路，点开频道详情可调整启停状态"><span class="group-status-label">线路状态</span><span class="legend-item"><i class="route-state-dot" data-enabled="true" aria-hidden="true"></i>已启用</span><span class="legend-item"><i class="route-state-dot" data-enabled="false" aria-hidden="true"></i>未启用</span></span>';
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
   const time=p.timestamp!=null?new Date(p.timestamp*1000).toLocaleString('zh-CN',{year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}):p.start;
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
   const cfg=state.settings,t=r.test,notes=(t.note||[]).filter(Boolean);
   let reason=r.assessment==='保留'?'通过':r.assessment;
   if(reason==='速率未达标')reason+=`：${r.speed_display}，要求严格高于 ${cfg.min_speed} KB/s`;
   if(reason==='分辨率未达标')reason+=`：${sizeText(t)}，要求高度至少 ${cfg.min_height} 像素`;
   if(reason==='分辨率未知')reason+=`：未能识别尺寸，要求高度至少 ${cfg.min_height} 像素`;
   const result=metricText(reason,speedTones.get(speedStatus(r.assessment)));
   const details=[t.speed!=null?r.speed_display:'',playbackLabel(t),t.fallback?'已降档':'',t.httpStatus?'HTTP '+t.httpStatus:''].filter(Boolean);
   body.innerHTML=`${result}${details.length?`<p>${esc(details.join(' · '))}</p>`:''}${notes.length?`<p>${esc(notes.join('；'))}</p>`:''}<small>测试时间</small><p>${esc(t.at||'未知')}</p><small>线路地址</small><code>${esc(r.url)}</code>`;
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
 return `<span class="source-icon ${kind==='epg'?'guide-icon':''}" aria-hidden="true"><svg viewBox="0 0 24 24">${kind==='epg'?'<rect x="4" y="5" width="16" height="15" rx="3"/><path d="M8 3v4m8-4v4M4 10h16M8 14h3m-3 3h7"/>':'<rect x="3" y="4" width="18" height="14" rx="3"/><path d="M8 21h8m-4-3v3m-3-9 5-3-5-3z"/>'}</svg></span>`;
}
const changingSources=new Set();
function sourceToggle(s){
 const button=document.createElement('button');
 button.type='button';button.dataset.sourceToggle=s.id;
 setSwitch(button,Boolean(s.enabled),'订阅 '+s.name);
 button.disabled=busy||changingSources.has(s.id);
 button.title='订阅当前已'+toggleText(Boolean(s.enabled))+'，点击'+toggleText(!s.enabled)+'，立即生效。停用后暂停更新，并从发布内容中排除此订阅。';
 return button.outerHTML;
}
function renderFeeds(){
 const sources=state.sources;
 $('sourceSummary').innerHTML=`<span><b>${sources.length}</b> 个订阅</span><span><i class="source-dot"></i>${sources.filter(s=>s.enabled).length} 个已启用</span><span>${sources.filter(s=>s.kind==='playlist').length} 个直播源 · ${sources.filter(s=>s.kind==='epg').length} 个节目单</span>`;
 $('feeds').innerHTML=sources.map(s=>`<article class="feed">
   <div class="feed-top">${sourceIcon(s.kind)}<div class="feed-title"><span class="feed-kind">${s.kind==='epg'?'节目单 · XMLTV':'直播源 · M3U / TXT'}</span><h2>${esc(s.name)}</h2></div>${sourceToggle(s)}</div>
   <div class="feed-address"><small>订阅地址</small><p class="url">${esc(s.url)}</p></div>
   <dl class="feed-facts"><div><dt>自动更新</dt><dd>${esc(sourceSchedule(s))}</dd></div><div><dt>资料优先级</dt><dd>${s.priority}</dd></div></dl>
   ${s.guide_urls?.length?`<details class="feed-guides"><summary>自带节目单 <span>${s.guide_urls.length}</span></summary>${s.guide_urls.map(url=>`<p class="url">${esc(url)}</p>`).join('')}</details>`:''}
   <div class="feed-bottom"><small><span class="feed-result-label">最近更新${s.updated?' · '+esc(new Date(s.updated*1000).toLocaleString()):''}</span>${esc(s.last||'尚未更新，保存后可更新订阅')}</small><button type="button" data-feed="${s.id}" aria-label="编辑 ${esc(s.name)}">编辑订阅</button></div>
 </article>`).join('')||'<div class="source-empty"><h2>添加你的第一个订阅</h2><p>接入直播清单或 XMLTV 节目单，统一管理更新与发布。</p><button type="button" id="emptyAddSource" class="primary">添加订阅</button></div>';
 if($('emptyAddSource'))$('emptyAddSource').onclick=()=>editSource();
 applyHelp($('feeds'));
}
function sourceSchedule(s){
 const hours=s.interval_hours??state.settings.minutes/60;
 const plan=(s.interval_hours==null?'跟随默认 · ':'')+(hours?'每 '+Number(hours.toFixed(4))+' 小时':'自动更新已关闭');
 if(state.preview&&s.next_update)return plan+' · 预览中暂停调度';
 return plan+(s.next_update?' · 下次更新 '+new Date(s.next_update*1000).toLocaleString():'');
}
function editSource(id){
 const s=state.sources.find(x=>x.id===id)||{enabled:1,priority:10,kind:'playlist'};
 const mode=s.interval_hours==null?'inherit':s.interval_hours===0?'off':'custom';
 const html=`<div class="source-intro">${sourceIcon(s.kind)}<p>连接直播清单或节目单，设置订阅的更新方式。</p></div>
 <section class="source-section"><h3>订阅信息</h3><div class="source-form-grid">
 <label>订阅名称<input name="name" required placeholder="例如：家庭直播源" value="${esc(s.name||'')}"></label>
 <label>内容类型<select name="kind"><option value="playlist">直播 · M3U / TXT</option><option value="epg" ${s.kind==='epg'?'selected':''}>节目单 · XMLTV / GZIP</option></select></label>
 <label class="source-wide">订阅地址<input name="url" type="url" required placeholder="https://example.com/channels.m3u" value="${esc(s.url||'')}" spellcheck="false" autocomplete="off"><small>填写可直接获取直播清单或节目单的完整地址。</small></label>
 </div></section>
 <section class="source-section"><h3>更新与优先级</h3><div class="source-form-grid">
 <label>自动更新<select name="frequency"><option value="inherit">跟随默认设置</option><option value="custom">按指定间隔更新</option><option value="off">关闭自动更新</option></select><small id="sourcePlanHint"></small></label>
 <label>资料优先级<input name="priority" type="number" step="1" required value="${esc(s.priority)}"><small>数值越小，频道资料越优先采用。</small></label>
 <label id="sourceHours" class="source-wide">更新间隔<span class="source-hours"><input name="hours" type="number" min="0.016666666666666666" max="720" step="any" required value="${esc(s.interval_hours||state.settings.minutes/60||6)}"><span>小时</span></span></label>
 </div></section>
 <footer class="source-editor-footer">${id?'<button type="button" id="deleteSource" class="source-delete">删除订阅</button>':'<span></span>'}<div><button type="button" id="cancelSource">取消</button><button type="submit" class="primary">保存订阅</button></div></footer>`;
 show(id?'编辑订阅':'添加订阅',html,async form=>{
   const f=form.elements;
   await api('source',{id,name:f.name.value,kind:f.kind.value,url:f.url.value,priority:+f.priority.value,interval_hours:f.frequency.value==='inherit'?null:f.frequency.value==='off'?0:+f.hours.value});
 });
 $('dialog').classList.add('source-editor');$('apply').hidden=true;
 $('cancelSource').onclick=()=>$('dialog').close();
 const f=$('editor').elements;
 f.kind.onchange=()=>{$('fields').querySelector('.source-intro .source-icon').outerHTML=sourceIcon(f.kind.value);};
 f.priority.title='多个订阅提供同一频道时，数字较小的订阅优先提供频道资料。';
 f.frequency.value=mode;
 function showHours(){
   f.hours.disabled=f.frequency.value!=='custom';$('sourceHours').hidden=f.hours.disabled;
   const hours=state.settings.minutes/60;
   $('sourcePlanHint').textContent=f.frequency.value==='inherit'?(hours?'当前默认每 '+hours+' 小时更新，可在设置页调整。':'默认自动更新已关闭。'):f.frequency.value==='off'?'仅在手动更新时获取最新内容。':'按下方设置的小时数自动更新。';
 }
 f.frequency.onchange=showHours;showHours();
 if(id)$('deleteSource').onclick=safely(async()=>{if(!confirm('删除这个订阅及其频道快照？'))return;await api('delete',{id});$('dialog').close();await reload();});
}
function bindTitleLogo(key){
 const slot=$('titleLogo');
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
   if(!candidates.length){slot.textContent='无台标';return;}
   const img=new Image();img.referrerPolicy='no-referrer';img.alt=c.label+'台标';
   let i=0;
   img.onerror=()=>{
     if(slot.firstChild!==img)return;
     if(++i<candidates.length){img.src=candidates[i];return;}
     slot.textContent='加载失败';
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
 const routes=c.lines.map((line,i)=>`<div class="route"><div class="route-heading"><b>线路 ${i+1} · ${esc(line.from.join(' / '))}</b><span data-result-badge>${testBadge(c.name,line)}</span></div><p class="url">${addressLink(c,line,i)}<small class="source-name">${esc(detailSource(line))}</small></p><small data-probe-cell="${esc(line.url)}"><span class="last-probe">${detailMetrics(line)}</span><span class="live-probe metric-result"></span></small><div class="route-controls">${routeToggle(c,line)}<button type="button" data-test="${esc(line.url)}" ${busy?'disabled':''}>测此线路</button>${hideButton('route',key,line.url)}${playButton(c,line)}</div></div>`).join('');
 const groups=[...new Set([...directoryGroups(),...Object.keys(state.hidden_groups||{})])].map(g=>[g,g]);
 const members=c.members.filter(m=>m!==key);
 const targets=state.channels.filter(ch=>ch.name!==key).map(ch=>[ch.name,ch.label===ch.name?ch.label:`${ch.label}（${ch.name}）`]);
 const autoGuide='自动匹配（'+(c.auto_epg||key)+'）';
 const settings=`<fieldset id="channelSettings" hidden disabled><legend>编辑频道</legend>
   <section class="edit-block"><h3>频道资料</h3><div class="settings-grid">
   <label>频道名称<input name="label" value="${esc(r.label)}" placeholder="${esc(key)}"><small>留空使用原名「${esc(key)}」。</small></label>
   <label>所属分组<select name="group">${editOptions(groups,r.group,'自动分组（'+c.auto_group+'）')}</select><small>选择后，频道和它的线路一起移入该组。</small></label></div></section>
   <section class="edit-block"><h3>频道台标 <span id="logoMode"></span></h3><div class="edit-logo-row"><div id="editLogoPreview"></div><div><div class="route-controls"><button type="button" id="chooseLogo">上传台标</button><button type="button" id="removeLogo" hidden>移除台标</button></div><small>PNG、JPEG 或 WebP，最大 2 MB。上传立即生效；未上传或移除后自动匹配。</small></div></div><input type="file" id="logoFile" accept="image/png,image/jpeg,image/webp" hidden><small id="logoFeedback" role="status"></small></section>
   <section class="edit-block"><h3>节目单与线路</h3><div class="settings-grid">
   <label>节目单对应频道<select name="epg">${editOptions([],r.epg,autoGuide)}</select><small id="epgHint">从已获取的节目单中选择。</small></label>
   <label>将线路合并到<select name="target" ${members.length?'disabled':''}>${editOptions(targets,r.target,'保持独立频道')}</select><small>${members.length?'先拆出下列成员，才能合并到其他频道。':'保存后，本频道的线路会出现在所选频道下。'}</small></label></div>
   ${members.length?`<div class="edit-members">已合并的频道　${members.map(m=>`<button type="button" data-unmerge="${esc(m)}">拆出 ${esc(m)}</button>`).join(' ')}</div>`:''}</section>

   <div class="detail-edit-actions"><button type="button" id="discardEdit">取消</button><button type="submit" class="primary">保存设置</button></div></fieldset>`;
 const overview=`<details class="detail-guide" open><summary>节目单</summary><div id="detailGuide" aria-live="polite">正在读取节目单…</div></details>
   <p id="detailPublication" class="detail-publication">${esc(publicationSummary(c))}</p><section class="detail-routes" data-detail-owner="${esc(key)}"><h3>线路状态 <small>${c.lines.length} 条</small></h3>${routes}</section>`;
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
     guideHint.textContent=entries.length?'选择电视指南中对应的频道，或保留自动匹配。':'暂无节目单频道。更新订阅后可在这里选择。';
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
 if(r.test.at)metrics.push(esc(new Date(r.test.at).toLocaleString()));
 metrics.push(...(r.test.note||[]).map(esc));
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

 list.querySelector('h3 small').textContent=c.lines.length+' 条';
 let previous=list.querySelector('h3');
 for(const [index,r] of c.lines.entries()){
   const box=boxes.get(r.url);
   if(!box)continue;
   if(previous.nextElementSibling!==box)previous.after(box);
   previous=box;
   box.querySelector('[data-address]').dataset.number=index+1;
   box.querySelector('.source-name').textContent=detailSource(r);
   box.querySelector('.route-heading b').textContent=`线路 ${index+1} · ${r.from.join(' / ')}`;
   const badge=box.querySelector('[data-result-badge]');
   if(!badge.contains(popupAnchor))badge.innerHTML=testBadge(c.name,r);
   box.querySelector('.last-probe').innerHTML=detailMetrics(r);
   syncRouteToggle(box,r);
 }
}
function paintProgress(){
 const url=liveJob?.active_url;
 const channels=url?(state?.channels||[]).filter(c=>c.lines.some(r=>r.url===url)):[];
 const links=channels.map(c=>`<button type="button" class="task-channel" data-locate="${esc(c.name)}" title="定位到测速中的${lineView()?'线路':'频道卡片'}">${esc(c.label)}</button>`).join(' / ');
 const names=$('taskChannels');
 if(names.dataset.markup!==links){
   names.innerHTML=links;
   names.dataset.markup=links;
 }
 const route=channels.length===1?' · 线路 '+(channels[0].lines.findIndex(r=>r.url===url)+1):'';
 const sample=liveJob?.sample?(liveJob.sample_seconds?`${liveJob.sample_seconds} 秒 · `:'')+liveJob.sample:'正在连接线路';
 if(liveJob){
   $('taskMessage').textContent=url?'正在测 '+(channels.length?'':liveJob.active_channel||'当前线路'):
     (liveJob.running?'进行中　':'任务　')+liveJob.message+(liveJob.running&&liveJob.total?'　'+liveJob.done+'/'+liveJob.total:'');
   $('taskProgress').textContent=url?`${route} · 整批第 ${liveJob.done+1}/${liveJob.total} 条 · ${sample}`:'';
 }
 for(const card of document.querySelectorAll('[data-card-name]')){
   const active=Boolean(url&&cardChannels.get(card.dataset.cardName)?.lines.some(r=>r.url===url));
   card.classList.toggle('testing',active);
   if(active)card.setAttribute('aria-label',card.dataset.cardName+'，正在测速');
   else card.setAttribute('aria-label',cardChannels.get(card.dataset.cardName)?.label||card.dataset.cardName);
 }
 for(const btn of document.querySelectorAll('[data-test]')){
   const active=Boolean(url&&url===btn.dataset.test);
   btn.disabled=active?(!liveJob.run_id||liveJob.stop_line):busy;
   btn.textContent=active?(liveJob.run_id?'跳过此线路':'测速中'):btn.closest('#dialog')?'测此线路':'测试';
   btn.title=active?document.querySelector('.task-actions [data-stop-current]').title:'只测试这条直播地址，按当前通过条件更新结果。';
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
   if(active)el.querySelector('.live-probe').textContent=liveJob.sample?'测速中 · '+liveJob.sample:'测速中…';
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
async function status(){
 const {job,logs,log_id}=await api('status?compact=1');liveJob=job;
 legacyLogs=log_id===undefined?(logs||[]):null;
 $('logKind').disabled=Boolean(legacyLogs);
 if(legacyLogs)$('logKind').value='history';
 latestLogVersion=JSON.stringify([log_id,job.log_epoch]);
 if($('logDialog').open&&logVersion!==latestLogVersion)await loadLogs(latestLogVersion);
 busy=job.running;$('sync').disabled=busy;updateProbeAction();
 for(const button of document.querySelectorAll('[data-test]'))button.disabled=busy;
 for(const button of document.querySelectorAll('[data-source-toggle]'))button.disabled=busy||changingSources.has(+button.dataset.sourceToggle);
 $('probePicked').disabled=busy||!picked.size;
 if(wasRunning&&!job.running){await reload(job.action==='probe');}
 else if(revision!==job.revision){await reload(job.running||job.action==='probe'||revision===null);}
 revision=job.revision;wasRunning=job.running;paintProgress();
}
async function launch(action,url,urls){await api('job',{action,url,urls});wasRunning=true;await status();}
document.addEventListener('click',safely(async e=>{
 const batchItem=e.target.closest('[data-selection-key]');
 if(batchItem&&$('channels').classList.contains('batch-open')){e.preventDefault();toggleBatchItem(batchItem);return;}
 const d=e.target.dataset;
 if(d.sourceToggle){
   const id=+d.sourceToggle,s=state.sources.find(s=>s.id===id);
   if(!s||busy||changingSources.has(id))return;
   const before=Boolean(s.enabled),button=e.target;
   changingSources.add(id);button.disabled=true;setSwitch(button,!before,'订阅 '+s.name);
   let saved=false;
   try{
     await api('source-state',{id,enabled:!before});saved=true;await reload();
     notice('订阅已'+toggleText(!before));
   }catch(error){if(!saved)setSwitch(button,before,'订阅 '+s.name);throw error;}
   finally{
     changingSources.delete(id);
     for(const b of document.querySelectorAll('[data-source-toggle]'))b.disabled=busy||changingSources.has(+b.dataset.sourceToggle);
   }
   return;
 }
 const play=e.target.closest('button[data-play]');
 if(play){await openPlayer(play.dataset.owner,play.dataset.play);return;}
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
     await reload();notice('已'+(mode==='hide'?'隐藏':toggleText(mode==='on'))+' '+keys.length+(routes?' 条线路':mode==='hide'?' 个频道':' 个频道内的全部可见线路')+(mode!=='hide'&&state.settings.strict?'，各线路下次取得有效测速结果后自动判定':''));
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
   btn.disabled=true;setSwitch(btn,mode==='on');
   let saved=false;
   try {
     await api('route',{name:d.owner,url:d.url,mode});saved=true;await reload();
     notice('线路已手动'+toggleText(mode==='on')+(state.settings.strict?'，下次取得有效测速结果后自动判定':''));
   }catch(error){
     if(!saved)setSwitch(btn,previous);
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
   if(all&&!confirm('终止整批测速？已完成的结果会保留，剩余线路将不再测试。'))return;
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
   if(urls.length&&confirm(`仅测试「${healthFilter.group}」当前筛出的 ${urls.length} 条${healthNames[healthFilter.status]}线路？`))return launch('probe',undefined,urls);
 }else if(confirm('按当前检测方式逐条测试启用频道下的全部线路。开始？'))return launch('probe');
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
   const time=esc(new Date(row.ts*1000).toLocaleString());
   const failed=['失败','部分失败','未通过','暂时失败','未能验证','待复测'].includes(row.result);
   const reason=failed?`<details data-id="${row.id}" ${open.has(String(row.id))?'open':''}><summary>查看失败原因</summary><p>${esc(row.reason||row.message)}</p></details>`:'';
   return `<article class="log-entry"><header><b>${esc(row.source||'历史任务')}${row.channel?' · '+esc(row.channel):''}</b><time>${time}</time></header>
    <div>${speedTones.has(row.result)?metricText(row.result,speedTones.get(row.result)):`<strong class="${failed?'bad':['已跳过','已终止'].includes(row.result)?'':'good'}">${esc(row.result)}</strong>`} ${esc(row.message)}</div>
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
 if($('channelSettings')?.disabled)return;
 const form=e.target,fields=$('fields').firstElementChild;
 const saved=await editing(form);
 await reload();
 if($('dialog').open&&$('fields').firstElementChild===fields){
   if(typeof saved==='string'){
     const channel=state.channels.find(c=>c.name===saved||c.members.includes(saved));
     if(channel)editChannel(channel.name);else $('dialog').close();
   }else $('dialog').close();
 }
 notice('已保存');});
let settingsDirty=false;
function updateCriteriaFields(){
 const f=$('schedule').elements;
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
   const fields={probe_mode:f.probe_mode.value,hours:+f.hours.value,criteria:f.criteria.value,min_speed:+f.min_speed.value,min_height:+f.min_height.value,mode:f.mode.value,strict:f.strict.checked,auto_probe:f.auto_probe.checked};
   settingsDirty=false;
   try{await api('settings',fields);}catch(error){settingsDirty=true;throw error;}
   await reload();
   if(!settingsDirty)$('saveStatus').textContent='设置已保存';
 }finally{button.disabled=false;}
});
document.querySelector('[data-tab="channels"]').classList.add('active');
async function poll(){
 try{await status();}catch(e){notice(e.message);}
 finally{setTimeout(poll,busy?800:2000);}
}
document.addEventListener('visibilitychange',safely(async()=>{
 if(state&&!document.hidden)await reload(true);
}));
safely(async()=>{await reload();await poll();})();
