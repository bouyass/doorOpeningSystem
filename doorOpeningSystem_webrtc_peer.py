import asyncio
import json
import websockets
from aiortc import (
    RTCPeerConnection,
    RTCSessionDescription,
    RTCIceCandidate,
    RTCConfiguration,
    RTCIceServer,
)
from aiortc.contrib.media import MediaPlayer, MediaRelay, MediaRecorder
import sounddevice as sd
import numpy as np
from threading import Lock
import cv2
import threading

SIGNALING_SERVER_URL = "ws://192.168.199.139:8765"

# ICE server configuration
ice_servers = [RTCIceServer("stun:stun.l.google.com:19302")]
configuration = RTCConfiguration(ice_servers)

pc = RTCPeerConnection(configuration)
media_relay = MediaRelay()
player = None
video_player = None
websocket = None
connection_ready = asyncio.Event()
audio_buffer = np.empty((0, 2), dtype=np.int16)
buffer_lock = Lock()


def addAudioTrack():
    global player, pc
    if not player:
        player = MediaPlayer(
            "alsa_input.usb-GN_Netcom_A_S_Jabra_EVOLVE_20_SE_000573D9C2BE0A-00.mono-fallback",
            format="pulse",
            options={"channels": "1", "sample_rate": "44100"},
        )
    audio_track = media_relay.subscribe(player.audio)
    pc.addTrack(audio_track)
    print("🎙️ Audio track added")


def addVideoTrack():
    global video_player, pc
    if not video_player:
        video_player = MediaPlayer(
            "/dev/video0",
            format="v4l2",
            options={"video_size": "320x240", "framerate": "10"},
        )
    if video_player.video:
        video_track = media_relay.subscribe(video_player.video)
        pc.addTrack(video_track)
        print("📹 Video track added")
    else:
        print("⚠️ No video track found")


async def consume_signaling():
    global websocket
    async with websockets.connect(SIGNALING_SERVER_URL) as ws:
        websocket = ws
        print("🌐 Connected to signaling server")

        addAudioTrack()
        addVideoTrack()

        offer = await pc.createOffer()
        await pc.setLocalDescription(offer)

        await ws.send(
            json.dumps(
                {"type": pc.localDescription.type, "sdp": pc.localDescription.sdp}
            )
        )
        print("📤 Sent initial offer")

        async for message in ws:
            data = json.loads(message)
            #data = data.candidate
            print("📥 Received:", data)

            if data["type"] == "offer":
                offer = RTCSessionDescription(sdp=data["sdp"], type="offer")
                await pc.setRemoteDescription(offer)

                addAudioTrack()
                addVideoTrack()

                answer = await pc.createAnswer()
                await pc.setLocalDescription(answer)

                await ws.send(
                    json.dumps(
                        {"type": pc.localDescription.type, "sdp": pc.localDescription.sdp}
                    )
                )
                print("📤 Sent answer")

            elif data["type"] == "answer":
                print("🔁 Processing received answer", data)
                answer = RTCSessionDescription(sdp=data["sdp"]["sdp"], type="answer")
                await pc.setRemoteDescription(answer)
                print("🔁 Received answer and set remote description")

            elif data["type"] == "ice-candidate":
                try:
                    candidate_dict = data["candidate"]
                    candidate_str = candidate_dict.get("candidate")

                    if not candidate_str:
                        print("⚠️ Empty candidate string, skipping")
                        continue

                    parts = candidate_str.split(' ')

                    foundation = parts[0].split(':')[1]
                    component = int(parts[1])
                    protocol = parts[2].lower()
                    priority = int(parts[3])
                    ip = parts[4]
                    port = int(parts[5])
                    candidate_type = parts[7]

                    related_address = None
                    related_port = None
                    if 'raddr' in parts:
                        related_address = parts[parts.index('raddr') + 1]
                    if 'rport' in parts:
                        related_port = int(parts[parts.index('rport') + 1])
                    ice_candidate = RTCIceCandidate(
                        foundation=foundation,
                        component=component,
                        priority=priority,
                        ip=ip,
                        port=port,
                        protocol=protocol,
                        type=candidate_type,
                        sdpMid=candidate_dict.get("sdpMid"),
                        sdpMLineIndex=candidate_dict.get("sdpMLineIndex"),
                        relatedAddress=related_address,
                        relatedPort=related_port
                    )

                    await pc.addIceCandidate(ice_candidate)
                    print("✅ ICE candidate added")

                except Exception as e:
                    print(f"❌ Error adding ICE candidate: {e}")

