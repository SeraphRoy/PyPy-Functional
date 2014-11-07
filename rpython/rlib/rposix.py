import os
import sys
from rpython.rtyper.lltypesystem.rffi import CConstant, CExternVariable, INT
from rpython.rtyper.lltypesystem import lltype, ll2ctypes, rffi
from rpython.rtyper.tool import rffi_platform
from rpython.translator.tool.cbuild import ExternalCompilationInfo
from rpython.rlib.rarithmetic import intmask
from rpython.rlib.objectmodel import (
    specialize, enforceargs, register_replacement_for)
from rpython.rlib import jit
from rpython.translator.platform import platform
from rpython.rlib import rstring

_WIN32 = sys.platform.startswith('win')
UNDERSCORE_ON_WIN32 = '_' if _WIN32 else ''

class CConstantErrno(CConstant):
    # these accessors are used when calling get_errno() or set_errno()
    # on top of CPython
    def __getitem__(self, index):
        assert index == 0
        try:
            return ll2ctypes.TLS.errno
        except AttributeError:
            raise ValueError("no C function call occurred so far, "
                             "errno is undefined")
    def __setitem__(self, index, value):
        assert index == 0
        ll2ctypes.TLS.errno = value

if os.name == 'nt':
    if platform.name == 'msvc':
        includes=['errno.h','stdio.h']
    else:
        includes=['errno.h','stdio.h', 'stdint.h']
    separate_module_sources =['''
        /* Lifted completely from CPython 3.3 Modules/posix_module.c */
        #include <malloc.h> /* for _msize */
        typedef struct {
            intptr_t osfhnd;
            char osfile;
        } my_ioinfo;
        extern __declspec(dllimport) char * __pioinfo[];
        #define IOINFO_L2E 5
        #define IOINFO_ARRAY_ELTS   (1 << IOINFO_L2E)
        #define IOINFO_ARRAYS 64
        #define _NHANDLE_           (IOINFO_ARRAYS * IOINFO_ARRAY_ELTS)
        #define FOPEN 0x01
        #define _NO_CONSOLE_FILENO (intptr_t)-2

        /* This function emulates what the windows CRT
            does to validate file handles */
        RPY_EXPORTED_FOR_TESTS int
        _PyVerify_fd(int fd)
        {
            const int i1 = fd >> IOINFO_L2E;
            const int i2 = fd & ((1 << IOINFO_L2E) - 1);

            static size_t sizeof_ioinfo = 0;

            /* Determine the actual size of the ioinfo structure,
             * as used by the CRT loaded in memory
             */
            if (sizeof_ioinfo == 0 && __pioinfo[0] != NULL) {
                sizeof_ioinfo = _msize(__pioinfo[0]) / IOINFO_ARRAY_ELTS;
            }
            if (sizeof_ioinfo == 0) {
                /* This should not happen... */
                goto fail;
            }

            /* See that it isn't a special CLEAR fileno */
                if (fd != _NO_CONSOLE_FILENO) {
                /* Microsoft CRT would check that 0<=fd<_nhandle but we can't do that.  Instead
                 * we check pointer validity and other info
                 */
                if (0 <= i1 && i1 < IOINFO_ARRAYS && __pioinfo[i1] != NULL) {
                    /* finally, check that the file is open */
                    my_ioinfo* info = (my_ioinfo*)(__pioinfo[i1] + i2 * sizeof_ioinfo);
                    if (info->osfile & FOPEN) {
                        return 1;
                    }
                }
            }
          fail:
            errno = EBADF;
            return 0;
        }
    ''',]
else:
    separate_module_sources = []
    includes=['errno.h','stdio.h']
errno_eci = ExternalCompilationInfo(
    includes=includes,
    separate_module_sources=separate_module_sources,
)

_get_errno, _set_errno = CExternVariable(INT, 'errno', errno_eci,
                                         CConstantErrno, sandboxsafe=True,
                                         _nowrapper=True, c_type='int')
# the default wrapper for set_errno is not suitable for use in critical places
# like around GIL handling logic, so we provide our own wrappers.

@jit.oopspec("rposix.get_errno()")
def get_errno():
    return intmask(_get_errno())

@jit.oopspec("rposix.set_errno(errno)")
def set_errno(errno):
    _set_errno(rffi.cast(INT, errno))

