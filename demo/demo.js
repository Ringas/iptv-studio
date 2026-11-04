// No backend or remote media requests: mutations live in this tab only.
const demoReady=fetch('./demo-state.json').then(r=>r.json());
let demoRevision=0;
window.demoAPI=async(path,data)=>{
 const s=await demoReady,kind=path.split('?')[0];
 if(kind==='state')return structuredClone(s);
 if(kind==='status')return {job:{running:false,message:'模拟演示 · 无后台任务',done:0,total:0,revision:demoRevision},logs:[]};
 if(kind==='guide'||kind==='guide-options'||kind==='logs')return [];
 if(kind==='route'||kind==='routes'){
  const targets=data.targets||[data];
  for(const t of targets)for(const c of s.channels)if(c.name===(t.name||t.channel))for(const r of c.lines)if(r.url===t.url){r.enabled=data.mode==='on';r.mode=data.mode;r.published=r.enabled;}
 }else if(kind==='settings')Object.assign(s.settings,data,{minutes:data.hours*60});
 else if(kind==='clear-logs')return {ok:true};
 else throw Error('静态演示不执行此服务器操作。可体验搜索、分组、视图切换、频道详情和线路启停。');
 s.exported=s.channels.filter(c=>c.enabled&&c.lines.some(r=>r.enabled)).length;
 s.lines=s.channels.reduce((n,c)=>n+c.lines.filter(r=>r.enabled).length,0);
 demoRevision++;return {ok:true};
};
