import sys

from pypy.interpreter.baseobjspace import W_Root, SpaceCache
from rpython.rtyper.lltypesystem import rffi, lltype
from rpython.rtyper.extregistry import ExtRegistryEntry
from pypy.module.cpyext.api import (
    cpython_api, bootstrap_function, PyObject, PyObjectP, ADDR,
    CANNOT_FAIL, Py_TPFLAGS_HEAPTYPE, PyTypeObjectPtr,
    INTERPLEVEL_API)
from pypy.module.cpyext.state import State
from pypy.objspace.std.typeobject import W_TypeObject
from pypy.objspace.std.objectobject import W_ObjectObject
from rpython.rlib.objectmodel import specialize, we_are_translated
from rpython.rlib.objectmodel import keepalive_until_here
from rpython.rtyper.annlowlevel import llhelper
from rpython.rlib import rawrefcount


#________________________________________________________
# type description

class BaseCpyTypedescr(object):
    basestruct = PyObject.TO
    W_BaseObject = W_ObjectObject

    def get_dealloc(self, space):
        from pypy.module.cpyext.typeobject import subtype_dealloc
        return llhelper(
            subtype_dealloc.api_func.functype,
            subtype_dealloc.api_func.get_wrapper(space))

    def allocate(self, space, w_type, itemcount=0):
        # similar to PyType_GenericAlloc?
        # except that it's not related to any pypy object.

        pytype = make_ref(space, w_type)
        pytype = rffi.cast(PyTypeObjectPtr, pytype)
        assert pytype
        # Don't increase refcount for non-heaptypes
        flags = rffi.cast(lltype.Signed, pytype.c_tp_flags)
        if not flags & Py_TPFLAGS_HEAPTYPE:
            Py_DecRef(space, w_type)

        if pytype:
            size = pytype.c_tp_basicsize
        else:
            size = rffi.sizeof(self.basestruct)
        if itemcount:
            size += itemcount * pytype.c_tp_itemsize
        buf = lltype.malloc(rffi.VOIDP.TO, size,
                            flavor='raw', zero=True)
        pyobj = rffi.cast(PyObject, buf)
        pyobj.c_ob_refcnt = 1
        pyobj.c_ob_type = pytype
        return pyobj

    def attach(self, space, pyobj, w_obj):
        pass

    def realize(self, space, obj):
        w_type = from_ref(space, rffi.cast(PyObject, obj.c_ob_type))
        w_obj = space.allocate_instance(self.W_BaseObject, w_type)
        track_reference(space, obj, w_obj)
        if w_type is not space.gettypefor(self.W_BaseObject):
            state = space.fromcache(RefcountState)
            state.set_lifeline(w_obj, obj)
        return w_obj

typedescr_cache = {}

def make_typedescr(typedef, **kw):
    """NOT_RPYTHON

    basestruct: The basic structure to allocate
    alloc     : allocate and basic initialization of a raw PyObject
    attach    : Function called to tie a raw structure to a pypy object
    realize   : Function called to create a pypy object from a raw struct
    dealloc   : a cpython_api(external=False), similar to PyObject_dealloc
    """

    tp_basestruct = kw.pop('basestruct', PyObject.TO)
    tp_alloc      = kw.pop('alloc', None)
    tp_attach     = kw.pop('attach', None)
    tp_realize    = kw.pop('realize', None)
    tp_dealloc    = kw.pop('dealloc', None)
    assert not kw, "Extra arguments to make_typedescr"

    null_dealloc = lltype.nullptr(lltype.FuncType([PyObject], lltype.Void))

    class CpyTypedescr(BaseCpyTypedescr):
        basestruct = tp_basestruct

        if tp_alloc:
            def allocate(self, space, w_type, itemcount=0):
                return tp_alloc(space, w_type)

        if tp_dealloc:
            def get_dealloc(self, space):
                return llhelper(
                    tp_dealloc.api_func.functype,
                    tp_dealloc.api_func.get_wrapper(space))

        if tp_attach:
            def attach(self, space, pyobj, w_obj):
                tp_attach(space, pyobj, w_obj)

        if tp_realize:
            def realize(self, space, ref):
                return tp_realize(space, ref)
    if typedef:
        CpyTypedescr.__name__ = "CpyTypedescr_%s" % (typedef.name,)

    typedescr_cache[typedef] = CpyTypedescr()

@bootstrap_function
def init_pyobject(space):
    from pypy.module.cpyext.object import PyObject_dealloc
    # typedescr for the 'object' type
    make_typedescr(space.w_object.instancetypedef,
                   dealloc=PyObject_dealloc)
    # almost all types, which should better inherit from object.
    make_typedescr(None)