if os.name == 'nt':
    is_valid_fd = jit.dont_look_inside(rffi.llexternal(
        "_PyVerify_fd", [rffi.INT], rffi.INT,
        compilation_info=errno_eci,
        ))
    @enforceargs(int)
    def validate_fd(fd):
        if not is_valid_fd(fd):
            raise OSError(get_errno(), 'Bad file descriptor')
else:
    def is_valid_fd(fd):
        return 1

    @enforceargs(int)
    def validate_fd(fd):
        pass

def closerange(fd_low, fd_high):
    # this behaves like os.closerange() from Python 2.6.
    for fd in xrange(fd_low, fd_high):
        try:
            if is_valid_fd(fd):
                os.close(fd)
        except OSError:
            pass

if _WIN32:
    includes = ['io.h', 'sys/utime.h', 'sys/types.h']
else:
    includes = ['unistd.h',  'sys/types.h',
                'utime.h', 'sys/time.h', 'sys/times.h']
eci = ExternalCompilationInfo(
    includes=includes,
)

class CConfig:
    _compilation_info_ = eci
    HAVE_UTIMES = rffi_platform.Has('utimes')
    UTIMBUF = rffi_platform.Struct('struct %sutimbuf' % UNDERSCORE_ON_WIN32,
                                   [('actime', rffi.INT),
                                    ('modtime', rffi.INT)])
    if not _WIN32:
        CLOCK_T = rffi_platform.SimpleType('clock_t', rffi.INT)

        TMS = rffi_platform.Struct(
            'struct tms', [('tms_utime', rffi.INT),
                           ('tms_stime', rffi.INT),
                           ('tms_cutime', rffi.INT),
                           ('tms_cstime', rffi.INT)])

config = rffi_platform.configure(CConfig)
globals().update(config)

def external(name, args, result, **kwds):
    return rffi.llexternal(name, args, result, compilation_info=eci, **kwds)

c_dup = external(UNDERSCORE_ON_WIN32 + 'dup', [rffi.INT], rffi.INT)
c_dup2 = external(UNDERSCORE_ON_WIN32 + 'dup2', [rffi.INT, rffi.INT], rffi.INT)
c_open = external(UNDERSCORE_ON_WIN32 + 'open',
                  [rffi.CCHARP, rffi.INT, rffi.MODE_T], rffi.INT)
c_execv = external('execv', [rffi.CCHARP, rffi.CCHARPP], rffi.INT)
c_execve = external('execve',
                    [rffi.CCHARP, rffi.CCHARPP, rffi.CCHARPP], rffi.INT)
c_getlogin = external('getlogin', [], rffi.CCHARP, releasegil=False)

# Win32 specific functions
c_spawnv = external('spawnv',
                    [rffi.INT, rffi.CCHARP, rffi.CCHARPP], rffi.INT)
c_spawnve = external('spawnve',
                    [rffi.INT, rffi.CCHARP, rffi.CCHARPP, rffi.CCHARP],
                     rffi.INT)
# Win32 Unicode functions
c_wopen = external(UNDERSCORE_ON_WIN32 + 'wopen',
                   [rffi.CWCHARP, rffi.INT, rffi.MODE_T], rffi.INT)

#___________________________________________________________________
# Wrappers around posix functions, that accept either strings, or
# instances with a "as_bytes()" method.
# - pypy.modules.posix.interp_posix passes an object containing a unicode path
#   which can encode itself with sys.filesystemencoding.
# - but rpython.rtyper.module.ll_os.py on Windows will replace these functions
#   with other wrappers that directly handle unicode strings.
@specialize.argtype(0)
def _as_bytes(path):
    assert path is not None
    if isinstance(path, str):
        return path
    elif isinstance(path, unicode):
        # This never happens in PyPy's Python interpreter!
        # Only in raw RPython code that uses unicode strings.
        # We implement python2 behavior: silently convert to ascii.
        return path.encode('ascii')
    else:
        return path.as_bytes()

@specialize.argtype(0)
def _as_bytes0(path):
    """Crashes translation if the path contains NUL characters."""
    res = _as_bytes(path)
    rstring.check_str0(res)
    return res

@specialize.argtype(0)
def _as_unicode(path):
    assert path is not None
    if isinstance(path, unicode):
        return path
    else:
        return path.as_unicode()

@specialize.argtype(0)
def _as_unicode0(path):
    """Crashes translation if the path contains NUL characters."""
    res = _as_unicode(path)
    rstring.check_str0(res)
    return res

