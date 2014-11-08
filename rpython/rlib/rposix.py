import os
import sys
import errno
from rpython.rtyper.lltypesystem.rffi import CConstant, CExternVariable, INT
from rpython.rtyper.lltypesystem import lltype, ll2ctypes, rffi
from rpython.rtyper.module.support import StringTraits, UnicodeTraits
from rpython.rtyper.tool import rffi_platform
from rpython.translator.tool.cbuild import ExternalCompilationInfo
from rpython.rlib.rarithmetic import intmask, widen
from rpython.rlib.objectmodel import (
    specialize, enforceargs, register_replacement_for)
from rpython.rlib import jit
from rpython.translator.platform import platform
from rpython.rlib import rstring
from rpython.rlib import debug, rthread

_WIN32 = sys.platform.startswith('win')
_CYGWIN = sys.platform == 'cygwin'
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
    libraries = []
else:
    includes = ['unistd.h',  'sys/types.h',
                'utime.h', 'sys/time.h', 'sys/times.h',
                'grp.h', 'dirent.h']
    libraries = ['util']
eci = ExternalCompilationInfo(
    includes=includes,
    libraries=libraries,
)

class CConfig:
    _compilation_info_ = eci
    SEEK_SET = rffi_platform.DefinedConstantInteger('SEEK_SET')
    SEEK_CUR = rffi_platform.DefinedConstantInteger('SEEK_CUR')
    SEEK_END = rffi_platform.DefinedConstantInteger('SEEK_END')

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

    GETPGRP_HAVE_ARG = rffi_platform.Has("getpgrp(0)")
    SETPGRP_HAVE_ARG = rffi_platform.Has("setpgrp(0, 0)")

config = rffi_platform.configure(CConfig)
globals().update(config)

def external(name, args, result, compilation_info=eci, **kwds):
    return rffi.llexternal(name, args, result,
                           compilation_info=compilation_info, **kwds)

c_dup = external(UNDERSCORE_ON_WIN32 + 'dup', [rffi.INT], rffi.INT)
c_dup2 = external(UNDERSCORE_ON_WIN32 + 'dup2', [rffi.INT, rffi.INT], rffi.INT)
c_open = external(UNDERSCORE_ON_WIN32 + 'open',
                  [rffi.CCHARP, rffi.INT, rffi.MODE_T], rffi.INT)

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
unicode_traits = UnicodeTraits()
string_traits = StringTraits()
if _WIN32:
    @specialize.argtype(0)
    def _prefer_unicode(path):
        if isinstance(path, str):
            return False
        elif isinstance(path, unicode):
            return True
        else:
            return path.is_unicode

    @specialize.argtype(0)
    def _preferred_traits(path):
        if _prefer_unicode(path):
            return unicode_traits
        else:
            return string_traits
else:
    @specialize.argtype(0)
    def _prefer_unicode(path):
        return False

    @specialize.argtype(0)
    def _preferred_traits(path):
        return string_traits
    
@specialize.argtype(0)
def stat(path):
    return os.stat(_as_bytes(path))

@specialize.argtype(0)
def lstat(path):
    return os.lstat(_as_bytes(path))

@specialize.argtype(0)
def statvfs(path):
    return os.statvfs(_as_bytes(path))

@specialize.argtype(0, 1)
def symlink(src, dest):
    os.symlink(_as_bytes(src), _as_bytes(dest))

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
    result = widen(result)
    if result < 0:
        raise OSError(get_errno(), '%s failed' % name)
    return result

@replace_os_function('dup')
def dup(fd):
    validate_fd(fd)
    return handle_posix_error('dup', c_dup(fd))

@replace_os_function('dup2')
def dup2(fd, newfd):
    validate_fd(fd)
    handle_posix_error('dup2', c_dup2(fd, newfd))

#___________________________________________________________________

@replace_os_function('open')
@specialize.argtype(0)
def open(path, flags, mode):
    if _prefer_unicode(path):
        fd = c_wopen(_as_unicode0(path), flags, mode)
    else:
        fd = c_open(_as_bytes0(path), flags, mode)
    return handle_posix_error('open', fd)

c_read = external(UNDERSCORE_ON_WIN32 + 'read',
                  [rffi.INT, rffi.VOIDP, rffi.SIZE_T], rffi.SSIZE_T)
c_write = external(UNDERSCORE_ON_WIN32 + 'write',
                   [rffi.INT, rffi.VOIDP, rffi.SIZE_T], rffi.SSIZE_T)
c_close = external(UNDERSCORE_ON_WIN32 + 'close', [rffi.INT], rffi.INT,
                   releasegil=False)

