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


PLAYING_ACTION = 0
PLAY_NEW = 1

class Chromecast():

    def __init__(self, host, port):
        self.host = host
        self.port = port
        self.running = True
        self.id_counts = collections.Counter()
        self.desired_actions = []
        self.desired_media = None
        self.dispatch = {
                'RECEIVER_STATUS': self.handle_receiver_status,
                'MEDIA_STATUS': self.handle_media_status,
                'PING': self.pong
                }

    async def handle_media_status(self, data):

        first_idx = None
        for idx, a in enumerate(self.desired_actions):
            if a[0] == PLAYING_ACTION:
                first_idx = idx
                break
        if first_idx is not None:

            _, action = self.desired_actions.pop(first_idx)
            msid = data['status'][0]['mediaSessionId']
            await action(msid, self.session)
        else:
            await self.state(data)

    async def state(self, data):
        print(json.dumps(data, indent=2))

    async def handle_receiver_status(self, msg):

        if 'applications' in msg['status']:
            try:
                da = min(filter(lambda a: a['appId'] == DEFAULT_APP, msg['status']['applications']))
                self.session = da['sessionId']
                print(f'DEFAULT App running, connecting now {self.session}')
                await self.connect(dst=self.session)

                if self.desired_media:
                    await self.get_status(dst=self.session)
                    await self.play_media(self.desired_media)
                    self.desired_media = None
                else:
                    await self.media_status()

                await self.ping()
            except ValueError:

                print('DEFAULT App not running')
                if self.desired_media:
                    print('Starting default app')
                    await self.launch_app()
                    await self.get_status()
                    await self.ping()

        else:
            await self.state({'playing': False})

    async def recv_loop(self):

        while self.running:
            data = await self.reader.read(4)
            read_len = unpack(">I", data)[0]

            data = await self.reader.read(read_len)

            response = make_msg()
            response.ParseFromString(data)
            resp = json.loads(response.payload_utf8)
            print(response.namespace)
            print(json.dumps(resp, indent=2))
            handler = self.dispatch.get(resp['type'])
            if handler:
                await handler(resp)

        self.reader.close()

    async def start(self):
        self.reader, self.writer = await asyncio.open_connection(self.host, self.port, ssl=ssl.SSLContext())

        # Connect and get-status
        await self.connect()
        await self.get_status()
        await self.ping()

        self.read_task = asyncio.create_task(self.recv_loop())

        while True:
            await asyncio.sleep(1)

    async def send_messager(self, ns, data, dst=None):
        self.id_counts[dst] += 1
        data['requestId'] = self.id_counts[dst]
        await self.send_message(ns, data, dst)

    async def send_message(self, ns, data, dst=None):

        msg = make_msg(ns)
        msg.payload_utf8 = json.dumps(data, ensure_ascii=False).encode('utf-8')
        if dst:
            msg.destination_id = dst

        sz = pack(">I", msg.ByteSize())
        self.writer.write(sz + msg.SerializeToString())
        await self.writer.drain()

    async def ping(self):
        await self.send_message('heartbeat', {"type": "PING"})

    async def pong(self, _):
        await self.send_message('heartbeat', {"type": "PONG"})

    async def connect(self, dst=None):
        await self.send_message('con', {"type": "CONNECT",
                                        "origin": {},
                                        "userAgent": "dmcast",
                                        "senderInfo": {}}, dst)

    async def get_status(self, dst=None):
        await self.send_messager('receiver', {"type": "GET_STATUS"}, dst)

    async def launch_app(self):
        await self.send_messager('receiver',
                                 {"type": "LAUNCH", "appId": DEFAULT_APP})

    async def media_status(self):
        await self.send_messager('media', {"type": "GET_STATUS"}, self.session)

    async def media_play(self, msid):
        await self.send_messager('media',
                                 {"type": "PLAY",
                                  "mediaSessionId": msid},
                                 self.session)

    async def media_pause(self, msid):
        await self.send_messager('media',
                                 {"type": "PAUSE",
                                  'mediaSessionId': msid},
                                 self.session)

    async def media_stop(self, msid):
        await self.send_messager('media',
                                 {"type": "STOP",
                                  'mediaSessionId': msid},
                                 self.session)

    async def app_stop(self):
        await self.send_message('receiver',
                                {"type": "STOP"},
                                self.session)
        self.session = None

    async def set_volume(self, volume):
        volume = min(max(0, volume), 1)
        await self.send_messager('receiver',
                                 {"type": "SET_VOLUME",
                                  "volume": {"level": volume}},
                                 self.session)

    async def set_volume_muted(self, muted):
        await self.send_messager('receiver',
                                 {"type": "SET_VOLUME",
                                  "volume": {"muted": muted}},
                                 self.session)

    async def disconnect(self, dst):
        await self.send_message('con', {"type": "CLOSE", "origin": {}}, dst)

    async def play_media(self, media):

        PLAY = {
            "type": "LOAD",
            "sessionId": self.session,
            "autoplay": True,
            "customData": {},
            "media": media}
        await self.send_messager('media', PLAY, dst=self.session)


# c.desired_media = media
# c.desired_actions = [[PLAYING_ACTION, c.stop]]

# Chromecast states
# DMR not running
#  Get notified of anything running

# DMR running (session reflects this connection)
# Play,pause, stop media
# mute / volume

# Play media at specified volume




