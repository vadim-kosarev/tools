set urlBase=https://video-1-303.uma.media/hls-vod/tsfdf8yj_ZoSva_7ai5bgg/1644176480/51/0x5000c500b35a38d1/948701dc44e446179fadc35a78905987.mp4
set maxSegment=600

del target\_ls.txt
mkdir target
mkdir target\src
mkdir target\result

FOR /L %%i IN (1,1,%maxSegment%) DO (
   set fName=seg-%%i-v1-a1.ts
   curl "%urlBase%/seg-%%i-v1-a1.ts" --output target/src/seg-%%i-v1-a1.ts || exit
   echo file 'src/seg-%%i-v1-a1.ts' >> target\_ls.txt
) 
