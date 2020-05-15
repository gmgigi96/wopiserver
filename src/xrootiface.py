'''
xrootiface.py

eos-xrootd interface for the IOP WOPI server

Author: Giuseppe.LoPresti@cern.ch, CERN/IT-ST
Contributions: Michael.DSilva@aarnet.edu.au
'''

import time
from XRootD import client as XrdClient
from XRootD.client.flags import OpenFlags, QueryCode


# module-wide state
config = None
log = None
xrdfs = {}    # this is to map each endpoint [string] to its XrdClient
defaultstorage = None
homepath = None


def _getxrdfor(endpoint):
  '''Look up the xrootd client for the given endpoint, create it if missing.
     Supports "default" for the defaultstorage endpoint.'''
  global xrdfs           # pylint: disable=global-statement
  global defaultstorage  # pylint: disable=global-statement
  if endpoint == 'default':
    return xrdfs[defaultstorage]
  try:
    return xrdfs[endpoint]
  except KeyError:
    # not found, create it
    xrdfs[endpoint] = XrdClient.FileSystem(endpoint)
    return xrdfs[endpoint]


def _geturlfor(endpoint):
  '''Look up the URL for a given endpoint: "default" corresponds to the defaultstorage one'''
  if endpoint == 'default':
    return defaultstorage
  return endpoint


def _eosargs(userid, atomicwrite=0, bookingsize=0):
  '''Split userid into uid,gid and generate extra EOS-specific arguments for the xroot URL'''
  try:
    # try to assert that userid must follow a '%d:%d' format
    userid = userid.split(':')
    if len(userid) != 2:
      raise ValueError
    ruid = int(userid[0])
    rgid = int(userid[1])
    return '?eos.ruid=' + ruid + '&eos.rgid=' + rgid + ('&eos.atomic=1' if atomicwrite else '') + \
            (('&eos.bookingsize='+str(bookingsize)) if bookingsize else '') + '&eos.app=wopi'
  except (ValueError, IndexError):
    raise ValueError('Only Unix-based userid is supported with xrootd storage')


def _xrootcmd(endpoint, cmd, subcmd, userid, args):
  '''Perform the <cmd>/<subcmd> action on the special /proc/user path on behalf of the given userid.
     Note that this is entirely EOS-specific.'''
  with XrdClient.File() as f:
    url = _geturlfor(endpoint) + '//proc/user/' + _eosargs(userid) + '&mgm.cmd=' + cmd + \
          ('&mgm.subcmd=' + subcmd if subcmd else '') + '&' + args
    tstart = time.clock()
    rc, statInfo_unused = f.open(url, OpenFlags.READ)
    tend = time.clock()
    log.info('msg="Invoked _xrootcmd" cmd="%s%s" url="%s" elapsedTimems="%.1f"' %
             (cmd, ('/' + subcmd if subcmd else ''), url, (tend-tstart)*1000))
    res = f.readline().decode('utf-8').strip('\n').split('&')
    if len(res) == 3:    # we may only just get stdout: in that case, assume it's all OK
      rc = res[2]
      rc = rc[rc.find('=')+1:]
      if rc != '0':
        # failure: get info from stderr, log and raise
        msg = res[1][res[1].find('=')+1:]
        log.info('msg="Error with xroot command" cmd="%s" subcmd="%s" args="%s" error="%s" rc="%s"' % \
                 (cmd, subcmd, args, msg, rc.strip('\00')))
        raise IOError(msg)
  # all right, return everything that came in stdout
  return res[0][res[0].find('stdout=')+7:]


def _getfilepath(filepath):
  '''map the given filepath into the target namespace by prepending the homepath (see storagehomepath in wopiserver.conf)'''
  return homepath + filepath


def init(inconfig, inlog):
  '''Init module-level variables'''
  global config         # pylint: disable=global-statement
  global log            # pylint: disable=global-statement
  global defaultstorage # pylint: disable=global-statement
  global homepath       # pylint: disable=global-statement
  config = inconfig
  log = inlog
  defaultstorage = config.get('xroot', 'storageserver')
  # prepare the xroot client for the default storageserver
  _getxrdfor(defaultstorage)
  if config.has_option('xroot', 'storagehomepath'):
    homepath = config.get('xroot', 'storagehomepath')
  else:
    homepath = ''


def stat(endpoint, filepath, userid):
  '''Stat a file via xroot on behalf of the given userid, and returns (size, mtime). Uses the default xroot API.'''
  filepath = _getfilepath(filepath)
  tstart = time.clock()
  rc, statInfo = _getxrdfor(endpoint).stat(filepath + _eosargs(userid))
  tend = time.clock()
  log.info('msg="Invoked stat" filepath="%s" elapsedTimems="%.1f"' % (filepath, (tend-tstart)*1000))
  if statInfo is None:
    raise IOError(rc.message.strip('\n'))
  return {'size': statInfo.size, 'mtime': statInfo.modtime}


def statx(endpoint, filepath, userid):
  '''Get extended stat info (inode, filepath, userid, size, mtime) via an xroot opaque query on behalf of the given userid'''
  tstart = time.clock()
  rc, rawinfo = _getxrdfor(endpoint).query(QueryCode.OPAQUEFILE, _getfilepath(filepath) + _eosargs(userid) + '&mgm.pcmd=stat')
  tend = time.clock()
  log.info('msg="Invoked stat" filepath="%s" elapsedTimems="%.1f"' % (_getfilepath(filepath), (tend-tstart)*1000))
  if '[SUCCESS]' not in str(rc):
    raise IOError(str(rc).strip('\n'))
  rawinfo = str(rawinfo)
  if 'retc=' in rawinfo:
    raise IOError(rawinfo.strip('\n'))
  statxdata = rawinfo.split()
  return {'inode': statxdata[2],
          'filepath': filepath,
          'userid': str(statxdata[5]) + ':' + str(statxdata[6]),
          'size': int(statxdata[8]),
          'mtime': statxdata[12]}