@replace_os_function('read')
def read(fd, count):
    if count < 0:
        raise OSError(errno.EINVAL, None)
    validate_fd(fd)
    with rffi.scoped_alloc_buffer(count) as buf:
        void_buf = rffi.cast(rffi.VOIDP, buf.raw)
        got = handle_posix_error('read', c_read(fd, void_buf, count))
        return buf.str(got)

@replace_os_function('write')
def write(fd, data):
    count = len(data)
    validate_fd(fd)
    with rffi.scoped_nonmovingbuffer(data) as buf:
        return handle_posix_error('write', c_write(fd, buf, count))

@replace_os_function('close')
def close(fd):
    validate_fd(fd)
    handle_posix_error('close', c_close(fd))

c_lseek = external('_lseeki64' if _WIN32 else 'lseek',
                   [rffi.INT, rffi.LONGLONG, rffi.INT], rffi.LONGLONG,
                   macro=True)

@replace_os_function('lseek')
def lseek(fd, pos, how):
    validate_fd(fd)
    if SEEK_SET is not None:
        if how == 0:
            how = SEEK_SET
        elif how == 1: 
            how = SEEK_CUR
        elif how == 2:
            how = SEEK_END
    return handle_posix_error('lseek', c_lseek(fd, pos, how))

c_ftruncate = external('ftruncate', [rffi.INT, rffi.LONGLONG], rffi.INT,
                       macro=True)
c_fsync = external('fsync' if not _WIN32 else '_commit', [rffi.INT], rffi.INT)
c_fdatasync = external('fdatasync', [rffi.INT], rffi.INT)

@replace_os_function('ftruncate')
def ftruncate(fd, length):
    validate_fd(fd)
    handle_posix_error('ftruncate', c_ftruncate(fd, length))

@replace_os_function('fsync')
def fsync(fd):
    validate_fd(fd)
    handle_posix_error('fsync', c_fsync(fd))

@replace_os_function('fdatasync')
def fdatasync(fd):
    validate_fd(fd)
    handle_posix_error('fdatasync', c_fdatasync(fd))

#___________________________________________________________________

c_chdir = external('chdir', [rffi.CCHARP], rffi.INT)
c_fchdir = external('fchdir', [rffi.INT], rffi.INT)
c_access = external(UNDERSCORE_ON_WIN32 + 'access',
                    [rffi.CCHARP, rffi.INT], rffi.INT)
c_waccess = external(UNDERSCORE_ON_WIN32 + 'waccess',
                     [rffi.CWCHARP, rffi.INT], rffi.INT)

@replace_os_function('chdir')
@specialize.argtype(0)
def chdir(path):
    if not _WIN32:
        handle_posix_error('chdir', c_chdir(_as_bytes0(path)))
    else:
        traits = _preferred_traits(path)
        from rpython.rlib.rwin32file import make_win32_traits
        win32traits = make_win32_traits(traits)
        path = traits._as_str0(path)

        # This is a reimplementation of the C library's chdir
        # function, but one that produces Win32 errors instead of DOS
        # error codes.
        # chdir is essentially a wrapper around SetCurrentDirectory;
        # however, it also needs to set "magic" environment variables
        # indicating the per-drive current directory, which are of the
        # form =<drive>:
        if not win32traits.SetCurrentDirectory(path):
            raise rwin32.lastWindowsError()
        MAX_PATH = rwin32.MAX_PATH
        assert MAX_PATH > 0

        with traits.scoped_alloc_buffer(MAX_PATH) as path:
            res = win32traits.GetCurrentDirectory(MAX_PATH + 1, path.raw)
            if not res:
                raise rwin32.lastWindowsError()
            res = rffi.cast(lltype.Signed, res)
            assert res > 0
            if res <= MAX_PATH + 1:
                new_path = path.str(res)
            else:
                with traits.scoped_alloc_buffer(res) as path:
                    res = win32traits.GetCurrentDirectory(res, path.raw)
                    if not res:
                        raise rwin32.lastWindowsError()
                    res = rffi.cast(lltype.Signed, res)
                    assert res > 0
                    new_path = path.str(res)
        if traits.str is unicode:
            if new_path[0] == u'\\' or new_path[0] == u'/':  # UNC path
                return
            magic_envvar = u'=' + new_path[0] + u':'
        else:
            if new_path[0] == '\\' or new_path[0] == '/':  # UNC path
                return
            magic_envvar = '=' + new_path[0] + ':'
        if not win32traits.SetEnvironmentVariable(magic_envvar, new_path):
            raise rwin32.lastWindowsError()

@replace_os_function('fchdir')
def fchdir(fd):
    validate_fd(fd)
    handle_posix_error('fchdir', c_fchdir(fd))

@replace_os_function('access')
@specialize.argtype(0)
def access(path, mode):
    if _WIN32:
        # All files are executable on Windows
        mode = mode & ~os.X_OK
    if _prefer_unicode(path):
        error = c_waccess(_as_unicode0(path), mode)
    else:
        error = c_access(_as_bytes0(path), mode)
    return error == 0

