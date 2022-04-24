#!/usr/bin/env python

# Based on work by Fred Clift from 2014
#
# ----------------------------------------------------------------------------
# "THE BEER-WARE LICENSE":
# <fred@clift.org> wrote this file. As long as you retain this notice you
# can do whatever you want with this stuff. If we meet some day, and you think
# this stuff is worth it, you can buy me a beer in return - Fred Clift
# (except that I dont drink....)
# ----------------------------------------------------------------------------
#

import cast_channel_pb2
import collections
import ssl
import asyncio
from struct import pack, unpack
import json

DEFAULT_APP = "CC1AD845"

namespace = {'con':        'urn:x-cast:com.google.cast.tp.connection',
             'receiver':   'urn:x-cast:com.google.cast.receiver',
             'cast':       'urn:x-cast:com.google.cast.media',
             'heartbeat':  'urn:x-cast:com.google.cast.tp.heartbeat',
             'message':    'urn:x-cast:com.google.cast.player.message',
             'media':      "urn:x-cast:com.google.cast.media"}


def make_msg(ns=None):
    msg = cast_channel_pb2.CastMessage()
    msg.protocol_version = msg.CASTV2_1_0
    msg.source_id = "sender-0"
    msg.destination_id = "receiver-0"
    msg.payload_type = cast_channel_pb2.CastMessage.STRING
    if ns:
        msg.namespace = namespace[ns]
    return msg


class Chromecast():

    def __init__(self, host, port):
        self.host = host
        self.port = port
        self.running = True
        self.id_counts = collections.Counter()
        self.dmr_session = None

        self.dispatch = {
                'RECEIVER_STATUS': self.handle_receiver_status,
                'MEDIA_STATUS': self.handle_media_status,
                'PING': self.pong
                }

    async def handle_media_status(self, data):

        if data['status']:
            self.msid = data['status'][0]['mediaSessionId']

    async def handle_receiver_status(self, msg):

        state = {'namespace': 'receiver',
                 'volume_level': msg['status']['volume']['level'],
                 'volume_muted': msg['status']['volume']['muted']}

        if 'applications' in msg['status']:

            apps = msg['status']['applications']
            state['current_app'] = apps[0]['displayName']

            try:
                # find details for DMR if it is already running
                da = min(filter(lambda a: a['appId'] == DEFAULT_APP, apps))
                self.dmr_session = da['sessionId']

                # open a connection
                await self.connect(dst=self.dmr_session)

                # check if anything is playing
                await self.media_status()

            except ValueError:
                self.dmr_session = None
        else:
            self.dmr_session = None

        await self.notify_state(state)

    async def notify_state(self, state):
        pass

    async def recv_loop(self):

        while self.running:
            data = await self.reader.read(4)

            if len(data) != 4:
                continue
            read_len = unpack(">I", data)[0]

            data = await self.reader.read(read_len)

            response = make_msg()
            response.ParseFromString(data)
            resp = json.loads(response.payload_utf8)
            # print(response.namespace)
            # print(json.dumps(resp, indent=2))
            handler = self.dispatch.get(resp['type'])
            if handler:
                await handler(resp)

    async def start(self):
        self.reader, self.writer = \
            await asyncio.open_connection(self.host, self.port, ssl=ssl.SSLContext())

        # Connect and get-status
        await self.connect()
        await self.get_status()
        await self.ping()

        self.read_task = asyncio.create_task(self.recv_loop())

        while self.running:
            await asyncio.sleep(1)
            await self.stop()

    async def stop(self):
        if self.dmr_session:
            await self.stop_drm()
        await self.disconnect()

        self.running = False
        self.writer.close()
        await self.writer.wait_closed()

    async def send_message(self, ns, data, dst=None):

        msg = make_msg(ns)
        msg.payload_utf8 = json.dumps(data, ensure_ascii=False).encode('utf-8')
        if dst:
            msg.destination_id = dst

        sz = pack(">I", msg.ByteSize())
        self.writer.write(sz + msg.SerializeToString())
        await self.writer.drain()

    async def send_messager(self, ns, data, dst=None):
        self.id_counts[dst] += 1
        data['requestId'] = self.id_counts[dst]
        await self.send_message(ns, data, dst)

    async def dmr_message(self, data, ns='media'):
        assert self.dmr_session
        await self.send_messager(ns, data, dst=self.dmr_session)

    async def ping(self):
        await self.send_message('heartbeat', {"type": "PING"})

    async def pong(self, _):
        await self.send_message('heartbeat', {"type": "PONG"})

    async def get_status(self, dst=None):
        await self.send_messager('receiver', {"type": "GET_STATUS"}, dst)

    async def disconnect(self, dst=None):
        await self.send_message('con', {"type": "CLOSE", "origin": {}}, dst)

    async def connect(self, dst=None):
        await self.send_message('con', {"type": "CONNECT", "origin": {},
                                        "userAgent": "dmcast",
                                        "senderInfo": {}}, dst)

    async def launch_dmr(self):
        if self.dmr_session is None:
            await self.send_messager('receiver', {"type": "LAUNCH", "appId": DEFAULT_APP})
            await self.get_status()

    async def stop_drm(self):
        await self.dmr_message({"type": "STOP"}, 'receiver')
        self.dmr_session = None

    # Media

    async def media_load(self, media):
        assert self.dmr_session

        PLAY = {
            "type": "LOAD",
            "sessionId": self.dmr_session,
            "autoplay": True,
            "customData": {},
            "media": media}
        await self.dmr_message('media', PLAY)

    async def media_status(self):
        await self.dmr_message({"type": "GET_STATUS"})

    async def media_play(self, msid):
        await self.dmr_message({"type": "PLAY", "mediaSessionId": msid})

    async def media_pause(self, msid):
        await self.dmr_message({"type": "PAUSE", 'mediaSessionId': msid})

    async def media_stop(self, msid):
        await self.dmr_message({"type": "STOP", 'mediaSessionId': msid})

    # Device

    async def device_volume(self, volume):
        v = min(max(0, volume), 1)
        await self.send_messager('receiver', {"type": "SET_VOLUME", "volume": {"level": v}})

    async def device_mute(self, muted):
        await self.send_messager('receiver', {"type": "SET_VOLUME", "volume": {"muted": muted}})



# example media
# media = {
    # "contentId": "https://your-domain.com/hello.mp3",
    # "streamType": "BUFFERED",
    # "contentType": "audio/mp3",
    # "metadata": {}
# }
