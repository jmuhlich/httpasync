=========
httpasync
=========

:Author: `Jeremy Muhlich <jmuhlich@bitflood.org>`
:Web: https://github.com/jmuhlich/httpasync
:Git: git clone https://github.com/jmuhlich/httpasync.git

Pure Python asynchronous HTTP client library based on httplib

Overview
========

httpasync is a self-contained pure-Python library to perform asynchronous
HTTP requests.  It depends only on core Python packages, is cross-platform,
and has been tested with Python versions as early as 2.3.  It is useful in
cases where a more complete solution such as Twisted would be overkill.

Example usage::

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
  print "Retrieved %d bytes, beginning:\n%s..." % (len(body), body[0:50])
