#!/usr/bin/python
'''
wopi_max_concurrency.py

A daemon pushing CERNBox WOPI monitoring data to Grafana.
TODO: make it a collectd plugin. References:
https://collectd.org/documentation/manpages/collectd-python.5.shtml
https://blog.dbrgn.ch/2017/3/10/write-a-collectd-python-plugin/
https://github.com/dbrgn/collectd-python-plugins

author: Giuseppe.LoPresti@cern.ch
CERN/IT-ST
'''

import fileinput
import socket
import time
import pickle
import struct
import datetime
import getopt
import sys

CARBON_TCPPORT = 2004
carbonHost = ''
verbose = False
prefix = 'cernbox.wopi.' + socket.gethostname().split('.')[0]
epoch = datetime.datetime(1970, 1, 1)


def usage(exitCode):
  '''prints usage'''
  print 'Usage : cat <logfile> | ' + sys.argv[0] + ' [-h|--help] -g|--grafanahost <hostname>'
  sys.exit(exitCode)

def send_metric(data):
  '''send data to grafana using the pickle protocol'''
  payload = pickle.dumps(data, protocol=2)
  header = struct.pack("!L", len(payload))
  message = header + payload
  sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
  sock.connect((carbonHost, CARBON_TCPPORT))
  sock.sendall(message)
  sock.close()

def get_wopi_metrics(data):
  '''Parse WOPI usage metrics'''
  for line in data:
    if data.isfirstline():
      logdate = line.split('T')[0].split('-')    # keeps the date until 'T', splits
      timestamp = (datetime.datetime(int(logdate[0]), int(logdate[1]), int(logdate[2]), 1, 0, 0) - epoch).total_seconds() + time.altzone
      maxconc = 0
      tokens = set()
    try:
      if 'msg="Lock"' in line and 'INFO' in line and 'result' not in line:
        # +1 for this acc. token
        l = line.split()
        tok = l[-1].split('=')[1]
        tokens.add(tok)
        if len(tokens) > maxconc:
          maxconc += 1
      if 'msg="Unlock"' in line and 'INFO' in line:
        # -1 for this acc. token
        l = line.split()
        tok = l[-1].split('=')[1]
        try:
          tokens.remove(tok)
        except KeyError:
          pass
    except Exception:
      if verbose:
        print 'Error occurred at line: %s' % line
      raise

  if 'tok' not in locals():
    # the file was empty, nothing to do
    return
  # prepare data for grafana
  output = []
  output.append(( prefix + '.maxconc', (int(timestamp), maxconc) ))
  send_metric(output)
  if verbose:
    print output


# first parse options
try:
  options, args = getopt.getopt(sys.argv[1:], 'hvg:', ['help', 'verbose', 'grafanahost'])
except Exception, e:
  print e
  usage(1)
for f, v in options:
  if f == '-h' or f == '--help':
    usage(0)
  elif f == '-v' or f == '--verbose':
    verbose = True
  elif f == '-g' or f == '--grafanahost':
    carbonHost = v
  else:
    print "unknown option : " + f
    usage(1)
if carbonHost == '':
  print 'grafanahost option is mandatory'
  usage(1)
# now parse input and collect statistics
try:
  get_wopi_metrics(fileinput.input('-'))
except Exception, e:
  print 'Error with collecting metrics:', e
  if verbose:
    raise

