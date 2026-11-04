import {rateLimitError} from './playback.mjs';
import {mediaFetch} from './media-http.mjs';
import dgram from 'node:dgram';
import {isIPv4} from 'node:net';

// Keep only PAT, SDT and EIT packets. Media bytes never leave the sampler.
function collector(){
  const packets=[];
  let tail=Buffer.alloc(0),received=0;
  return {
    get full(){return received>=6*1024*1024 || packets.length>=4096;},
    add(chunk){
      received+=chunk.length;
      const data=tail.length?Buffer.concat([tail,chunk]):chunk;
      let i=0;
      while(i+188<=data.length && packets.length<4096){
        if(data[i]!==0x47 || (i+188<data.length && data[i+188]!==0x47)){i++;continue;}
        const pid=((data[i+1]&31)<<8)|data[i+2];
        if(pid===0||pid===17||pid===18)packets.push(Buffer.from(data.subarray(i,i+188)));
        i+=188;
      }
      tail=Buffer.from(data.subarray(Math.max(i,data.length-187)));
    },
    result(){return Buffer.concat(packets).toString('base64');}
  };
}

async function datagrams(address,sample){
  const url=new URL(address);
  if(!isIPv4(url.hostname)||!Number(url.port))return;
  const first=Number(url.hostname.split('.')[0]);
  const multicast=first>=224&&first<=239;
  const socket=dgram.createSocket({type:'udp4',reuseAddr:true});
  await new Promise(resolve=>{
    const timeout=setTimeout(resolve,6500);
    function finish(){clearTimeout(timeout);resolve();}
    socket.on('error',finish);
    socket.on('message',(packet,peer)=>{
      if(!multicast && url.hostname!=='0.0.0.0' && peer.address!==url.hostname)return;
      if(url.protocol==='rtp:'){
        if(packet.length<12 || packet[0]>>6!==2)return;
        let start=12+4*(packet[0]&15);
        if(packet[0]&16){
          if(start+4>packet.length)return;
          start+=4+4*packet.readUInt16BE(start+2);
        }
        packet=packet.subarray(start,packet.length-(packet[0]&32?packet.at(-1):0));
      }
      sample.add(packet);
      if(sample.full)finish();
    });
    socket.bind(Number(url.port),multicast && process.platform!=='win32'?url.hostname:'0.0.0.0',()=>{
      if(multicast){
        try{socket.addMembership(url.hostname);}catch{finish();}
      }
    });
  });
  socket.close();
}

export async function eitSample(url,manifestHeaders,mediaHeaders,parseManifest){
  const sample=collector();
  if(/^(rtp|udp):/i.test(url)){
    await datagrams(url,sample);
    return sample.result();
  }
  const signal=AbortSignal.timeout(9000);
  async function read(address,depth,seg=null){
    if(depth>4||!/^https?:\/\//i.test(address)||sample.full)return;
    const headers={...(seg?mediaHeaders:manifestHeaders)};
    if(seg?.span)headers.Range=`bytes=${seg.span.start}-${seg.span.end}`;
    const response=await mediaFetch(address,{headers,signal});
    if(!response.ok||(seg?.span&&response.status!==206)){
      await response.body?.cancel();
      if(response.status===429)throw rateLimitError(response,'节目表入口');
      throw new Error('HTTP '+response.status);
    }
    let prefix=Buffer.alloc(0),kind=seg?'ts':'',size=0;
    const chunks=[];
    for await(const chunk of response.body){
      let data=Buffer.from(chunk);
      if(!kind){
        prefix=Buffer.concat([prefix,data]);
        if(prefix.length<10)continue;
        data=prefix;prefix=Buffer.alloc(0);
        kind=data.subarray(0,80).toString().trimStart().startsWith('#EXTM3U')?'playlist':'ts';
      }
      if(kind==='ts'){
        sample.add(data);
        if(sample.full)break;
      }else{
        size+=data.length;
        if(size>1024*1024)throw new Error('清单过大');
        chunks.push(data);
      }
    }
    if(kind!=='playlist')return;
    const list=parseManifest(Buffer.concat(chunks).toString().trim().split(/\r?\n/).map(s=>s.trim()),response.url);
    if(list.variants.length){
      const lowest=list.variants.reduce((a,b)=>a.rate<=b.rate?a:b);
      await read(lowest.url,depth+1);
    }else{
      const segments=list.segments.filter(s=>!s.gap&&!s.map&&s.encryption==='NONE');
      for(const s of segments.slice(segments.length>2?-2:-1))await read(s.url,depth+1,s);
    }
  }
  try{await read(url,0);}catch(error){if(error.httpStatus===429||!sample.result())throw error;}
  return sample.result();
}