@specialize.memo()
def _get_typedescr_1(typedef):
    try:
        return typedescr_cache[typedef]
    except KeyError:
        if typedef.bases:
            return _get_typedescr_1(typedef.bases[0])
        return typedescr_cache[None]

def get_typedescr(typedef):
    if typedef is None:
        return typedescr_cache[None]
    else:
        return _get_typedescr_1(typedef)

#________________________________________________________
# refcounted object support

class InvalidPointerException(Exception):
    pass

DEBUG_REFCOUNT = False

def debug_refcount(*args, **kwargs):
    frame_stackdepth = kwargs.pop("frame_stackdepth", 2)
    assert not kwargs
    frame = sys._getframe(frame_stackdepth)
    print >>sys.stderr, "%25s" % (frame.f_code.co_name, ),
    for arg in args:
        print >>sys.stderr, arg,
    print >>sys.stderr

def create_ref(space, w_obj, itemcount=0):
    """
    Allocates a PyObject, and fills its fields with info from the given
    interpreter object.
    """
    #state = space.fromcache(RefcountState)
    w_type = space.type(w_obj)
    if w_type.is_cpytype():
        ZZZ # py_obj = state.get_from_lifeline(w_obj)
        if py_obj:
            Py_IncRef(space, py_obj)
            return py_obj

    typedescr = get_typedescr(w_obj.typedef)
    py_obj = typedescr.allocate(space, w_type, itemcount=itemcount)
    track_reference(space, py_obj, w_obj)
    typedescr.attach(space, py_obj, w_obj)
    return py_obj

def track_reference(space, py_obj, w_obj):
    """
    Ties together a PyObject and an interpreter object.
    """
    # XXX looks like a PyObject_GC_TRACK
    assert py_obj.c_ob_refcnt < rawrefcount.REFCNT_FROM_PYPY
    py_obj.c_ob_refcnt += rawrefcount.REFCNT_FROM_PYPY
    if DEBUG_REFCOUNT:
        debug_refcount("MAKREF", py_obj, w_obj)
        assert w_obj
        assert py_obj
    rawrefcount.create_link_pypy(w_obj, py_obj)

def make_ref(space, w_obj):
    """
    Returns a new reference to an intepreter object.
    """
    GOES_AWAY
    if w_obj is None:
        return lltype.nullptr(PyObject.TO)
    assert isinstance(w_obj, W_Root)
    state = space.fromcache(RefcountState)
    try:
        py_obj = state.py_objects_w2r[w_obj]
    except KeyError:
        py_obj = create_ref(space, w_obj)
        track_reference(space, py_obj, w_obj)
    else:
        Py_IncRef(space, py_obj)
    return py_obj


def from_ref(space, ref):
    """
    Finds the interpreter object corresponding to the given reference.  If the
    object is not yet realized (see stringobject.py), creates it.
    """
    GOES_AWAY
    assert lltype.typeOf(ref) == PyObject
    if not ref:
        return None
    state = space.fromcache(RefcountState)
    ptr = rffi.cast(ADDR, ref)

    try:
        return state.py_objects_r2w[ptr]
    except KeyError:
        pass

    # This reference is not yet a real interpreter object.
    # Realize it.
    ref_type = rffi.cast(PyObject, ref.c_ob_type)
    if ref_type == ref: # recursion!
        raise InvalidPointerException(str(ref))
    w_type = from_ref(space, ref_type)
    assert isinstance(w_type, W_TypeObject)
    return get_typedescr(w_type.instancetypedef).realize(space, ref)


def debug_collect():
    rawrefcount._collect(track_allocation=False)


def as_pyobj(space, w_obj):
    """
    Returns a 'PyObject *' representing the given intepreter object.
    This doesn't give a new reference, but the returned 'PyObject *'
    is valid at least as long as 'w_obj' is.  **To be safe, you should
    use keepalive_until_here(w_obj) some time later.**  In case of
    doubt, use the safer make_ref().
    """
    if w_obj is not None:
        assert not is_pyobj(w_obj)
        py_obj = rawrefcount.from_obj(PyObject, w_obj)
        if not py_obj:
            py_obj = create_ref(space, w_obj)
            #track_reference(space, py_obj, w_obj) -- included with create_ref()
        return py_obj
    else:
        return lltype.nullptr(PyObject.TO)
as_pyobj._always_inline_ = 'try'
INTERPLEVEL_API['as_pyobj'] = as_pyobj

def pyobj_has_w_obj(pyobj):
    return rawrefcount.to_obj(W_Root, pyobj) is not None
INTERPLEVEL_API['pyobj_has_w_obj'] = staticmethod(pyobj_has_w_obj)

