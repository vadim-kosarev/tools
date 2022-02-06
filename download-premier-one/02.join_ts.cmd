call .\00.env.cmd

ffmpeg -safe 0 -f concat -i target/_ls.txt -c copy "target/result/%videoName%.ts"