# This Win32 function is not exposed via os, but needed to get a
# correct implementation of os.path.abspath.
@specialize.argtype(0)
def getfullpathname(path):
    length = rwin32.MAX_PATH + 1
    traits = _preferred_traits(path)
    from rpython.rlib.rwin32file import make_win32_traits
    win32traits = make_win32_traits(traits)
    with traits.scoped_alloc_buffer(length) as buf:
        res = win32traits.GetFullPathName(
            traits.as_str0(path), rffi.cast(rwin32.DWORD, length),
            buf.raw, lltype.nullptr(win32traits.LPSTRP.TO))
        if res == 0:
            raise rwin32.lastWindowsError("_getfullpathname failed")
        return buf.str(intmask(res))

c_getcwd = external(UNDERSCORE_ON_WIN32 + 'getcwd',
                    [rffi.CCHARP, rffi.SIZE_T], rffi.CCHARP)
c_wgetcwd = external(UNDERSCORE_ON_WIN32 + 'wgetcwd',
                     [rffi.CWCHARP, rffi.SIZE_T], rffi.CWCHARP)

@replace_os_function('getcwd')
def getcwd():
    bufsize = 256
    while True:
        buf = lltype.malloc(rffi.CCHARP.TO, bufsize, flavor='raw')
        res = c_getcwd(buf, bufsize)
        if res:
            break   # ok
        error = get_errno()
        lltype.free(buf, flavor='raw')
        if error != errno.ERANGE:
            raise OSError(error, "getcwd failed")
        # else try again with a larger buffer, up to some sane limit
        bufsize *= 4
        if bufsize > 1024*1024:  # xxx hard-coded upper limit
            raise OSError(error, "getcwd result too large")
    result = rffi.charp2str(res)
    lltype.free(buf, flavor='raw')
    return result

@replace_os_function('getcwdu')
def getcwdu():
    bufsize = 256
    while True:
        buf = lltype.malloc(rffi.CWCHARP.TO, bufsize, flavor='raw')
        res = c_wgetcwd(buf, bufsize)
        if res:
            break   # ok
        error = get_errno()
        lltype.free(buf, flavor='raw')
        if error != errno.ERANGE:
            raise OSError(error, "getcwd failed")
        # else try again with a larger buffer, up to some sane limit
        bufsize *= 4
        if bufsize > 1024*1024:  # xxx hard-coded upper limit
            raise OSError(error, "getcwd result too large")
    result = rffi.wcharp2unicode(res)
    lltype.free(buf, flavor='raw')
    return result

if not _WIN32:
    class CConfig:
        _compilation_info_ = eci
        DIRENT = rffi_platform.Struct('struct dirent',
            [('d_name', lltype.FixedSizeArray(rffi.CHAR, 1))])

    DIRP = rffi.COpaquePtr('DIR')
    config = rffi_platform.configure(CConfig)
    DIRENT = config['DIRENT']
    DIRENTP = lltype.Ptr(DIRENT)
    c_opendir = external('opendir', [rffi.CCHARP], DIRP)
    # XXX macro=True is hack to make sure we get the correct kind of
    # dirent struct (which depends on defines)
    c_readdir = external('readdir', [DIRP], DIRENTP, macro=True)
    c_closedir = external('closedir', [DIRP], rffi.INT)

@replace_os_function('listdir')
@specialize.argtype(0)
def listdir(path):
    if not _WIN32:
        path = _as_bytes0(path)
        dirp = c_opendir(path)
        if not dirp:
            raise OSError(get_errno(), "opendir failed")
        result = []
        while True:
            set_errno(0)
            direntp = c_readdir(dirp)
            if not direntp:
                error = get_errno()
                break
            namep = rffi.cast(rffi.CCHARP, direntp.c_d_name)
            name = rffi.charp2str(namep)
            if name != '.' and name != '..':
                result.append(name)
        c_closedir(dirp)
        if error:
            raise OSError(error, "readdir failed")
        return result
    else:  # _WIN32 case
        from rpython.rlib.rwin32file import make_win32_traits
        traits = _preferred_traits(path)
        win32traits = make_win32_traits(traits)
        path = traits.as_str0(path)

        if traits.str is unicode:
            if path and path[-1] not in (u'/', u'\\', u':'):
                path += u'/'
            mask = path + u'*.*'
        else:
            if path and path[-1] not in ('/', '\\', ':'):
                path += '/'
            mask = path + '*.*'

        filedata = lltype.malloc(win32traits.WIN32_FIND_DATA, flavor='raw')
        try:
            result = []
            hFindFile = win32traits.FindFirstFile(mask, filedata)
            if hFindFile == rwin32.INVALID_HANDLE_VALUE:
                error = rwin32.GetLastError()
                if error == win32traits.ERROR_FILE_NOT_FOUND:
                    return result
                else:
                    raise WindowsError(error,  "FindFirstFile failed")
            while True:
                name = traits.charp2str(rffi.cast(traits.CCHARP,
                                                  filedata.c_cFileName))
                if traits.str is unicode:
                    if not (name == u"." or name == u".."):
                        result.append(name)
                else:
                    if not (name == "." or name == ".."):
                        result.append(name)
                if not win32traits.FindNextFile(hFindFile, filedata):
                    break
            # FindNextFile sets error to ERROR_NO_MORE_FILES if
            # it got to the end of the directory
            error = rwin32.GetLastError()
            win32traits.FindClose(hFindFile)
            if error == win32traits.ERROR_NO_MORE_FILES:
                return result
            else:
                raise WindowsError(error,  "FindNextFile failed")
        finally:
            lltype.free(filedata, flavor='raw')

