"""Loopback Bridge client for recorded blade geometry; pulls one frame per step."""
from collections import deque
import ipaddress
from pathlib import Path
import socket
import time
from .messages import FrameDecoder, encode_message
from .tip_replay import sample_from_snapshot


class TipBridgeClient:
    def __init__(self,address,package,scene=None,timeout=15):
        host,port=address.rsplit(':',1)
        if not ipaddress.ip_address(host).is_loopback:raise ValueError('Bridge must be loopback')
        self.socket=socket.create_connection((host,int(port)),timeout=timeout)
        self.decoder=FrameDecoder();self.messages=deque();self.sequence=0;self.received=0
        self.session='';self.owned=False;self.started=False;self.finished=False;self.timeout=timeout
        self.expected_package=str((Path(package).resolve()/'manifest.json'))
        try:
            self._send('hello',dict(supported_versions=[1],capabilities=[],resume_session_id=None))
            ack=self._receive()
            if ack['type']!='hello_ack':raise ValueError('Expected Bridge handshake')
            self.session=ack['session_id']
            if ack['payload']['run_status'] not in ('READY','STOPPED'):
                raise ValueError('Bridge already has an active run; stop it before recorded tip preview')
            options={'tip_package':str(Path(package).resolve())}
            if scene:options['scene']=str(Path(scene).resolve())
            self.command('run.start',{'mode':'replay','options':options})
            self.owned=True
            self.command('run.pause')
        except BaseException:
            self.close()
            raise

    def _send(self,kind,payload):
        self.socket.sendall(encode_message(dict(protocol_version=1,type=kind,session_id=self.session,
                                                sequence=self.sequence,payload=payload)))
        self.sequence+=1

    def command(self,name,arguments=None):
        self._send('command',{'command':name,'arguments':arguments or {}})

    def _receive(self):
        deadline=time.monotonic()+self.timeout
        while not self.messages:
            self.socket.settimeout(max(.01,deadline-time.monotonic()))
            data=self.socket.recv(65536)
            if not data:raise ConnectionError('Bridge disconnected')
            self.messages.extend(self.decoder.feed(data))
            if len(self.messages)>128:raise ValueError('Bridge queue overflow')
            if time.monotonic()>deadline:raise TimeoutError('Bridge response timed out')
        message=self.messages.popleft()
        if message['sequence']<=self.received:raise ValueError('Out-of-order Bridge sequence')
        if self.session and message['session_id']!=self.session:raise ValueError('Bridge session changed')
        self.received=message['sequence']
        if message['type']=='error':raise RuntimeError(message['payload']['message'])
        if message['type']=='lifecycle' and message['payload']['run_status']=='FAILED':
            raise RuntimeError(message['payload']['reason'])
        return message

    def next_sample(self):
        if self.finished:return None
        if self.started:self.command('run.step')
        while True:
            message=self._receive()
            if message['type']=='snapshot':
                sample=sample_from_snapshot(message['payload'])
                if sample['provenance'].get('file')!=self.expected_package:
                    raise ValueError('Bridge returned a different result package')
                self.started=True
                return sample
            if message['type']=='lifecycle' and message['payload']['run_status']=='STOPPED':
                self.finished=True
                return None

    def close(self):
        if self.socket is None:return
        try:
            if self.owned and not self.finished:
                self.command('run.stop',{'timeout_seconds':2})
                while True:
                    message=self._receive()
                    if message['type']=='lifecycle' and message['payload']['run_status']=='STOPPED':break
        finally:
            self.socket.close();self.socket=None;self.owned=False
