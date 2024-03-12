:: update the data below
:: replace ^& with ^^&

set suffix=zvezdy-1

:: Video
curl -H @headers.txt https://vkvd117.mycdn.me/?expires=1710597512403^&srcIp=95.216.148.18^&pr=40^&srcAg=CHROME^&ms=185.226.53.161^&mid=7204095338736^&type=5^&subId=6381682821872^&sig=K0EGottM89U^&ct=32^&urls=45.136.22.176^&clientType=13^&appId=512000384397^&asubs=y^&id=6237076064812^&bytes=0-978873737 --output a-video-%suffix%.webm

:: Audio
curl -H @headers.txt https://vkvd117.mycdn.me/?expires=1710597512403^&srcIp=95.216.148.18^&pr=40^&srcAg=CHROME^&ms=185.226.53.161^&mid=7204095338736^&type=1^&subId=6381682821872^&sig=aKN4Dpo2bzs^&ct=22^&urls=45.136.22.176^&clientType=13^&appId=512000384397^&asubs=y^&id=6237076064812^&bytes=0-166210796 --output b-audio-%suffix%.webm
