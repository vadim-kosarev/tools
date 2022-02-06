call .\00.env.cmd

del target\_ls.txt
mkdir target
mkdir target\src
mkdir target\result

FOR /L %%i IN (1,1,%maxSegment%) DO (
   set fName=seg-%%i-v1-a1.ts
   curl "%urlBase%/seg-%%i-v1-a1.ts" --output target/src/seg-%%i-v1-a1.ts || exit
   echo file 'src/seg-%%i-v1-a1.ts' >> target\_ls.txt
) 