@pc.on("icecandidate")
async def on_icecandidate(candidate):
    if candidate and websocket:
        await websocket.send(
            json.dumps(
                {
                    "type": "candidate",
                    "sdpMid": candidate.sdpMid,
                    "sdpMLineIndex": candidate.sdpMLineIndex,
                    "candidate": candidate.candidate,
                }
            )
        )
        print(f"📤 Sent ICE candidate: {candidate.candidate}")


@pc.on("connectionstatechange")
async def on_connectionstatechange():
    print(f"🔄 Connection state changed: {pc.connectionState}")
    if pc.connectionState == "connected":
        connection_ready.set()
        print("✅ Peers are connected")


@pc.on("datachannel")
def on_datachannel(channel):
    print(f"📡 DataChannel received: {channel.label}")

    @channel.on("open")
    def on_open():
        print("✅ DataChannel is open")
        channel.send("Hello from Python!")

    @channel.on("message")
    def on_message(message):
        print(f"📩 Message: {message}")


@pc.on("track")
async def on_track(track):
    global audio_buffer
    print(f"✅ Track received: {track.kind}")

    if track.kind == "audio":
        print("🔄 Receiving audio frames...")

        async def receive_audio():
            global audio_buffer
            while True:
                frame = await track.recv()
                audio_data = frame.to_ndarray(format="s16").T
                if audio_data.shape[1] == 1:
                    audio_data = np.repeat(audio_data, 2, axis=1)
                with buffer_lock:
                    audio_buffer = np.concatenate((audio_buffer, audio_data), axis=0)
                    print(f"🔊 Received audio frame, buffer length: {len(audio_buffer)}")

        asyncio.create_task(receive_audio())

        start_output_stream()

        recorder = MediaRecorder("received_audio.wav")
        recorder.addTrack(track)
        await recorder.start()
        print("💾 MediaRecorder started")

    elif track.kind == "video":
        print("📹 Receiving video...")

        async def display_video():
            while True:
                frame = await track.recv()
                img = frame.to_ndarray(format="bgr24")
                cv2.imshow("WebRTC Video", img)
                if cv2.waitKey(1) & 0xFF == ord("q"):
                    break
            cv2.destroyAllWindows()

        asyncio.create_task(display_video())


def audio_callback(outdata, frames, time, status):
    global audio_buffer
    try:
        with buffer_lock:
            print(f"Buffer length: {len(audio_buffer)}, frames requested: {frames}")
            if len(audio_buffer) >= frames:
                outdata[:] = audio_buffer[:frames]
                audio_buffer = audio_buffer[frames:]
            else:
                outdata[: len(audio_buffer)] = audio_buffer
                outdata[len(audio_buffer) :] = 0
                audio_buffer = np.empty((0, 2), dtype=np.int16)
    except Exception as e:
        print(f"❌ Error in audio_callback: {e}")
        outdata[:] = np.zeros((frames, 2), dtype=np.int16)


def start_output_stream():
    def run_stream():
        with sd.OutputStream(
            samplerate=44100,
            channels=2,
            dtype="int16",
            callback=audio_callback,
        ):
            print("▶️ Audio output started")
            while True:
                sd.sleep(1000)

    threading.Thread(target=run_stream, daemon=True).start()


if __name__ == "__main__":
    asyncio.run(consume_signaling())
