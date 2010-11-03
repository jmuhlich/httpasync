# httpasync.py
#
# Copyright 2010 Jeremy Muhlich
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
httpasync is a self-contained pure-Python library to perform asynchronous
HTTP requests.  It depends only on core Python packages, is cross-platform,
and has been tested with Python versions as early as 2.3.  It is useful in
cases where a more complete solution such as Twisted would be overkill.

Example usage:

import httpasync
import urllib2
import time
request = urllib2.Request('http://www.google.com/')
client = httpasync.HTTPClientAsync(request)
states = client.async_updater()
for state in states:
    time.sleep(0.1)
    print '%s...' % state
body = client.response.read()
print "Retrieved %d bytes, beginning:\\n%s..." % (len(body), body[0:50])

"""

import urllib2
import urllib
import httplib
import socket
import os
import errno
import time
import cStringIO


CS_CONNECT = 'connecting'
CS_SEND = 'sending'
CS_RECV = 'receiving'
CS_CLOSE = 'closed'

WINDOW_LEN = 2**16


class HTTPConnectionAsync(httplib.HTTPConnection):
    """HTTPConnection subclass for use with async client class.
    Buffers sent data instead of writing it to a socket."""

    def __init__(self, host, port=None, strict=None):
        httplib.HTTPConnection.__init__(self, host, port, strict)
        self._send_buffer = cStringIO.StringIO()
        self.response_class = HTTPResponseAsync

    def send(self, str):
        """Send 'str' to the server. (Actually just store it to send later)"""
        self._send_buffer.write(str)

    def close(self):
        # prevent main httplib code from messing with our StringIO masquerading
        # as a socket (see HTTPResponseAsync)
        pass


class HTTPResponseAsync(httplib.HTTPResponse):
    """HTTPResponse subclass for use with async client class.  Just
    overrides __init__ to support the fake socket object our
    Connection class uses."""

    def __init__(self, sock, debuglevel=0, strict=0, method=None):
        # parent __init__ calls sock.makefile, but sock here is really a StringIO
        # which doesn't have a makefile method. so we need to patch up self.fp
        # after running the parent __init__ with a dummy socket object.
        dummy_sock = socket.socket()
        httplib.HTTPResponse.__init__(self, dummy_sock, debuglevel, strict, method)
        self.fp = sock


class TimeoutError(Exception):
    """Client exceeded its timeout when servicing an HTTP request."""

    pass


class HTTPClientAsync:
    """Pure-python asynchronous HTTP client."""

    def __init__(self, request, timeout=5):
        """request: a urllib2.Request object
        timeout: how long to allow for request servicing, in seconds"""

        self.request = request
        self.response = None
        self.timeout = timeout
        self.conn = None
        self.sock = None
        self._build_request_data()

    def _build_request_data(self):
        # Assembles the raw HTTP request in a buffer for sending later

        # copied from urllib2.AbstractHTTPHandler.do_open and modified
        # FIXME: there must be a simpler way to do this, maybe with h.request()
        req = self.request
        host = req.get_host()
        if not host:
            raise URLError('no host given')

        h = self.conn = HTTPConnectionAsync(host) # will parse host:port
        if req.has_data():
            data = req.get_data()
            h.putrequest('POST', req.get_selector(), skip_host=1)
            if not 'Content-type' in req.headers:
                h.putheader('Content-type',
                            'application/x-www-form-urlencoded')
            if not 'Content-length' in req.headers:
                h.putheader('Content-length', '%d' % len(data))
        else:
            h.putrequest('GET', req.get_selector(), skip_host=1)

        scheme, sel = urllib.splittype(req.get_selector())
        sel_host, sel_path = urllib.splithost(sel)
        h.putheader('Host', sel_host or host)
        h.putheader('User-agent', 'Python-urllib/2.1')
        # prevent keepalives
        h.putheader('Connection', 'close')
        # "parent" code excised here since we don't implement the handler hierarchy
        for k, v in req.headers.items():
            h.putheader(k, v)
        h.endheaders()
        if req.has_data():
            h.send(data)

    def _check_timeout(self):
        if time.time() > self.deadline:
            raise TimeoutError('connection timed out')

    def async_updater(self):
        """Returns a generator that, when iterated, advances the state
        of the request without blocking on any socket calls."""

        # The basic pattern here is to perform infinite loops around a
        # series of non-blocking socket calls, yielding if the call is
        # incomplete and breaking when it does complete.  The yield is
        # placed at the bottom of each loop to reduce the number of
        # iterations required (for example after connect succeeds,
        # send usually finishes instantly due to buffering in the
        # kernel and never even has to loop, proceeding directly to
        # recv).  Note the complete/incomplete conditions are a little
        # different for each call and generally require trapping
        # certain error numbers.  Real, unexpected socket errors are
        # passed on.

        sock = socket.socket()
        sock.setblocking(False)  # this is obviously critical
        self.deadline = time.time() + self.timeout

        # connect
        while True:
            self._check_timeout()  # this should be done at the beginning of each loop
            try:
                sock.connect((self.conn.host, self.conn.port))
            except socket.error, e:
                if e[0] == errno.EISCONN:
                    break
                elif e[0] == errno.EWOULDBLOCK or e[0] == errno.EINPROGRESS or e[0] == errno.EALREADY or \
                        (os.name == 'nt' and e[0] == errno.WSAEINVAL):
                    pass
                else:
                    raise
            yield CS_CONNECT
        error = sock.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR)
        if error:
            # TODO: determine when this case can actually happen
            raise socket.error((error,))

        # send
        send_buf_data = self.conn._send_buffer.getvalue()
        send_buf = buffer(send_buf_data, 0, WINDOW_LEN)
        total_count = 0
        while True:
            self._check_timeout()
            try:
                count = sock.send(send_buf)
                total_count += count
                if total_count < len(send_buf_data):
                    send_buf = buffer(send_buf_data, total_count, WINDOW_LEN)
                else:
                    break
            except socket.error, e:
                if e[0] == errno.EWOULDBLOCK or e[0] == errno.ENOBUFS:
                    pass
                else:
                    raise
            yield CS_SEND

        # receive
        recv_buf = []
        while True:
            self._check_timeout()
            try:
                data = sock.recv(WINDOW_LEN)
                if len(data) > 0:
                    recv_buf.append(data)
                else:
                    break
            except socket.error, e:
                if e[0] == errno.EWOULDBLOCK:
                    pass
                else:
                    raise
            yield CS_RECV

        # close, and parse response data
        sock.close()
        # trick getresponse() into reading from this buffer instead of a real socket
        self.conn.sock = cStringIO.StringIO(''.join(recv_buf))
        h_response = self.conn.getresponse()
        # re-wrap body to let httplib deal with chunked encoding, etc
        h_response.fp = cStringIO.StringIO(h_response.read())
        code, msg, hdrs = h_response.status, h_response.reason, h_response.msg
        url = self.request.get_full_url()
        if code == 200:
            self.response = urllib.addinfourl(h_response.fp, hdrs, url)
        else:
            # FIXME: should we raise an error or just return a response with a non-200 code?
            # (does the urllib response class thing even support that?)
            raise urllib2.HTTPError(url, code, msg, hdrs, h_response.fp)
        yield CS_CLOSE


def test_fetch(url):
    """Fetch a URL, walking through each state, and display the response."""
    print "Requesting", url
    print
    request = urllib2.Request(url)
    client = HTTPClientAsync(request)
    states = client.async_updater()
    try:
        for state in states:
            time.sleep(0.1)
            print state
        body = client.response.read()
        print "\nRetrieved %d bytes, beginning:\n\n%s" % (len(body), body[0:50])
    except urllib2.HTTPError, e:
        print "\nServer returned an error:\n%d %s" % (e.code, e.msg)


if __name__ == '__main__':
    # provide a URL of your choice on the command line to use that for the
    # test instead of python.org
    import sys
    if len(sys.argv) >= 2:
        url = sys.argv[1]
    else:
        url = 'http://www.python.org/'
    test_fetch(url)
