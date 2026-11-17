// A lease lasts for the whole probe session, including redirected/media hosts.
// On collision, do not wait inside a measurement window: isolate the route later.
import {mkdirSync,readFileSync,writeFileSync} from 'node:fs';
import {createHash} from 'node:crypto';
import {join} from 'node:path';
const held=new Set();
export function acquireGateway(url){
  const root=process.env.IPTV_GATEWAY_LEASE_DIR,owner=process.env.IPTV_GATEWAY_OWNER;
  if(!root||!owner)return;
  const host=url.hostname.toLowerCase().replace(/\.$/,'');
  const id=createHash('sha256').update(host).digest('hex');
  try{
    const remaining=JSON.parse(readFileSync(join(root,id+'.cool'),'utf8')).until-Date.now()/1000;
    if(remaining>0)throw Object.assign(new Error('媒体网关仍在冷却，保留为未能验证'),{httpStatus:429,retryAfter:Math.ceil(remaining),unverified:true});
  }catch(e){if(e.code!=='ENOENT')throw e;}
  if(held.has(host))return;
  const dir=join(root,id+'.lease');
  try{mkdirSync(dir);writeFileSync(join(dir,'owner'),owner);held.add(host);
    console.log(JSON.stringify({gateway_acquired:true,host}));}
  catch(e){
    if(e.code!=='EEXIST')throw e;
    throw Object.assign(new Error('实际网关被另一条检测占用，转隔离复核'),{unverified:true,retryable:true,retryDeferred:'gateway_isolation'});
  }
}
export function reportGatewayLimit(response,url){
  if(response.status!==429||!process.env.IPTV_GATEWAY_LEASE_DIR)return;
  const value=response.headers.get('retry-after')||'';
  const seconds=/^\d+$/.test(value)?Number(value):Math.max(0,(Date.parse(value)-Date.now())/1000)||0;
  console.log(JSON.stringify({gateway_limit:true,host:url.hostname.toLowerCase().replace(/\.$/,''),seconds:Math.max(6,Math.ceil(seconds))}));
}
