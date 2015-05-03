from rpython.rtyper.extregistry import ExtRegistryEntry
from rpython.rtyper.lltypesystem.lltype import typeOf
from rpython.annotator import model as annmodel
from rpython.annotator.signature import annotation

import py

class ExtFuncEntry(ExtRegistryEntry):
    safe_not_sandboxed = False

    # common case: args is a list of annotation or types
    def normalize_args(self, *args_s):
        args = self.signature_args
        signature_args = [annotation(arg, None) for arg in args]
        assert len(args_s) == len(signature_args),\
               "Argument number mismatch"

        for i, expected in enumerate(signature_args):
            arg = annmodel.unionof(args_s[i], expected)
            if not expected.contains(arg):
                name = getattr(self, 'name', None)
                if not name:
                    try:
                        name = self.instance.__name__
                    except AttributeError:
                        name = '?'
                raise Exception("In call to external function %r:\n"
                                "arg %d must be %s,\n"
                                "          got %s" % (
                    name, i+1, expected, args_s[i]))
        return signature_args

    def compute_result_annotation(self, *args_s):
        self.normalize_args(*args_s)   # check arguments
        return self.signature_result

    def specialize_call(self, hop):
        rtyper = hop.rtyper
        signature_args = self.normalize_args(*hop.args_s)
        args_r = [rtyper.getrepr(s_arg) for s_arg in signature_args]
        args_ll = [r_arg.lowleveltype for r_arg in args_r]
        s_result = hop.s_result
        r_result = rtyper.getrepr(s_result)
        ll_result = r_result.lowleveltype
        name = getattr(self, 'name', None) or self.instance.__name__
        fake_method_name = rtyper.type_system.name[:2] + 'typefakeimpl'
        impl = getattr(self, 'lltypeimpl', None)
        fakeimpl = getattr(self, 'lltypefakeimpl', self.instance)
        if impl:
            if hasattr(self, 'lltypefakeimpl'):
                # If we have both an llimpl and an llfakeimpl,
                # we need a wrapper that selects the proper one and calls it
                from rpython.tool.sourcetools import func_with_new_name
                # Using '*args' is delicate because this wrapper is also
                # created for init-time functions like llarena.arena_malloc
                # which are called before the GC is fully initialized
                args = ', '.join(['arg%d' % i for i in range(len(args_ll))])
                d = {'original_impl': impl,
                     's_result': s_result,
                     'fakeimpl': fakeimpl,
                     '__name__': __name__,
                     }
                exec py.code.compile("""
                    from rpython.rlib.objectmodel import running_on_llinterp
                    from rpython.rlib.debug import llinterpcall
                    from rpython.rlib.jit import dont_look_inside
                    # note: we say 'dont_look_inside' mostly because the
                    # JIT does not support 'running_on_llinterp', but in
                    # theory it is probably right to stop jitting anyway.
                    @dont_look_inside
                    def ll_wrapper(%s):
                        if running_on_llinterp:
                            return llinterpcall(s_result, fakeimpl, %s)
                        else:
                            return original_impl(%s)
                """ % (args, args, args)) in d
                impl = func_with_new_name(d['ll_wrapper'], name + '_wrapper')
            if rtyper.annotator.translator.config.translation.sandbox:
                impl._dont_inline_ = True
            # store some attributes to the 'impl' function, where
            # the eventual call to rtyper.getcallable() will find them
            # and transfer them to the final lltype.functionptr().
            impl._llfnobjattrs_ = {
                '_name': self.name,
                '_safe_not_sandboxed': self.safe_not_sandboxed,
                }
            obj = rtyper.getannmixlevel().delayedfunction(
                impl, signature_args, hop.s_result)
        else:
            #if not self.safe_not_sandboxed:
            #    print '>>>>>>>>>>>>>-----------------------------------'
            #    print name, self.name
            #    print '<<<<<<<<<<<<<-----------------------------------'
            obj = rtyper.type_system.getexternalcallable(args_ll, ll_result,
                                 name, _external_name=self.name, _callable=fakeimpl,
                                 _safe_not_sandboxed=self.safe_not_sandboxed)
        vlist = [hop.inputconst(typeOf(obj), obj)] + hop.inputargs(*args_r)
        hop.exception_is_here()
        return hop.genop('direct_call', vlist, r_result)

def register_external(function, args, result=None, export_name=None,
                       llimpl=None, llfakeimpl=None, sandboxsafe=False):
    """
    function: the RPython function that will be rendered as an external function (e.g.: math.floor)
    args: a list containing the annotation of the arguments
    result: surprisingly enough, the annotation of the result
    export_name: the name of the function as it will be seen by the backends
    llimpl: optional; if provided, this RPython function is called instead of the target function
    llfakeimpl: optional; if provided, called by the llinterpreter
    sandboxsafe: use True if the function performs no I/O (safe for --sandbox)
    """

    if export_name is None:
        export_name = function.__name__

    class FunEntry(ExtFuncEntry):
        _about_ = function
        safe_not_sandboxed = sandboxsafe

        if args is None:
            def normalize_args(self, *args_s):
                return args_s    # accept any argument unmodified
        elif callable(args):
            # custom annotation normalizer (see e.g. os.utime())
            normalize_args = staticmethod(args)
        else: # use common case behavior
            signature_args = args

        signature_result = annotation(result, None)
        name = export_name
        if llimpl:
            lltypeimpl = staticmethod(llimpl)
        if llfakeimpl:
            lltypefakeimpl = staticmethod(llfakeimpl)

    if export_name:
        FunEntry.__name__ = export_name
    else:
        FunEntry.__name__ = function.func_name

def is_external(func):
    if hasattr(func, 'value'):
        func = func.value
    if hasattr(func, '_external_name'):
        return True
    return False