# Returns True when the unicode function should be called:
# - on Windows
# - if the path is Unicode.
if _WIN32:
    @specialize.argtype(0)
    def _prefer_unicode(path):
        if isinstance(path, str):
            return False
        elif isinstance(path, unicode):
            return True
        else:
            return path.is_unicode
else:
    @specialize.argtype(0)
    def _prefer_unicode(path):
        return False

@specialize.argtype(0)
def stat(path):
    return os.stat(_as_bytes(path))

@specialize.argtype(0)
def lstat(path):
    return os.lstat(_as_bytes(path))


@specialize.argtype(0)
def statvfs(path):
    return os.statvfs(_as_bytes(path))


@specialize.argtype(0)
def unlink(path):
    return os.unlink(_as_bytes(path))

@specialize.argtype(0, 1)
def rename(path1, path2):
    return os.rename(_as_bytes(path1), _as_bytes(path2))

@specialize.argtype(0)
def listdir(dirname):
    return os.listdir(_as_bytes(dirname))

@specialize.argtype(0)
def access(path, mode):
    return os.access(_as_bytes(path), mode)

@specialize.argtype(0)
def chmod(path, mode):
    return os.chmod(_as_bytes(path), mode)

@specialize.argtype(0)
def chdir(path):
    return os.chdir(_as_bytes(path))

@specialize.argtype(0)
def mkdir(path, mode=0777):
    return os.mkdir(_as_bytes(path), mode)

@specialize.argtype(0)
def rmdir(path):
    return os.rmdir(_as_bytes(path))

@specialize.argtype(0)
def mkfifo(path, mode):
    os.mkfifo(_as_bytes(path), mode)

@specialize.argtype(0)
def mknod(path, mode, device):
    os.mknod(_as_bytes(path), mode, device)

@specialize.argtype(0, 1)
def symlink(src, dest):
    os.symlink(_as_bytes(src), _as_bytes(dest))

if os.name == 'nt':
    import nt
    @specialize.argtype(0)
    def _getfullpathname(path):
        return nt._getfullpathname(_as_bytes(path))

@specialize.argtype(0, 1)
def putenv(name, value):
    os.environ[_as_bytes(name)] = _as_bytes(value)

@specialize.argtype(0)
def unsetenv(name):
    del os.environ[_as_bytes(name)]

if os.name == 'nt':
    from rpython.rlib import rwin32
    os_kill = rwin32.os_kill
else:
    os_kill = os.kill

#___________________________________________________________________
# Implementation of many posix functions.
# They usually check the return value and raise an (RPython) OSError
# with errno.

def replace_os_function(name):
    return register_replacement_for(
        getattr(os, name, None),
        sandboxed_name='ll_os.ll_os_%s' % name)

@specialize.arg(0)
def handle_posix_error(name, result):
    if result < 0:
        raise OSError(get_errno(), '%s failed' % name)
    return intmask(result)

@replace_os_function('dup')
def dup(fd):
    validate_fd(fd)
    return handle_posix_error('dup', c_dup(fd))

@replace_os_function('dup2')
def dup2(fd, newfd):
    validate_fd(fd)
    handle_posix_error('dup2', c_dup2(fd, newfd))

@replace_os_function('open')
@specialize.argtype(0)
def open(path, flags, mode):
    if _prefer_unicode(path):
        fd = c_wopen(_as_unicode0(path), flags, mode)
    else:
        fd = c_open(_as_bytes0(path), flags, mode)
    return handle_posix_error('open', fd)
        
@replace_os_function('execv')
def execv(path, args):
    rstring.check_str0(path)
    # This list conversion already takes care of NUL bytes.
    l_args = rffi.ll_liststr2charpp(args)
    c_execv(path, l_args)
    rffi.free_charpp(l_args)
    raise OSError(get_errno(), "execv failed")

@replace_os_function('execve')
def execve(path, args, env):
    envstrs = []
    for item in env.iteritems():
        envstr = "%s=%s" % item
        envstrs.append(envstr)

    rstring.check_str0(path)
    # This list conversion already takes care of NUL bytes.
    l_args = rffi.ll_liststr2charpp(args)
    l_env = rffi.ll_liststr2charpp(envstrs)
    c_execve(path, l_args, l_env)

    rffi.free_charpp(l_env)
    rffi.free_charpp(l_args)
    raise OSError(get_errno(), "execve failed")

