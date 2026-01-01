# Why did I build this?

## TL;DR version:
Together with my buddy Claude, I "built" a Docker container that allows you to utilise the Linux devices you have around the place, connect them to Bluetooth speakers, and present them in Music Assistant as a Sendspin client.

## Longer version
Disclaimer: the following explanation is the ultimate in privilege, however the output is beneficial for everyone. 😉

I recently got an infrared sauna which has Bluetooth speakers and I wanted to automate the full experience - which included the playing of a sauna relaxation playlist.

I'd already set up a Squeezelite instance on an ESP32 unit, but it had connection issues (due to WiFi + Bluetooth A2DP).

As there was already a Surface Pro 4 running Ubuntu with TouchKio nearby, I thought: "Why not use that? It's always turned on."

I tried setting it up as a Squeezelite client but had some challenges, so thought it would be easier to build something specific that used the hardware available (the Bluetooth controller) and included some connection handling as well as diagnostics and info.