#___________________________________________________________________

c_execv = external('execv', [rffi.CCHARP, rffi.CCHARPP], rffi.INT)
c_execve = external('execve',
                    [rffi.CCHARP, rffi.CCHARPP, rffi.CCHARPP], rffi.INT)
c_spawnv = external('spawnv',
                    [rffi.INT, rffi.CCHARP, rffi.CCHARPP], rffi.INT)
c_spawnve = external('spawnve',
                    [rffi.INT, rffi.CCHARP, rffi.CCHARPP, rffi.CCHARPP],
                     rffi.INT)

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

c_fork = external('fork', [], rffi.PID_T, _nowrapper = True)
c_openpty = external('openpty',
                     [rffi.INTP, rffi.INTP, rffi.VOIDP, rffi.VOIDP, rffi.VOIDP],
                     rffi.INT)
c_forkpty = external('forkpty',
                     [rffi.INTP, rffi.VOIDP, rffi.VOIDP, rffi.VOIDP],
                     rffi.PID_T)

@replace_os_function('fork')
def fork():
    # NB. keep forkpty() up-to-date, too
    ofs = debug.debug_offset()
    opaqueaddr = rthread.gc_thread_before_fork()
    childpid = c_fork()
    rthread.gc_thread_after_fork(childpid, opaqueaddr)
    childpid = handle_posix_error('fork', childpid)
    if childpid == 0:
        debug.debug_forked(ofs)
    return childpid

@replace_os_function('openpty')
def openpty():
    master_p = lltype.malloc(rffi.INTP.TO, 1, flavor='raw')
    slave_p = lltype.malloc(rffi.INTP.TO, 1, flavor='raw')
    try:
        handle_posix_error(
            'openpty', c_openpty(master_p, slave_p, None, None, None))
        return (widen(master_p[0]), widen(slave_p[0]))
    finally:
        lltype.free(master_p, flavor='raw')
        lltype.free(slave_p, flavor='raw')

@replace_os_function('forkpty')
def forkpty():
    master_p = lltype.malloc(rffi.INTP.TO, 1, flavor='raw')
    master_p[0] = rffi.cast(rffi.INT, -1)
    try:
        ofs = debug.debug_offset()
        opaqueaddr = rthread.gc_thread_before_fork()
        childpid = c_forkpty(master_p, None, None, None)
        rthread.gc_thread_after_fork(childpid, opaqueaddr)
        childpid = handle_posix_error('forkpty', childpid)
        if childpid == 0:
            debug.debug_forked(ofs)
        return (childpid, master_p[0])
    finally:
        lltype.free(master_p, flavor='raw')

if _WIN32:
    # emulate waitpid() with the _cwait() of Microsoft's compiler
    c__cwait = external('_cwait',
                        [rffi.INTP, rffi.PID_T, rffi.INT], rffi.PID_T)
    def c_waitpid(pid, status_p, options):
        result = c__cwait(status_p, pid, options)
        # shift the status left a byte so this is more
        # like the POSIX waitpid
        status_p[0] = rffi.cast(rffi.INT, widen(status_p[0]) << 8)
        return result
elif _CYGWIN:
    c_waitpid = external('cygwin_waitpid',
                         [rffi.PID_T, rffi.INTP, rffi.INT], rffi.PID_T)
else:
    c_waitpid = external('waitpid',
                         [rffi.PID_T, rffi.INTP, rffi.INT], rffi.PID_T)

@replace_os_function('waitpid')
def waitpid(pid, options):
    status_p = lltype.malloc(rffi.INTP.TO, 1, flavor='raw')
    status_p[0] = rffi.cast(rffi.INT, 0)
    try:
        result = handle_posix_error('waitpid',
                                    c_waitpid(pid, status_p, options))
        status = widen(status_p[0])
        return (result, status)
    finally:
        lltype.free(status_p, flavor='raw')

