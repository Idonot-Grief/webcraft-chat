# webcraft-chat
A wrapper for you minecraft server that let's you view in-game chat from a website and talk to the chat as a guest.
To run this, you have to download java to the javbin directory in your server root. For example:

______________________________
| <-- ServerRoot
|
|-----webcraft_wrapper.py
|
|-----server.jar
|
|-----fabic-server-launch.jar
|
|-----javbin
|       |
|      bin
|       |
|   javaw.exe
_______________________________

This program only needs Python 3.14 and Flask. Nothing else to run the script.
How it works is the python script hijacks the server console, then hosts a flask
frontend. When the client on the frontend sends a message, it uses /tellraw to emulate
an actual chat message. To read messages, it looks for join messages and leave messages,
and then checks if console logs contain the proper username syntax. If true,
it will show the message on the website. This also means instead of using start.bat,
you have to use the script to launch the server, so configure your provisioning tool
to start the python script instead of the bath or bash file. And yes, it is 
written for Windows, so you will have to modify the script to work on Linux.
