import py
from rpython.translator import cdir
from rpython.translator.tool.cbuild import ExternalCompilationInfo
from rpython.rtyper.lltypesystem import lltype, llmemory, rffi
from rpython.rtyper.extregistry import ExtRegistryEntry

# these functions manipulate directly the GIL, whose definition does not
# escape the C code itself
translator_c_dir = py.path.local(cdir)

eci = ExternalCompilationInfo(
    includes = ['src/thread.h'],
    separate_module_files = [translator_c_dir / 'src' / 'thread.c'],
    include_dirs = [translator_c_dir])

llexternal = rffi.llexternal


gil_allocate      = llexternal('RPyGilAllocate', [], lltype.Void,
                               _nowrapper=True, sandboxsafe=True,
                               compilation_info=eci)

_gil_yield_thread = llexternal('RPyGilYieldThread', [], lltype.Signed,
                               _nowrapper=True, sandboxsafe=True,
                               compilation_info=eci)

_gil_release      = llexternal('RPyGilRelease', [], lltype.Void,
                               _nowrapper=True, sandboxsafe=True,
                               compilation_info=eci)

_gil_acquire      = llexternal('RPyGilAcquire', [], lltype.Void,
                              _nowrapper=True, sandboxsafe=True,
                              compilation_info=eci)

gil_fetch_fastgil = llexternal('RPyFetchFastGil', [], llmemory.Address,
                               _nowrapper=True, sandboxsafe=True,
                               compilation_info=eci)

# ____________________________________________________________


# NOTE: a multithreaded program should call gil_allocate() once before
# starting to use threads, and possibly after a fork() too


def invoke_after_thread_switch(callback):
    """Invoke callback() after a thread switch.

    This is a hook used by pypy.module.signal.  Several callbacks should
    be easy to support (but not right now).

    This function can only be called if we_are_translated(), but registers
    the callback statically.  The exact point at which
    invoke_after_thread_switch() is called has no importance: the
    callback() will be called anyway.
    """
    raise TypeError("invoke_after_thread_switch() is meant to be translated "
                    "and not called directly")

def _after_thread_switch():
    """NOT_RPYTHON"""


class Entry(ExtRegistryEntry):
    _about_ = invoke_after_thread_switch

    def compute_result_annotation(self, s_callback):
        assert s_callback.is_constant()
        callback = s_callback.const
        bk = self.bookkeeper
        translator = bk.annotator.translator
        if hasattr(translator, '_rgil_invoke_after_thread_switch'):
            assert translator._rgil_invoke_after_thread_switch == callback, (
                "not implemented yet: several invoke_after_thread_switch()")
        else:
            translator._rgil_invoke_after_thread_switch = callback
        bk.emulate_pbc_call("rgil.invoke_after_thread_switch", s_callback, [])

    def specialize_call(self, hop):
        # the actual call is not done here
        pass

class Entry(ExtRegistryEntry):
    _about_ = _after_thread_switch

    def compute_result_annotation(self):
        # the call has been emulated already in invoke_after_thread_switch()
        pass

    def specialize_call(self, hop):
        translator = hop.rtyper.annotator.translator
        if hasattr(translator, '_rgil_invoke_after_thread_switch'):
            import pdb;pdb.set_trace()
            func = translator._rgil_invoke_after_thread_switch
            graph = translator._graphof(func)
            llfn = self.rtyper.getcallable(graph)
            c_callback = hop.inputconst(lltype.typeOf(llfn), llfn)
            hop.exception_is_here()
            hop.genop("direct_call", [c_callback])
        else:
            hop.exception_cannot_occur()


def release():
    # this function must not raise, in such a way that the exception
    # transformer knows that it cannot raise!
    _gil_release()
release._gctransformer_hint_cannot_collect_ = True
release._dont_reach_me_in_del_ = True

def acquire():
    from rpython.rlib import rthread
    _gil_acquire()
    rthread.gc_thread_run()
    _after_thread_switch()
acquire._gctransformer_hint_cannot_collect_ = True
acquire._dont_reach_me_in_del_ = True

# The _gctransformer_hint_cannot_collect_ hack is needed for
# translations in which the *_external_call() functions are not inlined.
# They tell the gctransformer not to save and restore the local GC
# pointers in the shadow stack.  This is necessary because the GIL is
# not held after the call to gil.release() or before the call
# to gil.acquire().

def yield_thread():
    # explicitly release the gil, in a way that tries to give more
    # priority to other threads (as opposed to continuing to run in
    # the same thread).
    if _gil_yield_thread():
        from rpython.rlib import rthread
        rthread.gc_thread_run()
        _after_thread_switch()
yield_thread._gctransformer_hint_close_stack_ = True
yield_thread._dont_reach_me_in_del_ = True
yield_thread._dont_inline_ = True

# yield_thread() needs a different hint: _gctransformer_hint_close_stack_.
# The *_external_call() functions are themselves called only from the rffi
# module from a helper function that also has this hint.
