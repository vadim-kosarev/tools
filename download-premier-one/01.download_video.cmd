set urlBase=https://video-1-201.uma.media/hls-vod/p1Hntzr4nN3AJK2aGze3_w/1644184453/67/0x5000c500b35da91f/9f321ab742784ab08907ff2da5332240.mp4
set maxSegment=250

del target\_ls.txt
mkdir target
mkdir target\src
mkdir target\result

FOR /L %%i IN (1,1,%maxSegment%) DO (
   set fName=seg-%%i-v1-a1.ts
   curl "%urlBase%/seg-%%i-v1-a1.ts" --output target/src/seg-%%i-v1-a1.ts || exit
   echo file 'src/seg-%%i-v1-a1.ts' >> target\_ls.txt
) 