@replace_os_function('spawnv')
def spawnv(mode, path, args):
    rstring.check_str0(path)
    l_args = rffi.ll_liststr2charpp(args)
    childpid = c_spawnv(mode, path, l_args)
    rffi.free_charpp(l_args)
    return handle_posix_error('spawnv', childpid)

@replace_os_function('spawnve')
def spawnve(mode, path, args, env):
    envstrs = []
    for item in env.iteritems():
        envstrs.append("%s=%s" % item)
    rstring.check_str0(path)
    l_args = rffi.ll_liststr2charpp(args)
    l_env = rffi.ll_liststr2charpp(envstrs)
    childpid = c_spawnve(mode, path, l_args, l_env)
    rffi.free_charpp(l_env)
    rffi.free_charpp(l_args)
    return handle_posix_error('spawnve', childpid)

@replace_os_function('getlogin')
def getlogin():
    result = c_getlogin()
    if not result:
        raise OSError(get_errno(), "getlogin failed")
    return rffi.charp2str(result)


#___________________________________________________________________

UTIMBUFP = lltype.Ptr(UTIMBUF)
c_utime = external('utime', [rffi.CCHARP, UTIMBUFP], rffi.INT)
if HAVE_UTIMES:
    class CConfig:
        _compilation_info_ = eci
        TIMEVAL = rffi_platform.Struct('struct timeval', [
            ('tv_sec', rffi.LONG),
            ('tv_usec', rffi.LONG)])
    config = rffi_platform.configure(CConfig)
    TIMEVAL = config['TIMEVAL']
    TIMEVAL2P = rffi.CArrayPtr(TIMEVAL)
    c_utimes = external('utimes', [rffi.CCHARP, TIMEVAL2P], rffi.INT)

if _WIN32:
    from rpython.rlib import rwin32
    GetSystemTime = external(
        'GetSystemTime',
        [lltype.Ptr(rwin32.SYSTEMTIME)],
        lltype.Void,
        calling_conv='win')

    SystemTimeToFileTime = external(
        'SystemTimeToFileTime',
        [lltype.Ptr(rwin32.SYSTEMTIME),
         lltype.Ptr(rwin32.FILETIME)],
        rwin32.BOOL,
        calling_conv='win')

    SetFileTime = external(
        'SetFileTime',
        [rwin32.HANDLE,
         lltype.Ptr(rwin32.FILETIME),
         lltype.Ptr(rwin32.FILETIME),
         lltype.Ptr(rwin32.FILETIME)],
        rwin32.BOOL,
        calling_conv='win')


@replace_os_function('utime')
@specialize.argtype(0, 1)
def utime(path, times):
    if not _WIN32:
        path = _as_bytes0(path)
        if times is None:
            error = c_utime(path, lltype.nullptr(UTIMBUFP.TO))
        else:
            actime, modtime = times
            if HAVE_UTIMES:
                import math
                l_times = lltype.malloc(TIMEVAL2P.TO, 2, flavor='raw')
                fracpart, intpart = math.modf(actime)
                rffi.setintfield(l_times[0], 'c_tv_sec', int(intpart))
                rffi.setintfield(l_times[0], 'c_tv_usec', int(fracpart * 1e6))
                fracpart, intpart = math.modf(modtime)
                rffi.setintfield(l_times[1], 'c_tv_sec', int(intpart))
                rffi.setintfield(l_times[1], 'c_tv_usec', int(fracpart * 1e6))
                error = c_utimes(path, l_times)
                lltype.free(l_times, flavor='raw')
            else:
                l_utimbuf = lltype.malloc(UTIMBUFP.TO, flavor='raw')
                l_utimbuf.c_actime  = rffi.r_time_t(actime)
                l_utimbuf.c_modtime = rffi.r_time_t(modtime)
                error = c_utime(path, l_utimbuf)
                lltype.free(l_utimbuf, flavor='raw')
        handle_posix_error('utime', error)
    else:  # _WIN32 case
        from rpython.rlib.rwin32file import make_win32_traits
        if _prefer_unicode(path):
            # XXX remove dependency on rtyper.module.  The "traits"
            # are just used for CreateFile anyway.
            from rpython.rtyper.module.support import UnicodeTraits
            win32traits = make_win32_traits(UnicodeTraits())
            path = _as_unicode0(path)
        else:
            from rpython.rtyper.module.support import StringTraits
            win32traits = make_win32_traits(StringTraits())
            path = _as_bytes0(path)
        hFile = win32traits.CreateFile(path,
                           win32traits.FILE_WRITE_ATTRIBUTES, 0,
                           None, win32traits.OPEN_EXISTING,
                           win32traits.FILE_FLAG_BACKUP_SEMANTICS,
                           rwin32.NULL_HANDLE)
        if hFile == rwin32.INVALID_HANDLE_VALUE:
            raise rwin32.lastWindowsError()
        ctime = lltype.nullptr(rwin32.FILETIME)
        atime = lltype.malloc(rwin32.FILETIME, flavor='raw')
        mtime = lltype.malloc(rwin32.FILETIME, flavor='raw')
        try:
            if tp is None:
                now = lltype.malloc(rwin32.SYSTEMTIME, flavor='raw')
                try:
                    GetSystemTime(now)
                    if (not SystemTimeToFileTime(now, atime) or
                        not SystemTimeToFileTime(now, mtime)):
                        raise rwin32.lastWindowsError()
                finally:
                    lltype.free(now, flavor='raw')
            else:
                actime, modtime = tp
                time_t_to_FILE_TIME(actime, atime)
                time_t_to_FILE_TIME(modtime, mtime)
            if not SetFileTime(hFile, ctime, atime, mtime):
                raise rwin32.lastWindowsError()
        finally:
            rwin32.CloseHandle(hFile)
            lltype.free(atime, flavor='raw')
            lltype.free(mtime, flavor='raw')

