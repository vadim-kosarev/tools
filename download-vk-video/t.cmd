:: update the data below
:: replace ^& with ^^&

set site=boosty
set suffix=madagaskar-1

set videoUrl="https://vd331.okcdn.ru/?expires=1742956629653&srcIp=176.115.147.200&pr=42&srcAg=CHROME&ms=185.226.53.40&type=3&sig=IBVFUOKnVMQ&ct=11&urls=45.136.20.28&clientType=18&id=7008066079487&bytes=0-2000000000"
set audioUrl="https://vd331.okcdn.ru/?expires=1742956629653&srcIp=176.115.147.200&pr=42&srcAg=CHROME&ms=185.226.53.40&type=3&sig=IBVFUOKnVMQ&ct=12&urls=45.136.20.28&clientType=18&id=7008066079487&bytes=0-2000000000"

set headersFile=@headers-%site%.txt
set videoFile=%suffix%-1.VIDEO.webm
set audioFile=%suffix%-1.AUDIO.webm

:: Video
start "%videoFile%" curl -H %headersFile% %videoUrl% --output "%videoFile%"

:: Audio
start "%audioFile%" curl -H %headersFile% %audioUrl% --output "%audioFile%"