@specialize.ll()
def from_pyobj(space, pyobj):
    assert is_pyobj(pyobj)
    if pyobj:
        pyobj = rffi.cast(PyObject, pyobj)
        w_obj = rawrefcount.to_obj(W_Root, pyobj)
        if w_obj is None:
            XXXXXXXXXXX
        return w_obj
    else:
        return None
from_pyobj._always_inline_ = 'try'
INTERPLEVEL_API['from_pyobj'] = from_pyobj


def is_pyobj(x):
    if x is None or isinstance(x, W_Root):
        return False
    elif is_PyObject(lltype.typeOf(x)):
        return True
    else:
        raise TypeError(repr(type(x)))
INTERPLEVEL_API['is_pyobj'] = staticmethod(is_pyobj)

class Entry(ExtRegistryEntry):
    _about_ = is_pyobj
    def compute_result_annotation(self, s_x):
        from rpython.rtyper.llannotation import SomePtr
        return self.bookkeeper.immutablevalue(isinstance(s_x, SomePtr))
    def specialize_call(self, hop):
        hop.exception_cannot_occur()
        return hop.inputconst(lltype.Bool, hop.s_result.const)

@specialize.ll()
def make_ref(space, obj):
    """Increment the reference counter of the PyObject and return it.
    Can be called with either a PyObject or a W_Root.
    """
    if obj:
        if is_pyobj(obj):
            pyobj = rffi.cast(PyObject, obj)
        else:
            pyobj = as_pyobj(space, obj)
        assert pyobj.c_ob_refcnt > 0
        pyobj.c_ob_refcnt += 1
        if not is_pyobj(obj):
            keepalive_until_here(obj)
        return pyobj
    else:
        return lltype.nullptr(PyObject.TO)
INTERPLEVEL_API['make_ref'] = make_ref


@specialize.ll()
def get_w_obj_and_decref(space, obj):
    """Decrement the reference counter of the PyObject and return the
    corresponding W_Root object (so the reference count is at least
    REFCNT_FROM_PYPY and cannot be zero).  Can be called with either
    a PyObject or a W_Root.
    """
    if is_pyobj(obj):
        pyobj = rffi.cast(PyObject, obj)
        w_obj = from_pyobj(space, pyobj)
    else:
        w_obj = obj
        pyobj = as_pyobj(space, w_obj)
    if pyobj:
        pyobj.c_ob_refcnt -= 1
        assert pyobj.c_ob_refcnt >= rawrefcount.REFCNT_FROM_PYPY
        keepalive_until_here(w_obj)
    return w_obj
INTERPLEVEL_API['get_w_obj_and_decref'] = get_w_obj_and_decref


@specialize.ll()
def incref(space, obj):
    make_ref(space, obj)
INTERPLEVEL_API['incref'] = incref

@specialize.ll()
def decref(space, obj):
    if is_pyobj(obj):
        obj = rffi.cast(PyObject, obj)
        if obj:
            assert obj.c_ob_refcnt > 0
            obj.c_ob_refcnt -= 1
            if obj.c_ob_refcnt == 0:
                _Py_Dealloc(space, obj)
    else:
        get_w_obj_and_decref(space, obj)
INTERPLEVEL_API['decref'] = decref


@cpython_api([PyObject], lltype.Void)
def Py_IncRef(space, obj):
    incref(obj)

@cpython_api([PyObject], lltype.Void)
def Py_DecRef(space, obj):
    decref(space, obj)

@cpython_api([PyObject], lltype.Void)
def _Py_NewReference(space, obj):
    obj.c_ob_refcnt = 1
    w_type = from_ref(space, rffi.cast(PyObject, obj.c_ob_type))
    assert isinstance(w_type, W_TypeObject)
    get_typedescr(w_type.instancetypedef).realize(space, obj)

@cpython_api([PyObject], lltype.Void)
def _Py_Dealloc(space, obj):
    from pypy.module.cpyext.api import generic_cpy_call_dont_decref
    pto = obj.c_ob_type
    #print >>sys.stderr, "Calling dealloc slot", pto.c_tp_dealloc, "of", obj, \
    #      "'s type which is", rffi.charp2str(pto.c_tp_name)
    generic_cpy_call_dont_decref(space, pto.c_tp_dealloc, obj)

@cpython_api([rffi.VOIDP], lltype.Signed, error=CANNOT_FAIL)
def _Py_HashPointer(space, ptr):
    return rffi.cast(lltype.Signed, ptr)


class RefcountState:
    def __init__(self, *args):
        GOES_AWAY

def borrow_from(container, borrowed):
    GOES_AWAY
