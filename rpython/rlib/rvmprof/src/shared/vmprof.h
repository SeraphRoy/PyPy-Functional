#pragma once

#include <unistd.h>

// common defines
#define MARKER_STACKTRACE '\x01'
#define MARKER_VIRTUAL_IP '\x02'
#define MARKER_TRAILER '\x03'
#define MARKER_INTERP_NAME '\x04'   /* deprecated */
#define MARKER_HEADER '\x05'
#define MARKER_TIME_N_ZONE '\x06'
#define MARKER_META '\x07'
#define MARKER_NATIVE_SYMBOLS '\x08'

#define VERSION_BASE '\x00'
#define VERSION_THREAD_ID '\x01'
#define VERSION_TAG '\x02'
#define VERSION_MEMORY '\x03'
#define VERSION_MODE_AWARE '\x04'
#define VERSION_DURATION '\x05'
#define VERSION_TIMESTAMP '\x06'

#define PROFILE_MEMORY '\x01'
#define PROFILE_LINES  '\x02'
#define PROFILE_NATIVE '\x04'
#define PROFILE_RPYTHON '\x08'

#ifdef VMPROF_UNIX
#define VMP_SUPPORTS_NATIVE_PROFILING
#endif

#ifdef __x86_64__
#define X86_64
#elif defined(__i386__)
#define X86_32
#endif

#ifdef RPYTHON_VMPROF
// only for pypy
#include "rvmprof.h"
#include "vmprof_stack.h"
#define PY_STACK_FRAME_T vmprof_stack_t
#define PY_EVAL_RETURN_T void
#define PY_THREAD_STATE_T void
#define FRAME_STEP(f) f->next
#define FRAME_CODE(f) f->

#ifdef RPYTHON_LL2CTYPES
#  define IS_VMPROF_EVAL(PTR) 0
#else
    // Is there is a way to tell the compiler
    // that this prototype can have ANY return value. Just removing
    // the return type will default to int
    typedef long Signed;
    RPY_EXTERN Signed __vmprof_eval_vmprof();
#  define IS_VMPROF_EVAL(PTR) PTR == (void*)__vmprof_eval_vmprof
#endif


#else
#define RPY_EXTERN
// for cpython
#include "_vmprof.h"
#include <Python.h>
#include <frameobject.h>
#define PY_STACK_FRAME_T PyFrameObject
#define PY_EVAL_RETURN_T PyObject
#define PY_THREAD_STATE_T PyThreadState
#define FRAME_STEP(f) f->f_back
#define FRAME_CODE(f) f->f_code
PY_EVAL_RETURN_T * vmprof_eval(PY_STACK_FRAME_T *f, int throwflag);
#define VMPROF_EVAL() vmprof_eval
#define IS_VMPROF_EVAL(PTR) PTR == (void*)vmprof_eval
#endif


