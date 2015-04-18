# adapted from
# https://github.com/leporo/tornado-redis/blob/master/demos/websockets
from json import loads

import toredis
from tornado.web import authenticated
from tornado.websocket import WebSocketHandler
from tornado.gen import engine, Task

from moi import r_client
from qiita_pet.handlers.base_handlers import BaseHandler
from qiita_db.analysis import Analysis


class MessageHandler(WebSocketHandler):
    def __init__(self, *args, **kwargs):
        super(MessageHandler, self).__init__(*args, **kwargs)
        # The redis server
        self.r_client = r_client

        # The toredis server that allows event-based message handling
        self.toredis = toredis.Client()
        self.toredis.connect()

        self.channel = None
        self.channel_messages = None

    def get_current_user(self):
        user = self.get_secure_cookie("user")
        if user is None:
            raise ValueError("No user associated with the websocket!")
        else:
            return user.strip('" ')

    @authenticated
    def on_message(self, msg):
        # When the websocket receives a message from the javascript client,
        # parse into JSON
        msginfo = loads(msg)

        # Determine which Redis communication channel the server needs to
        # listen on
        self.channel = msginfo.get('user', None)

        if self.channel is not None:
            self.channel_messages = '%s:messages' % self.channel
            self.listen()

    def listen(self):
        # Attach a callback on the channel to listen too. This callback is
        # executed when anything is placed onto the channel.
        self.toredis.subscribe(self.channel, callback=self.callback)

        # Potential race-condition where a separate process may have placed
        # messages into the queue before we've been able to attach listen.
        oldmessages = self.r_client.lrange(self.channel_messages, 0, -1)
        if oldmessages is not None:
            for message in oldmessages:
                self.write_message(message)

    def callback(self, msg):
        message_type, channel, payload = msg

        # if a compute process wrote to the Redis channel that we are
        # listening too, and if it is actually a message, send the payload to
        # the javascript client via the websocket
        if channel == self.channel and message_type == 'message':
            self.write_message(payload)

    @engine
    def on_close(self):
        yield Task(self.toredis.unsubscribe, self.channel)
        self.r_client.delete('%s:messages' % self.channel)
        self.redis.disconnect()


class SelectedSocketHandler(WebSocketHandler, BaseHandler):
    """Websocket for removing samples on default analysis display page"""
    @authenticated
    def on_message(self, msg):
        # When the websocket receives a message from the javascript client,
        # parse into JSON
        msginfo = loads(msg)
        default = Analysis(self.current_user.default_analysis)
        if msginfo['samples']:
            default.remove_samples([msginfo['proc_data']], msginfo['samples'])
        else:
            default.remove_samples([msginfo['proc_data']])

        self.write_message('true')


class SelectSamplesHandler(WebSocketHandler, BaseHandler):
    """Websocket for selecting and deselecting samples on list studies page"""
    @authenticated
    def on_message(self, msg):
        """Selects or deselects samples on a message from the user

        Parameters
        ----------
        msg : JSON str
            Message containing sample and prc_data information, in the form
            {'action': action, 'proc_data': [p1, ...], 'samples': [[s1], ...]}
            Where proc data in p1 matches to samples list in s1
        """
        # When the websocket receives a message from the javascript client,
        # parse into JSON
        msginfo = loads(msg)
        num_samples = sum(len(x) for x in msginfo["samples"])
        default = Analysis(self.current_user.default_analysis)
        if msginfo["action"] == "select":
            # match proc data with samples and add them
            select = {}
            for pid, samples in zip(msginfo["proc_data"], msginfo["samples"]):
                select[pid] = samples
                default.add_samples(select)
            self.write_message("%d samples selected" % num_samples)
        elif msginfo["action"] == "deselect":
            for pid, samples in zip(msginfo["proc_data"], msginfo["samples"]):
                default.remove_samples([pid], samples)
            self.write_message("%d samples removed" % num_samples)
        else:
            raise ValueError("Unknown action: %s" % msginfo["action"])
