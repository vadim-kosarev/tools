:: update the data below
:: replace ^& with ^^&

set site=boosty
set suffix=MADAGASCAR.paxan_doma

set videoUrl="https://vd331.okcdn.ru/?expires=1742982416131&srcIp=185.203.241.169&pr=42&srcAg=CHROME&ms=185.226.53.40&type=3&sig=09AH4hZmYw4&ct=11&urls=45.136.20.28&clientType=18&zs=43&id=7008066079487&bytes=0-2000000000"
set audioUrl="https://vd331.okcdn.ru/?expires=1742982416131&srcIp=185.203.241.169&pr=42&srcAg=CHROME&ms=185.226.53.40&type=3&sig=09AH4hZmYw4&ct=12&urls=45.136.20.28&clientType=18&zs=43&id=7008066079487&bytes=0-2000000000"

set headersFile=@headers-%site%.txt
set videoFile=%suffix%.VIDEO.webm
set audioFile=%suffix%.AUDIO.webm

:: Video
::start "%videoFile%" 
call curl -H %headersFile% %videoUrl% --output "%videoFile%"

:: Audio
::start "%audioFile%" 
call curl -H %headersFile% %audioUrl% --output "%audioFile%"


ffmpeg -i "%videoFile%" -i "%audioFile%" -c:v copy -c:a copy "%suffix%.FINAL.mp4"