if _WIN32:
    CreatePipe = external('CreatePipe', [rwin32.LPHANDLE,
                                         rwin32.LPHANDLE,
                                         rffi.VOIDP,
                                         rwin32.DWORD],
                          rwin32.BOOL)
    c_open_osfhandle = external('_open_osfhandle', [rffi.INTPTR_T,
                                                    rffi.INT],
                                rffi.INT)
else:
    INT_ARRAY_P = rffi.CArrayPtr(rffi.INT)
    c_pipe = external('pipe', [INT_ARRAY_P], rffi.INT)

@replace_os_function('pipe')
def pipe():
    if _WIN32:
        pread  = lltype.malloc(rwin32.LPHANDLE.TO, 1, flavor='raw')
        pwrite = lltype.malloc(rwin32.LPHANDLE.TO, 1, flavor='raw')
        try:
            if not CreatePipe(
                    pread, pwrite, lltype.nullptr(rffi.VOIDP.TO), 0):
                raise WindowsError(rwin32.GetLastError(), "CreatePipe failed")
            hread = rffi.cast(rffi.INTPTR_T, pread[0])
            hwrite = rffi.cast(rffi.INTPTR_T, pwrite[0])
        finally:
            lltype.free(pwrite, flavor='raw')
            lltype.free(pread, flavor='raw')
        fdread = _open_osfhandle(hread, 0)
        fdwrite = _open_osfhandle(hwrite, 1)
        return (fdread, fdwrite)
    else:
        filedes = lltype.malloc(INT_ARRAY_P.TO, 2, flavor='raw')
        try:
            handle_posix_error('pipe', c_pipe(filedes))
            return (widen(filedes[0]), widen(filedes[1]))
        finally:
            lltype.free(filedes, flavor='raw')

#___________________________________________________________________

c_getlogin = external('getlogin', [], rffi.CCHARP, releasegil=False)
c_getloadavg = external('getloadavg', 
                        [rffi.CArrayPtr(lltype.Float), rffi.INT], rffi.INT)

@replace_os_function('getlogin')
def getlogin():
    result = c_getlogin()
    if not result:
        raise OSError(get_errno(), "getlogin failed")
    return rffi.charp2str(result)

@replace_os_function('getloadavg')
def getloadavg():
    load = lltype.malloc(rffi.CArrayPtr(lltype.Float).TO, 3, flavor='raw')
    try:
        r = c_getloadavg(load, 3)
        if r != 3:
            raise OSError
        return (load[0], load[1], load[2])
    finally:
        lltype.free(load, flavor='raw')

#___________________________________________________________________

c_readlink = external('readlink',
                      [rffi.CCHARP, rffi.CCHARP, rffi.SIZE_T], rffi.SSIZE_T)

@replace_os_function('readlink')
def readlink(path):
    path = _as_bytes0(path)
    bufsize = 1023
    while True:
        buf = lltype.malloc(rffi.CCHARP.TO, bufsize, flavor='raw')
        res = widen(c_readlink(path, buf, bufsize))
        if res < 0:
            lltype.free(buf, flavor='raw')
            error = get_errno()    # failed
            raise OSError(error, "readlink failed")
        elif res < bufsize:
            break                       # ok
        else:
            # buf too small, try again with a larger buffer
            lltype.free(buf, flavor='raw')
            bufsize *= 4
    # convert the result to a string
    result = rffi.charp2strn(buf, res)
    lltype.free(buf, flavor='raw')
    return result

c_isatty = external(UNDERSCORE_ON_WIN32 + 'isatty', [rffi.INT], rffi.INT)

@replace_os_function('isatty')
def isatty(fd):
    if not is_valid_fd(fd):
        return False
    return c_isatty(fd) != 0
    
c_strerror = external('strerror', [rffi.INT], rffi.CCHARP,
                      releasegil=False)

@replace_os_function('strerror')
def strerror(errnum):
    res = c_strerror(errnum)
    if not res:
        raise ValueError("os_strerror failed")
    return rffi.charp2str(res)

c_system = external('system', [rffi.CCHARP], rffi.INT)

@replace_os_function('system')
def system(command):
    return widen(c_system(command))

c_unlink = external('unlink', [rffi.CCHARP], rffi.INT)
c_mkdir = external('mkdir', [rffi.CCHARP, rffi.MODE_T], rffi.INT)
c_rmdir = external(UNDERSCORE_ON_WIN32 + 'rmdir', [rffi.CCHARP], rffi.INT)
c_wrmdir = external(UNDERSCORE_ON_WIN32 + 'wrmdir', [rffi.CWCHARP], rffi.INT)

