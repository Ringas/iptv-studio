/* Read the picture size from a video PES in an MPEG-TS sample.
   Only the first bounded portion of the measured last segment is supplied. */
class Bits {
  constructor(bytes){this.bytes=bytes;this.pos=0;}
  read(n){
    if(this.pos+n>this.bytes.length*8)throw Error('Incomplete SPS');
    let value=0;
    while(n--){value=value*2+((this.bytes[this.pos>>3]>>(7-this.pos%8))&1);this.pos++;}
    return value;
  }
  skip(n){if(this.pos+n>this.bytes.length*8)throw Error('Incomplete SPS');this.pos+=n;}
  ue(){
    let zeros=0;
    while(!this.read(1))if(++zeros>24)throw Error('Invalid SPS');
    return 2**zeros-1+this.read(zeros);
  }
  se(){const u=this.ue();return u%2?(u+1)/2:-u/2;}
}
function h264(b){
  const profile=b.read(8);b.skip(16);b.ue();
  let chroma=1;
  if([100,110,122,244,44,83,86,118,128,138,139,134,135,144].includes(profile)){
    chroma=b.ue();if(chroma>3)throw Error('Invalid chroma format');
    const four=chroma===3;
    if(four && b.read(1))chroma=0;
    b.ue();b.ue();b.skip(1);
    if(b.read(1)){
      for(let i=0;i<(four?12:8);i++)if(b.read(1)){
        let last=8,next=8;
        for(let j=0;j<(i<6?16:64);j++){if(next)next=(last+b.se()+256)%256;last=next||last;}
      }
    }
  }
  b.ue();const order=b.ue();
  if(order===0)b.ue();
  else if(order===1){b.skip(1);b.se();b.se();const count=b.ue();for(let i=0;i<count;i++)b.se();}
  b.ue();b.skip(1);
  let width=16*(1+b.ue()),height=16*(1+b.ue());
  const frame=b.read(1);height*=2-frame;if(!frame)b.skip(1);b.skip(1);
  if(b.read(1)){
    width-=(b.ue()+b.ue())*(chroma===1||chroma===2?2:1);
    height-=(b.ue()+b.ue())*(2-frame)*(chroma===1?2:1);
  }
  return {width,height};
}
function h265(b){
  b.skip(4);const layers=b.read(3);b.skip(1+96);
  const flags=[];for(let i=0;i<layers;i++)flags.push([b.read(1),b.read(1)]);
  if(layers)b.skip(2*(8-layers));
  for(const [p,l] of flags){if(p)b.skip(88);if(l)b.skip(8);}
  b.ue();let chroma=b.ue();
  if(chroma>3)throw Error('Invalid chroma format');
  if(chroma===3 && b.read(1))chroma=0;
  let width=b.ue(),height=b.ue();
  if(b.read(1)){width-=(b.ue()+b.ue())*(chroma===1||chroma===2?2:1);height-=(b.ue()+b.ue())*(chroma===1?2:1);}
  return {width,height};
}
export function dimensions(sample){
  const video=new Map();
  let start=0;
  while(start+188<sample.length && (sample[start]!==71||sample[start+188]!==71))start++;
  for(let p=start;p+188<=sample.length;p+=188){
    if(sample[p]!==71)break;
    const pid=((sample[p+1]&31)<<8)|sample[p+2],control=(sample[p+3]>>4)&3;
    if(!(control&1))continue;
    let i=p+4+(control===3?1+sample[p+4]:0);
    if(i>=p+188)continue;
    if(sample[p+1]&64){
      if(i+9>p+188 || sample.readUIntBE(i,3)!==1 || (sample[i+3]&240)!==224)continue;
      if(!video.has(pid))video.set(pid,[]);
      i+=9+sample[i+8];
    }
    if(i<p+188&&video.has(pid))video.get(pid).push(sample.subarray(i,p+188));
  }
  for(const chunks of video.values()){
    const data=Buffer.concat(chunks), prefix=Buffer.from([0,0,1]);
    for(let p=data.indexOf(prefix);p!==-1;p=data.indexOf(prefix,p+3)){
      const i=p+3, avc=(data[i]&31)===7, hevc=(data[i]>>1&63)===33;
      if(!avc&&!hevc)continue;
      const end=data.indexOf(prefix,i+1);
      const bytes=data.subarray(i+(avc?1:2),Math.min(end<0?data.length:end,i+4096));
      const rbsp=bytes.filter((v,j)=>!(v===3&&j>=2&&bytes[j-1]===0&&bytes[j-2]===0));
      try{
        const size=(avc?h264:h265)(new Bits(rbsp));
        if(size.width>=16&&size.height>=16&&size.width<=16384&&size.height<=16384)return {...size,source:'末片视频头'};
      }catch{} // A partial or unrelated NAL must not become a published size.
    }
  }
  return null;
}
