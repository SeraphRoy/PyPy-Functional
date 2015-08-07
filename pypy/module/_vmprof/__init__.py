from pypy.interpreter.mixedmodule import MixedModule

class Module(MixedModule):
    """
    VMProf for PyPy: a statistical profiler
    """
    appleveldefs = {
    }

    interpleveldefs = {
        'enable': 'interp_vmprof.enable',
        'disable': 'interp_vmprof.disable',
        'error': 'space.fromcache(interp_vmprof.Cache).w_error',
    }


# Force the __extend__ hacks and method replacements to occur
# early.  Without this, for example, 'PyCode._init_ready' was
# already found by the annotator to be the original empty
# method, and the annotator doesn't notice that interp_vmprof.py
# (loaded later) replaces this method.
import pypy.module._vmprof.interp_vmprof