if not _WIN32:
    TMSP = lltype.Ptr(TMS)
    os_times = external('times', [TMSP], CLOCK_T)

    # Here is a random extra platform parameter which is important.
    # Strictly speaking, this should probably be retrieved at runtime, not
    # at translation time.
    CLOCK_TICKS_PER_SECOND = float(os.sysconf('SC_CLK_TCK'))
else:
    GetCurrentProcess = external(
        'GetCurrentProcess', [],
        rwin32.HANDLE, calling_conv='win')
    GetProcessTimes = external(
        'GetProcessTimes', [
            rwin32.HANDLE,
            lltype.Ptr(rwin32.FILETIME), lltype.Ptr(rwin32.FILETIME),
            lltype.Ptr(rwin32.FILETIME), lltype.Ptr(rwin32.FILETIME)],
        rwin32.BOOL, calling_conv='win')

@replace_os_function('times')
def times():
    if not _WIN32:
        l_tmsbuf = lltype.malloc(TMSP.TO, flavor='raw')
        try:
            result = os_times(l_tmsbuf)
            result = rffi.cast(lltype.Signed, result)
            if result == -1:
                raise OSError(get_errno(), "times failed")
            return (
                rffi.cast(lltype.Signed, l_tmsbuf.c_tms_utime)
                                               / CLOCK_TICKS_PER_SECOND,
                rffi.cast(lltype.Signed, l_tmsbuf.c_tms_stime)
                                               / CLOCK_TICKS_PER_SECOND,
                rffi.cast(lltype.Signed, l_tmsbuf.c_tms_cutime)
                                               / CLOCK_TICKS_PER_SECOND,
                rffi.cast(lltype.Signed, l_tmsbuf.c_tms_cstime)
                                               / CLOCK_TICKS_PER_SECOND,
                result / CLOCK_TICKS_PER_SECOND)
        finally:
            lltype.free(l_tmsbuf, flavor='raw')
    else:
        pcreate = lltype.malloc(rwin32.FILETIME, flavor='raw')
        pexit   = lltype.malloc(rwin32.FILETIME, flavor='raw')
        pkernel = lltype.malloc(rwin32.FILETIME, flavor='raw')
        puser   = lltype.malloc(rwin32.FILETIME, flavor='raw')
        try:
            hProc = GetCurrentProcess()
            GetProcessTimes(hProc, pcreate, pexit, pkernel, puser)
            # The fields of a FILETIME structure are the hi and lo parts
            # of a 64-bit value expressed in 100 nanosecond units
            # (of course).
            return (
                rffi.cast(lltype.Signed, pkernel.c_dwHighDateTime) * 429.4967296 +
                rffi.cast(lltype.Signed, pkernel.c_dwLowDateTime) * 1E-7,
                rffi.cast(lltype.Signed, puser.c_dwHighDateTime) * 429.4967296 +
                rffi.cast(lltype.Signed, puser.c_dwLowDateTime) * 1E-7,
                0, 0, 0)
        finally:
            lltype.free(puser,   flavor='raw')
            lltype.free(pkernel, flavor='raw')
            lltype.free(pexit,   flavor='raw')
            lltype.free(pcreate, flavor='raw')

