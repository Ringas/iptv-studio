"""浅色使用透明图案叠浅底；保守去底不误删封闭白色、浅灰细节和彩色底板。"""
import hashlib,json
import logos

def fixture(w=60,h=60):
 raw=bytearray(bytes((255,255,255,255))*w*h)
 def box(x1,y1,x2,y2,color):
  for y in range(y1,y2):
   for x in range(x1,x2):raw[(y*w+x)*4:(y*w+x+1)*4]=bytes(color)
 return raw,box
raw,box=fixture();box(15,15,45,45,(180,20,20,255));box(24,24,36,36,(255,255,255,255))
clean=logos.remove_white_background(logos.png(60,60,raw));pixels=logos.ffmpeg(clean,raw=True)
assert pixels[3]==0 and pixels[(30*60+30)*4+3]==255 and pixels[(15*60+15)*4+3]==255
raw,box=fixture();box(15,15,45,45,(220,220,220,255))
pixels=logos.ffmpeg(logos.remove_white_background(logos.png(60,60,raw)),raw=True)
assert pixels[(30*60+30)*4:(30*60+30)*4+4]==bytes((220,220,220,255))
for color in ((200,20,20,255),(255,255,255,255),(255,255,255,128)):
 original=logos.png(20,20,bytes(color)*400)
 assert logos.remove_white_background(original)==original
# Dark transparent artwork uses exact compositing without an added outline.
raw=bytearray(60*60*4)
for y in range(20,40):
 for x in range(10,50):raw[(y*60+x)*4:(y*60+x+1)*4]=bytes((30,60,120,255))
image=logos.png(60,60,raw);transparent=logos.ffmpeg(logos.render(image,'transparent'),raw=True)
light=logos.ffmpeg(logos.render(image,'light'),raw=True);bg=(237,241,246)
for i in range(300*180):
 alpha=transparent[i*4+3]/255
 expected=bytes([round(transparent[i*4+k]*alpha+bg[k]*(1-alpha)) for k in range(3)]+[255])
 assert light[i*4:i*4+4]==expected
# White artwork retains contrast on a light background.
white=bytearray(60*60*4)
for y in range(20,40):
 for x in range(10,50):white[(y*60+x)*4:(y*60+x+1)*4]=bytes((255,255,255,255))
white=logos.png(60,60,white)
assert logos.needs_light_contrast(white)
outlined=logos.ffmpeg(logos.render(white,'light'),raw=True)
assert any(max(outlined[i:i+3])<150 for i in range(0,len(outlined),4)),'white logo lost its contrast outline'
# New light rendering must not reuse the old cache, while other mode URLs stay stable.
candidates=['https://fixture.invalid/logo.png'];old=hashlib.sha256(json.dumps(candidates,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
assert old not in logos.image_route(candidates,'light')
for mode in ('off','dark','transparent'):assert old in logos.image_route(candidates,mode)
print('通过：白底处理、封闭白色/浅灰/彩色保护、透明白字、浅底逐像素合成、浅色缓存失效与其他模式地址稳定。')