@replace_os_function('unlink')
@specialize.argtype(0)
def unlink(path):
    if not _WIN32:
        handle_posix_error('unlink', c_unlink(_as_bytes0(path)))
    else:
        traits = _preferred_traits(path)
        win32traits = make_win32_traits(traits)
        if not win32traits.DeleteFile(traits.as_str0(path)):
            raise rwin32.lastWindowsError()

@replace_os_function('mkdir')
@specialize.argtype(0)
def mkdir(path, mode=0o777):
    if not _WIN32:
        handle_posix_error('mkdir', c_mkdir(_as_bytes0(path), mode))
    else:
        traits = _preferred_traits(path)
        win32traits = make_win32_traits(traits)
        if not win32traits.CreateDirectory(traits.as_str0(path), None):
            raise rwin32.lastWindowsError()

@replace_os_function('rmdir')
@specialize.argtype(0)
def rmdir(path):
    if _prefer_unicode(path):
        handle_posix_error('wrmdir', c_wrmdir(_as_unicode0(path)))
    else:
        handle_posix_error('rmdir', c_rmdir(_as_bytes0(path)))

c_chmod = external('chmod', [rffi.CCHARP, rffi.MODE_T], rffi.INT)
c_fchmod = external('fchmod', [rffi.INT, rffi.MODE_T], rffi.INT)
c_rename = external('rename', [rffi.CCHARP, rffi.CCHARP], rffi.INT)

@replace_os_function('chmod')
@specialize.argtype(0)
def chmod(path, mode):
    if not _WIN32:
        handle_posix_error('chmod', c_chmod(_as_bytes0(path), mode))
    else:
        traits = _preferred_traits(path)
        win32traits = make_win32_traits(traits)
        path = traits.as_str0(path)
        attr = win32traits.GetFileAttributes(path)
        if attr == win32traits.INVALID_FILE_ATTRIBUTES:
            raise rwin32.lastWindowsError()
        if mode & 0200: # _S_IWRITE
            attr &= ~win32traits.FILE_ATTRIBUTE_READONLY
        else:
            attr |= win32traits.FILE_ATTRIBUTE_READONLY
        if not win32traits.SetFileAttributes(path, attr):
            raise rwin32.lastWindowsError()

@replace_os_function('fchmod')
def fchmod(fd, mode):
    handle_posix_error('fchmod', c_fchmod(fd, mode))

@replace_os_function('rename')
@specialize.argtype(0, 1)
def rename(path1, path2):
    if not _WIN32:
        handle_posix_error('rename',
                           c_rename(_as_bytes0(path1), _as_bytes0(path2)))
    else:
        traits = _preferred_traits(path)
        win32traits = make_win32_traits(traits)
        path1 = traits.as_str0(path1)
        path2 = traits.as_str0(path2)
        if not win32traits.MoveFile(path1, path2):
            raise rwin32.lastWindowsError()

c_mkfifo = external('mkfifo', [rffi.CCHARP, rffi.MODE_T], rffi.INT)
c_mknod = external('mknod', [rffi.CCHARP, rffi.MODE_T, rffi.INT], rffi.INT,
                   macro=True)
#                                           # xxx: actually ^^^ dev_t

@replace_os_function('mkfifo')
@specialize.argtype(0)
def mkfifo(path, mode):
    handle_posix_error('mkfifo', c_mkfifo(_as_bytes0(path), mode))

@replace_os_function('mknod')
@specialize.argtype(0)
def mknod(path, mode, dev):
    handle_posix_error('mknod', c_mknod(_as_bytes0(path), mode, dev))

c_umask = external(UNDERSCORE_ON_WIN32 + 'umask', [rffi.MODE_T], rffi.MODE_T)

@replace_os_function('umask')
def umask(newmask):
    return widen(c_umask(newmask))

c_chown = external('chown', [rffi.CCHARP, rffi.INT, rffi.INT], rffi.INT)
c_lchown = external('lchown', [rffi.CCHARP, rffi.INT, rffi.INT], rffi.INT)
c_fchown = external('fchown', [rffi.INT, rffi.INT, rffi.INT], rffi.INT)

@replace_os_function('chown')
def chown(path, uid, gid):
    handle_posix_error('chown', c_chown(path, uid, gid))

@replace_os_function('lchown')
def lchown(path, uid, gid):
    handle_posix_error('lchown', c_lchown(path, uid, gid))

@replace_os_function('fchown')
def fchown(fd, uid, gid):
    handle_posix_error('fchown', c_fchown(fd, uid, gid))

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
        from rpython.rlib.rwin32file import make_win32_traits, time_t_to_FILE_TIME
        traits = _preferred_traits(path)
        win32traits = make_win32_traits(traits)
        path = traits.as_str0(path)
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
            if times is None:
                now = lltype.malloc(rwin32.SYSTEMTIME, flavor='raw')
                try:
                    GetSystemTime(now)
                    if (not SystemTimeToFileTime(now, atime) or
                        not SystemTimeToFileTime(now, mtime)):
                        raise rwin32.lastWindowsError()
                finally:
                    lltype.free(now, flavor='raw')
            else:
                actime, modtime = times
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

