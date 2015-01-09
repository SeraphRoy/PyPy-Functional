
"""
The low-level implementation of termios module
note that this module should only be imported when
termios module is there
"""

import termios
from rpython.rtyper.lltypesystem import rffi
from rpython.rtyper.lltypesystem import lltype
from rpython.rtyper.extfunc import lazy_register, register_external
from rpython.rlib.rarithmetic import intmask
from rpython.rtyper.extregistry import ExtRegistryEntry
from rpython.annotator import model as annmodel
from rpython.rtyper import rclass
from rpython.rlib import rtermios, rposix
from rpython.rtyper.tool import rffi_platform
from rpython.translator.tool.cbuild import ExternalCompilationInfo

eci = ExternalCompilationInfo(
    includes = ['termios.h', 'unistd.h']
)

class CConfig:
    _compilation_info_ = eci
    NCCS = rffi_platform.DefinedConstantInteger('NCCS')
    _HAVE_STRUCT_TERMIOS_C_ISPEED = rffi_platform.Defined(
            '_HAVE_STRUCT_TERMIOS_C_ISPEED')
    _HAVE_STRUCT_TERMIOS_C_OSPEED = rffi_platform.Defined(
            '_HAVE_STRUCT_TERMIOS_C_OSPEED')

c_config = rffi_platform.configure(CConfig)
NCCS = c_config['NCCS']

TCFLAG_T = rffi.UINT
CC_T = rffi.UCHAR
SPEED_T = rffi.UINT
INT = rffi.INT

_add = []
if c_config['_HAVE_STRUCT_TERMIOS_C_ISPEED']:
    _add.append(('c_ispeed', SPEED_T))
if c_config['_HAVE_STRUCT_TERMIOS_C_OSPEED']:
    _add.append(('c_ospeed', SPEED_T))
TERMIOSP = rffi.CStructPtr('termios', ('c_iflag', TCFLAG_T), ('c_oflag', TCFLAG_T),
                           ('c_cflag', TCFLAG_T), ('c_lflag', TCFLAG_T),
                           ('c_line', CC_T),
                           ('c_cc', lltype.FixedSizeArray(CC_T, NCCS)), *_add)

def c_external(name, args, result):
    return rffi.llexternal(name, args, result, compilation_info=eci)

c_tcsendbreak = c_external('tcsendbreak', [INT, INT], INT)
c_tcdrain = c_external('tcdrain', [INT], INT)
c_tcflush = c_external('tcflush', [INT, INT], INT)
c_tcflow = c_external('tcflow', [INT, INT], INT)

# a bit C-c C-v code follows...

def tcsendbreak_llimpl(fd, duration):
    if c_tcsendbreak(fd, duration):
        raise OSError(rposix.get_errno(), 'tcsendbreak failed')
register_external(termios.tcsendbreak, [int, int],
                  llimpl=tcsendbreak_llimpl,
                  export_name='termios.tcsendbreak')

def tcdrain_llimpl(fd):
    if c_tcdrain(fd) < 0:
        raise OSError(rposix.get_errno(), 'tcdrain failed')
register_external(termios.tcdrain, [int], llimpl=tcdrain_llimpl,
                  export_name='termios.tcdrain')

def tcflush_llimpl(fd, queue_selector):
    if c_tcflush(fd, queue_selector) < 0:
        raise OSError(rposix.get_errno(), 'tcflush failed')
register_external(termios.tcflush, [int, int], llimpl=tcflush_llimpl,
                  export_name='termios.tcflush')

def tcflow_llimpl(fd, action):
    if c_tcflow(fd, action) < 0:
        raise OSError(rposix.get_errno(), 'tcflow failed')
register_external(termios.tcflow, [int, int], llimpl=tcflow_llimpl,
                  export_name='termios.tcflow')