#___________________________________________________________________

c_setsid = external('setsid', [], rffi.PID_T)
c_getsid = external('getsid', [rffi.PID_T], rffi.PID_T)
c_getuid = external('getuid', [], rffi.INT)
c_geteuid = external('geteuid', [], rffi.INT)
c_setuid = external('setuid', [rffi.INT], rffi.INT)
c_seteuid = external('seteuid', [rffi.INT], rffi.INT)
c_getgid = external('getgid', [], rffi.INT)
c_getegid = external('getegid', [], rffi.INT)
c_setgid = external('setgid', [rffi.INT], rffi.INT)
c_setegid = external('setegid', [rffi.INT], rffi.INT)

@replace_os_function('setsid')
def setsid():
    return handle_posix_error('setsid', c_setsid())

@replace_os_function('getsid')
def getsid(pid):
    return handle_posix_error('getsid', c_getsid(pid))

@replace_os_function('getuid')
def getuid():
    return handle_posix_error('getuid', c_getuid())

@replace_os_function('geteuid')
def geteuid():
    return handle_posix_error('geteuid', c_geteuid())

@replace_os_function('setuid')
def setuid(uid):
    handle_posix_error('setuid', c_setuid(uid))

@replace_os_function('seteuid')
def seteuid(uid):
    handle_posix_error('seteuid', c_seteuid(uid))

@replace_os_function('getgid')
def getgid():
    return handle_posix_error('getgid', c_getgid())

@replace_os_function('getegid')
def getegid():
    return handle_posix_error('getegid', c_getegid())

@replace_os_function('setgid')
def setgid(gid):
    handle_posix_error('setgid', c_setgid(gid))

@replace_os_function('setegid')
def setegid(gid):
    handle_posix_error('setegid', c_setegid(gid))

c_setreuid = external('setreuid', [rffi.INT, rffi.INT], rffi.INT)
c_setregid = external('setregid', [rffi.INT, rffi.INT], rffi.INT)

@replace_os_function('setreuid')
def setreuid(ruid, euid):
    handle_posix_error('setreuid', c_setreuid(ruid, euid))

@replace_os_function('setregid')
def setregid(rgid, egid):
    handle_posix_error('setregid', c_setregid(rgid, egid))

c_getresuid = external('getresuid', [rffi.INTP] * 3, rffi.INT)
c_getresgid = external('getresgid', [rffi.INTP] * 3, rffi.INT)
c_setresuid = external('setresuid', [rffi.INT] * 3, rffi.INT)
c_setresgid = external('setresgid', [rffi.INT] * 3, rffi.INT)

@replace_os_function('getresuid')
def getresuid():
    out = lltype.malloc(rffi.INTP.TO, 3, flavor='raw')
    try:
        handle_posix_error('getresuid',
                           c_getresuid(rffi.ptradd(out, 0),
                                       rffi.ptradd(out, 1),
                                       rffi.ptradd(out, 2)))
        return (intmask(out[0]), intmask(out[1]), intmask(out[2]))
    finally:
        lltype.free(out, flavor='raw')

@replace_os_function('getresgid')
def getresgid():
    out = lltype.malloc(rffi.INTP.TO, 3, flavor='raw')
    try:
        handle_posix_error('getresgid',
                           c_getresgid(rffi.ptradd(out, 0),
                                       rffi.ptradd(out, 1),
                                       rffi.ptradd(out, 2)))
        return (intmask(out[0]), intmask(out[1]), intmask(out[2]))
    finally:
        lltype.free(out, flavor='raw')

@replace_os_function('setresuid')
def setresuid(ruid, euid, suid):
    handle_posix_error('setresuid', c_setresuid(ruid, euid, suid))

@replace_os_function('setresgid')
def setresgid(rgid, egid, sgid):
    handle_posix_error('setresgid', c_setresgid(rgid, egid, sgid))