c_getpid = external('getpid', [], rffi.PID_T, releasegil=False)
c_getppid = external('getppid', [], rffi.PID_T, releasegil=False)
c_setsid = external('setsid', [], rffi.PID_T)
c_getsid = external('getsid', [rffi.PID_T], rffi.PID_T)

@replace_os_function('getpid')
def getpid():
    return widen(c_getpid())

@replace_os_function('getppid')
def getppid():
    return widen(c_getppid())

@replace_os_function('setsid')
def setsid():
    return handle_posix_error('setsid', c_setsid())

@replace_os_function('getsid')
def getsid(pid):
    return handle_posix_error('getsid', c_getsid(pid))

c_getpgid = external('getpgid', [rffi.PID_T], rffi.PID_T)
c_setpgid = external('setpgid', [rffi.PID_T, rffi.PID_T], rffi.INT)

@replace_os_function('getpgid')
def getpgid(pid):
    return handle_posix_error('getpgid', c_getpgid(pid))

@replace_os_function('setpgid')
def setpgid(pid, gid):
    handle_posix_error('setpgid', c_setpgid(pid, gid))

PID_GROUPS_T = rffi.CArrayPtr(rffi.PID_T)
c_getgroups = external('getgroups', [rffi.INT, PID_GROUPS_T], rffi.INT)
c_setgroups = external('setgroups', [rffi.SIZE_T, PID_GROUPS_T], rffi.INT)
c_initgroups = external('initgroups', [rffi.CCHARP, rffi.PID_T], rffi.INT)

@replace_os_function('getgroups')
def getgroups():
    n = handle_posix_error('getgroups',
                           c_getgroups(0, lltype.nullptr(PID_GROUPS_T.TO)))
    groups = lltype.malloc(PID_GROUPS_T.TO, n, flavor='raw')
    try:
        n = handle_posix_error('getgroups', c_getgroups(n, groups))
        return [widen(groups[i]) for i in range(n)]
    finally:
        lltype.free(groups, flavor='raw')

@replace_os_function('setgroups')
def setgroups(gids):
    n = len(gids)
    groups = lltype.malloc(PID_GROUPS_T.TO, n, flavor='raw')
    try:
        for i in range(n):
            groups[i] = rffi.cast(rffi.PID_T, gids[i])
        handle_posix_error('setgroups', c_setgroups(n, groups))
    finally:
        lltype.free(groups, flavor='raw')

@replace_os_function('initgroups')
def initgroups(user, group):
    handle_posix_error('initgroups', c_initgroups(user, group))

if GETPGRP_HAVE_ARG:
    c_getpgrp = external('getpgrp', [rffi.INT], rffi.INT)
else:
    c_getpgrp = external('getpgrp', [], rffi.INT)
if SETPGRP_HAVE_ARG:
    c_setpgrp = external('setpgrp', [rffi.INT, rffi.INT], rffi.INT)
else:
    c_setpgrp = external('setpgrp', [], rffi.INT)

@replace_os_function('getpgrp')
def getpgrp():
    if GETPGRP_HAVE_ARG:
        return handle_posix_error('getpgrp', c_getpgrp(0))
    else:
        return handle_posix_error('getpgrp', c_getpgrp())

@replace_os_function('setpgrp')
def setpgrp():
    if SETPGRP_HAVE_ARG:
        handle_posix_error('setpgrp', c_setpgrp(0, 0))
    else:
        handle_posix_error('setpgrp', c_setpgrp())

c_tcgetpgrp = external('tcgetpgrp', [rffi.INT], rffi.PID_T)
c_tcsetpgrp = external('tcsetpgrp', [rffi.INT, rffi.PID_T], rffi.INT)

@replace_os_function('tcgetpgrp')
def tcgetpgrp(fd):
    return handle_posix_error('tcgetpgrp', c_tcgetpgrp(fd))

@replace_os_function('tcsetpgrp')
def tcsetpgrp(fd, pgrp):
    return handle_posix_error('tcsetpgrp', c_tcsetpgrp(fd, pgrp))

#___________________________________________________________________