def setxattr(endpoint, filepath, userid, key, value):
  '''Set the extended attribute <key> to <value> via a special open on behalf of the given userid'''
  _xrootcmd(endpoint, 'attr', 'set', userid, 'mgm.attr.key=' + key + '&mgm.attr.value=' + str(value) + \
            '&mgm.path=' + _getfilepath(filepath))


def getxattr(endpoint, filepath, userid, key):
  '''Get the extended attribute <key> via a special open on behalf of the given userid'''
  res = _xrootcmd(endpoint, 'attr', 'get', userid, 'mgm.attr.key=' + key + '&mgm.path=' + _getfilepath(filepath))
  # if no error, the response comes in the format <key>="<value>"
  try:
    return res.split('"')[1]
  except IndexError:
    log.warning('msg="Failed to getxattr" filepath="%s" key="%s" res="%s"' % (filepath, key, res))
    return None


def rmxattr(endpoint, filepath, userid, key):
  '''Remove the extended attribute <key> via a special open on behalf of the given userid'''
  filepath = _getfilepath(filepath)
  _xrootcmd(endpoint, 'attr', 'rm', userid, 'mgm.attr.key=' + key + '&mgm.path=' + filepath)


def readfile(endpoint, filepath, userid):
  '''Read a file via xroot on behalf of the given userid. Note that the function is a generator, managed by Flask.'''
  log.debug('msg="Invoking readFile" filepath="%s"' % filepath)
  with XrdClient.File() as f:
    fileurl = _geturlfor(endpoint) + '/' + homepath + filepath + _eosargs(userid)
    tstart = time.clock()
    rc, statInfo_unused = f.open(fileurl, OpenFlags.READ)
    tend = time.clock()
    if not rc.ok:
      # the file could not be opened: check the case of ENOENT and log it as info to keep the logs cleaner
      if 'No such file or directory' in rc.message:
        log.info('msg="File not found on read" filepath="%s"' % filepath)
      else:
        log.warning('msg="Error opening the file for read" filepath="%s" code="%d" error="%s"' % \
                    (filepath, rc.shellcode, rc.message.strip('\n')))
      # as this is a generator, we yield the error string instead of the file's contents
      yield IOError(rc.message)
    else:
      log.info('msg="File open for read" filepath="%s" elapsedTimems="%.1f"' % (filepath, (tend-tstart)*1000))
      chunksize = config.getint('io', 'chunksize')
      rc, statInfo = f.stat()
      chunksize = min(chunksize, statInfo.size-1)
      # the actual read is buffered and managed by the Flask server
      for chunk in f.readchunks(offset=0, chunksize=chunksize):
        yield chunk


def writefile(endpoint, filepath, userid, content, noversion=0):
  '''Write a file via xroot on behalf of the given userid. The entire content is written
     and any pre-existing file is deleted (or moved to the previous version if supported).
     If noversion=1, the write explicitly disables versioning: this is useful for lock files.'''
  size = len(content)
  log.debug('msg="Invoking writeFile" filepath="%s" size="%d"' % (filepath, size))
  f = XrdClient.File()
  tstart = time.clock()
  rc, statInfo_unused = f.open(_geturlfor(endpoint) + '/' + homepath + filepath + _eosargs(userid, 1, size) + \
                               ('&sys.versioning=0' if noversion else ''), OpenFlags.DELETE)
  tend = time.clock()
  log.info('msg="File open for write" filepath="%s" elapsedTimems="%.1f"' % (filepath, (tend-tstart)*1000))
  if not rc.ok:
    log.warning('msg="Error opening the file for write" filepath="%s" error="%s"' % (filepath, rc.message.strip('\n')))
    raise IOError(rc.message.strip('\n'))
  # write the file. In a future implementation, we should find a way to only update the required chunks...
  rc, statInfo_unused = f.write(content, offset=0, size=size)
  if not rc.ok:
    log.warning('msg="Error writing the file" filepath="%s" error="%s"' % (filepath, rc.message.strip('\n')))
    raise IOError(rc.message.strip('\n'))
  rc, statInfo_unused = f.truncate(size)
  if not rc.ok:
    log.warning('msg="Error truncating the file" filepath="%s" error="%s"' % (filepath, rc.message.strip('\n')))
    raise IOError(rc.message.strip('\n'))
  rc, statInfo_unused = f.close()
  if not rc.ok:
    log.warning('msg="Error closing the file" filepath="%s" error="%s"' % (filepath, rc.message.strip('\n')))
    raise IOError(rc.message.strip('\n'))


def renamefile(endpoint, origfilepath, newfilepath, userid):
  '''Rename a file via a special open from origfilepath to newfilepath on behalf of the given userid.'''
  _xrootcmd(endpoint, 'file', 'rename', userid, 'mgm.path=' + _getfilepath(origfilepath) + \
            '&mgm.file.source=' + _getfilepath(origfilepath) + '&mgm.file.target=' + _getfilepath(newfilepath))


def removefile(endpoint, filepath, userid, force=0):
  '''Remove a file via a special open on behalf of the given userid.
     If force=1 or True, then pass the f option, that is skip the recycle bin.
     This is useful for lock files, but as it requires root access the userid is overridden.'''
  if force:
    userid = '0:0'
  _xrootcmd(endpoint, 'rm', None, userid, 'mgm.path=' + _getfilepath(filepath) + \
                                     ('&mgm.option=f' if force else ''))