c_getuid = external('getuid', [], rffi.INT)
c_geteuid = external('geteuid', [], rffi.INT)
c_setuid = external('setuid', [rffi.INT], rffi.INT)
c_seteuid = external('seteuid', [rffi.INT], rffi.INT)
c_getgid = external('getgid', [], rffi.INT)
c_getegid = external('getegid', [], rffi.INT)
c_setgid = external('setgid', [rffi.INT], rffi.INT)
c_setegid = external('setegid', [rffi.INT], rffi.INT)

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
        return (widen(out[0]), widen(out[1]), widen(out[2]))
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
        return (widen(out[0]), widen(out[1]), widen(out[2]))
    finally:
        lltype.free(out, flavor='raw')

@replace_os_function('setresuid')
def setresuid(ruid, euid, suid):
    handle_posix_error('setresuid', c_setresuid(ruid, euid, suid))

@replace_os_function('setresgid')
def setresgid(rgid, egid, sgid):
    handle_posix_error('setresgid', c_setresgid(rgid, egid, sgid))

#___________________________________________________________________

c_chroot = external('chroot', [rffi.CCHARP], rffi.INT)

@replace_os_function('chroot')
def chroot(path):
    handle_posix_error('chroot', c_chroot(_as_bytes0(path)))

if not _WIN32:
    CHARARRAY1 = lltype.FixedSizeArray(lltype.Char, 1)
    class CConfig:
        _compilation_info_ = ExternalCompilationInfo(
            includes = ['sys/utsname.h']
        )
        UTSNAME = rffi_platform.Struct('struct utsname', [
            ('sysname',  CHARARRAY1),
            ('nodename', CHARARRAY1),
            ('release',  CHARARRAY1),
            ('version',  CHARARRAY1),
            ('machine',  CHARARRAY1)])
    config = rffi_platform.configure(CConfig)
    UTSNAMEP = lltype.Ptr(config['UTSNAME'])

    c_uname = external('uname', [UTSNAMEP], rffi.INT,
                        compilation_info=CConfig._compilation_info_)

@replace_os_function('uname')
def uname():
    l_utsbuf = lltype.malloc(UTSNAMEP.TO, flavor='raw')
    try:
        handle_posix_error('uname', c_uname(l_utsbuf))
        return (
            rffi.charp2str(rffi.cast(rffi.CCHARP, l_utsbuf.c_sysname)),
            rffi.charp2str(rffi.cast(rffi.CCHARP, l_utsbuf.c_nodename)),
            rffi.charp2str(rffi.cast(rffi.CCHARP, l_utsbuf.c_release)),
            rffi.charp2str(rffi.cast(rffi.CCHARP, l_utsbuf.c_version)),
            rffi.charp2str(rffi.cast(rffi.CCHARP, l_utsbuf.c_machine)),
        )
    finally:
        lltype.free(l_utsbuf, flavor='raw')

c_makedev = external('makedev', [rffi.INT, rffi.INT], rffi.INT)
c_major = external('major', [rffi.INT], rffi.INT)
c_minor = external('minor', [rffi.INT], rffi.INT)

@replace_os_function('makedev')
def makedev(maj, min):
    return c_makedev(maj, min)

@replace_os_function('major')
def major(dev):
    return c_major(dev)

@replace_os_function('minor')
def minor(dev):
    return c_minor(dev)

#___________________________________________________________________

c_sysconf = external('sysconf', [rffi.INT], rffi.LONG)
c_fpathconf = external('fpathconf', [rffi.INT, rffi.INT], rffi.LONG)
c_pathconf = external('pathconf', [rffi.CCHARP, rffi.INT], rffi.LONG)
c_confstr = external('confstr',
                     [rffi.INT, rffi.CCHARP, rffi.SIZE_T], rffi.SIZE_T)

@replace_os_function('sysconf')
def sysconf(value):
    set_errno(0)
    res = c_sysconf(value)
    if res == -1:
        errno = get_errno()
        if errno != 0:
            raise OSError(errno, "sysconf failed")
    return res

@replace_os_function('fpathconf')
def fpathconf(fd, value):
    set_errno(0)
    res = c_fpathconf(fd, value)
    if res == -1:
        errno = get_errno()
        if errno != 0:
            raise OSError(errno, "fpathconf failed")
    return res

@replace_os_function('pathconf')
def pathconf(path, value):
    set_errno(0)
    res = c_pathconf(_as_bytes0(path), value)
    if res == -1:
        errno = get_errno()
        if errno != 0:
            raise OSError(errno, "pathconf failed")
    return res

@replace_os_function('confstr')
def confstr(value):
    set_errno(0)
    n = intmask(c_confstr(value, lltype.nullptr(rffi.CCHARP.TO), 0))
    if n > 0:
        buf = lltype.malloc(rffi.CCHARP.TO, n, flavor='raw')
        try:
            c_confstr(value, buf, n)
            return rffi.charp2strn(buf, n)
        finally:
            lltype.free(buf, flavor='raw')
    else:
        errno = get_errno()
        if errno != 0:
            raise OSError(errno, "confstr failed")
        return None

