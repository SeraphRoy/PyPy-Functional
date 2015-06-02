from rpython.jit.codewriter.effectinfo import EffectInfo
from rpython.jit.metainterp.executor import execute
from rpython.jit.codewriter.heaptracker import vtable2descr, descr2vtable
from rpython.jit.metainterp.history import Const, ConstInt, BoxInt
from rpython.jit.metainterp.history import CONST_NULL, BoxPtr
from rpython.jit.metainterp.optimizeopt import info, optimizer
from rpython.jit.metainterp.optimizeopt.optimizer import REMOVED
from rpython.jit.metainterp.optimizeopt.util import (make_dispatcher_method,
                                                     descrlist_dict, sort_descrs)

from rpython.jit.metainterp.optimizeopt.rawbuffer import RawBuffer, InvalidRawOperation
from rpython.jit.metainterp.resoperation import rop, ResOperation
from rpython.rlib.objectmodel import we_are_translated, specialize
from rpython.jit.metainterp.optimizeopt.intutils import IntUnbounded

class AbstractVirtualInfo(info.PtrInfo):
    _attrs_ = ('_cached_vinfo',)
    is_about_raw = False
    _cached_vinfo = None

    def is_forced_virtual(self):
        xxx
        return self.box is not None
    
    #def force_box(self, optforce):
    #    xxxx
    #    if self.box is None:
    #        optforce.forget_numberings(self.source_op)
    #        self._really_force(optforce)
    #    return self.box

    def force_at_end_of_preamble(self, already_forced, optforce):
        xxxx
        value = already_forced.get(self, None)
        if value:
            return value
        return OptValue(self.force_box(optforce))

    def visitor_walk_recursive(self, visitor):
        # checks for recursion: it is False unless
        # we have already seen the very same keybox
        if self.box is None and not visitor.already_seen_virtual(self.source_op):
            self._visitor_walk_recursive(visitor)

    def _visitor_walk_recursive(self, visitor):
        raise NotImplementedError("abstract base")

    @specialize.argtype(1)
    def _visitor_dispatch_virtual_type(self, visitor):
        raise NotImplementedError("abstract base")

    def _really_force(self, optforce):
        raise NotImplementedError("abstract base")

    def import_from(self, other, optimizer):
        raise NotImplementedError("should not be called at this level")

def get_fielddescrlist_cache(cpu):
    if not hasattr(cpu, '_optimizeopt_fielddescrlist_cache'):
        result = descrlist_dict()
        cpu._optimizeopt_fielddescrlist_cache = result
        return result
    return cpu._optimizeopt_fielddescrlist_cache
get_fielddescrlist_cache._annspecialcase_ = "specialize:memo"

class AbstractVirtualStructInfo(AbstractVirtualInfo):
    _attrs_ = ('_fields',)

    def __init__(self):
        AbstractVirtualInfo.__init__(self)
        #self._fields = {}

    def getfield(self, ofs, default):
        return self._fields.get(ofs, default)

    def setfield(self, ofs, fieldvalue):
        assert isinstance(fieldvalue, optimizer.OptValue)
        self._fields[ofs] = fieldvalue

    def _get_descr(self):
        raise NotImplementedError

    def _is_immutable_and_filled_with_constants(self, memo=None):
        # check if it is possible to force the given structure into a
        # compile-time constant: this is allowed only if it is declared
        # immutable, if all fields are already filled, and if each field
        # is either a compile-time constant or (recursively) a structure
        # which also answers True to the same question.
        #
        # check that all fields are filled.  The following equality check
        # also fails if count == -1, meaning "not an immutable at all".
        count = self._get_descr().count_fields_if_immutable()
        if count != len(self._fields):
            return False
        #
        # initialize 'memo'
        if memo is None:
            memo = {}
        elif self in memo:
            return True   # recursive case: assume yes
        memo[self] = None
        #
        for value in self._fields.itervalues():
            if value.is_constant():
                pass            # it is a constant value: ok
            elif (isinstance(value, AbstractVirtualStructValue)
                  and value.is_virtual()):
                # recursive check
                if not value._is_immutable_and_filled_with_constants(memo):
                    return False
            else:
                return False    # not a constant at all
        return True

    def force_at_end_of_preamble(self, already_forced, optforce):
        if self in already_forced:
            return self
        already_forced[self] = self
        if self._fields:
            for ofs in self._fields.keys():
                self._fields[ofs] = self._fields[ofs].force_at_end_of_preamble(already_forced, optforce)
        return self

    def _really_force(self, optforce):
        op = self.source_op
        assert op is not None
        # ^^^ This case should not occur any more (see test_bug_3).
        #
        if not we_are_translated():
            op.name = 'FORCE ' + self.source_op.name

        if self._is_immutable_and_filled_with_constants():
            box = optforce.optimizer.constant_fold(op)
            self.make_constant(box)
            for ofs, value in self._fields.iteritems():
                subbox = value.force_box(optforce)
                assert isinstance(subbox, Const)
                execute(optforce.optimizer.cpu, None, rop.SETFIELD_GC,
                        ofs, box, subbox)
            # keep self._fields, because it's all immutable anyway
        else:
            optforce.emit_operation(op)
            op = optforce.getlastop()
            self.box = box = op
            #
            iteritems = self._fields.iteritems()
            if not we_are_translated(): #random order is fine, except for tests
                iteritems = list(iteritems)
                iteritems.sort(key=lambda (x, y): x.sort_key())
            for ofs, value in iteritems:
                subbox = value.force_box(optforce)
                op = ResOperation(rop.SETFIELD_GC, [box, subbox], descr=ofs)
                optforce.emit_operation(op)

    def _get_field_descr_list(self):
        _cached_sorted_fields = self._cached_sorted_fields
        if self._fields is None:
            nfields = 0
        else:
            nfields = len(self._fields)
        if (_cached_sorted_fields is not None and
            nfields == len(_cached_sorted_fields)):
            lst = self._cached_sorted_fields
        else:
            if self._fields is None:
                lst = []
            else:
                lst = self._fields.keys()
            sort_descrs(lst)
            cache = get_fielddescrlist_cache(self.cpu)
            result = cache.get(lst, None)
            if result is None:
                cache[lst] = lst
            else:
                lst = result
            # store on self, to not have to repeatedly get it from the global
            # cache, which involves sorting
            self._cached_sorted_fields = lst
        return lst

    def _visitor_walk_recursive(self, visitor):
        lst = self._get_field_descr_list()
        fieldboxes = [self._fields[ofs].get_key_box() for ofs in lst]
        visitor.register_virtual_fields(self.source_op, fieldboxes)
        for ofs in lst:
            fieldvalue = self._fields[ofs]
            fieldvalue.visitor_walk_recursive(visitor)

class VirtualInfo(AbstractVirtualStructInfo):

    def __init__(self, known_class, descr):
        AbstractVirtualStructInfo.__init__(self)
        assert isinstance(known_class, Const)
        self.known_class = known_class
        self.descr = descr

    @specialize.argtype(1)
    def _visitor_dispatch_virtual_type(self, visitor):
        fielddescrs = self._get_field_descr_list()
        return visitor.visit_virtual(self.known_class, fielddescrs)

    def _get_descr(self):
        return vtable2descr(self.cpu, self.known_class.getint())

    def __repr__(self):
        cls_name = self.known_class.value.adr.ptr._obj._TYPE._name
        if self._fields is None:
            return '<VirtualValue FORCED cls=%s>' % (cls_name,)
        field_names = [field.name for field in self._fields]
        return "<VirtualValue cls=%s fields=%s>" % (cls_name, field_names)

class VStructInfo(AbstractVirtualStructInfo):

    def __init__(self, cpu, structdescr, source_op):
        xxx
        AbstractVirtualStructValue.__init__(self, cpu, source_op)
        self.structdescr = structdescr

    @specialize.argtype(1)
    def _visitor_dispatch_virtual_type(self, visitor):
        fielddescrs = self._get_field_descr_list()
        return visitor.visit_vstruct(self.structdescr, fielddescrs)

    def _get_descr(self):
        return self.structdescr

class AbstractVArrayInfo(AbstractVirtualInfo):
    """
    Base class for VArrayValue (for normal GC arrays) and VRawBufferValue (for
    malloc()ed memory)
    """

    def getlength(self):
        return len(self._items)

    def get_item_value(self, i):
        raise NotImplementedError

    def set_item_value(self, i, newval):
        raise NotImplementedError

    def _visitor_walk_recursive(self, visitor):
        itemboxes = []
        for i in range(self.getlength()):
            itemvalue = self.get_item_value(i)
            if itemvalue is not None:
                box = itemvalue.get_key_box()
            else:
                box = None
            itemboxes.append(box)
        visitor.register_virtual_fields(self.source_op, itemboxes)
        for i in range(self.getlength()):
            itemvalue = self.get_item_value(i)
            if itemvalue is not None:
                itemvalue.visitor_walk_recursive(visitor)


class VArrayInfo(AbstractVArrayInfo):

    def __init__(self, arraydescr, constvalue, size, source_op,
                 clear=False):
        AbstractVirtualValue.__init__(self, source_op)
        self.arraydescr = arraydescr
        self.constvalue = constvalue
        if clear:
            self._items = [constvalue] * size
        else:
            self._items = [None] * size
        self.clear = clear

    def getlength(self):
        return len(self._items)

    def get_missing_null_value(self):
        return self.constvalue

    def get_item_value(self, i):
        """Return the i'th item, unless it is 'constvalue' on a 'clear'
        array.  In that case (or if the i'th item is already None),
        return None.  The idea is that this method returns the value
        that must be set into an array that was allocated "correctly",
        i.e. if 'clear' is True, that means with zero=True."""
        subvalue = self._items[i]
        if self.clear and (subvalue is self.constvalue or
                           subvalue.is_null()):
            subvalue = None
        return subvalue

    def set_item_value(self, i, newval):
        self._items[i] = newval

    def getitem(self, index):
        res = self._items[index]
        return res

    def setitem(self, index, itemvalue):
        assert isinstance(itemvalue, optimizer.OptValue)
        self._items[index] = itemvalue        

    def force_at_end_of_preamble(self, already_forced, optforce):
        # note that this method is on VArrayValue instead of
        # AbstractVArrayValue because we do not want to support virtualstate
        # for rawbuffers for now
        if self in already_forced:
            return self
        already_forced[self] = self
        for index in range(self.getlength()):
            itemval = self._items[index]
            # XXX should be skip alltogether, but I don't wanna know or
            #     fight unrolling just yet
            if itemval is None:
                itemval = self.constvalue
            itemval = itemval.force_at_end_of_preamble(already_forced, optforce)
            self.set_item_value(index, itemval)
        return self

    def _really_force(self, optforce):
        assert self.source_op is not None
        if not we_are_translated():
            self.source_op.name = 'FORCE ' + self.source_op.name
        # XXX two possible optimizations:
        # * if source_op is NEW_ARRAY_CLEAR, emit NEW_ARRAY if it's
        #   immediately followed by SETARRAYITEM_GC into all items (hard?)
        # * if source_op is NEW_ARRAY, emit NEW_ARRAY_CLEAR if it's
        #   followed by setting most items to zero anyway
        optforce.emit_operation(self.source_op)
        op = optforce.getlastop() # potentially replaced
        self.box = box = op
        for index in range(len(self._items)):
            subvalue = self._items[index]
            if subvalue is None:
                continue
            if self.clear:
                if subvalue is self.constvalue or subvalue.is_null():
                    continue
            subbox = subvalue.force_box(optforce)
            op = ResOperation(rop.SETARRAYITEM_GC,
                              [box, ConstInt(index), subbox],
                               descr=self.arraydescr)
            optforce.emit_operation(op)

    @specialize.argtype(1)
    def _visitor_dispatch_virtual_type(self, visitor):
        return visitor.visit_varray(self.arraydescr, self.clear)


class VArrayStructInfo(AbstractVirtualInfo):
    def __init__(self, arraydescr, size, source_op):
        AbstractVirtualValue.__init__(self, source_op)
        self.arraydescr = arraydescr
        self._items = [{} for _ in xrange(size)]

    def getlength(self):
        return len(self._items)

    def getinteriorfield(self, index, ofs, default):
        return self._items[index].get(ofs, default)

    def setinteriorfield(self, index, ofs, itemvalue):
        assert isinstance(itemvalue, optimizer.OptValue)
        self._items[index][ofs] = itemvalue

    def _really_force(self, optforce):
        assert self.source_op is not None
        if not we_are_translated():
            self.source_op.name = 'FORCE ' + self.source_op.name
        optforce.emit_operation(self.source_op)
        op = optforce.getlastop()
        self.box = box = op
        for index in range(len(self._items)):
            iteritems = self._items[index].iteritems()
            # random order is fine, except for tests
            if not we_are_translated():
                iteritems = list(iteritems)
                iteritems.sort(key=lambda (x, y): x.sort_key())
            for descr, value in iteritems:
                subbox = value.force_box(optforce)
                op = ResOperation(rop.SETINTERIORFIELD_GC,
                    [box, ConstInt(index), subbox], descr=descr
                )
                optforce.emit_operation(op)

    def _get_list_of_descrs(self):
        descrs = []
        for item in self._items:
            item_descrs = item.keys()
            sort_descrs(item_descrs)
            descrs.append(item_descrs)
        return descrs

    def _visitor_walk_recursive(self, visitor):
        itemdescrs = self._get_list_of_descrs()
        itemboxes = []
        for i in range(len(self._items)):
            for descr in itemdescrs[i]:
                itemboxes.append(self._items[i][descr].get_key_box())
        visitor.register_virtual_fields(self.keybox, itemboxes)
        for i in range(len(self._items)):
            for descr in itemdescrs[i]:
                self._items[i][descr].visitor_walk_recursive(visitor)

    def force_at_end_of_preamble(self, already_forced, optforce):
        if self in already_forced:
            return self
        already_forced[self] = self
        for index in range(len(self._items)):
            for descr in self._items[index].keys():
                self._items[index][descr] = self._items[index][descr].force_at_end_of_preamble(already_forced, optforce)
        return self

    @specialize.argtype(1)
    def _visitor_dispatch_virtual_type(self, visitor):
        return visitor.visit_varraystruct(self.arraydescr, self._get_list_of_descrs())


class VRawBufferInfo(AbstractVArrayInfo):
    is_about_raw = True

    def __init__(self, cpu, logops, size, source_op):
        AbstractVirtualValue.__init__(self, source_op)
        # note that size is unused, because we assume that the buffer is big
        # enough to write/read everything we need. If it's not, it's undefined
        # behavior anyway, although in theory we could probably detect such
        # cases here
        self.size = size
        self.buffer = RawBuffer(cpu, logops)

    def getintbound(self):
        return IntUnbounded()

    def getlength(self):
        return len(self.buffer.values)

    def get_item_value(self, i):
        return self.buffer.values[i]

    def set_item_value(self, i, newval):
        self.buffer.values[i] = newval

    def getitem_raw(self, offset, length, descr):
        if not self.is_virtual():
            raise InvalidRawOperation
            # see 'test_virtual_raw_buffer_forced_but_slice_not_forced'
            # for the test above: it's not enough to check is_virtual()
            # on the original object, because it might be a VRawSliceValue
            # instead.  If it is a virtual one, then we'll reach here anway.
        return self.buffer.read_value(offset, length, descr)

    def setitem_raw(self, offset, length, descr, value):
        if not self.is_virtual():
            raise InvalidRawOperation
        self.buffer.write_value(offset, length, descr, value)

    def _really_force(self, optforce):
        op = self.source_op
        assert op is not None
        if not we_are_translated():
            op.name = 'FORCE ' + self.source_op.name
        optforce.emit_operation(self.source_op)
        self.box = optforce.getlastop()
        for i in range(len(self.buffer.offsets)):
            # write the value
            offset = self.buffer.offsets[i]
            descr = self.buffer.descrs[i]
            itemvalue = self.buffer.values[i]
            itembox = itemvalue.force_box(optforce)
            op = ResOperation(rop.RAW_STORE,
                              [self.box, ConstInt(offset), itembox],
                              descr=descr)
            optforce.emit_operation(op)

    @specialize.argtype(1)
    def _visitor_dispatch_virtual_type(self, visitor):
        # I *think* we need to make a copy of offsets and descrs because we
        # want a snapshot of the virtual state right now: if we grow more
        # elements later, we don't want them to go in this virtual state
        return visitor.visit_vrawbuffer(self.size,
                                        self.buffer.offsets[:],
                                        self.buffer.descrs[:])


class VRawSliceInfo(AbstractVirtualInfo):
    is_about_raw = True

    def __init__(self, rawbuffer_value, offset, source_op):
        AbstractVirtualValue.__init__(self, source_op)
        self.rawbuffer_value = rawbuffer_value
        self.offset = offset

    def getintbound(self):
        return IntUnbounded()

    def _really_force(self, optforce):
        op = self.source_op
        assert op is not None
        if not we_are_translated():
            op.name = 'FORCE ' + self.source_op.name
        self.rawbuffer_value.force_box(optforce)
        optforce.emit_operation(op)
        self.box = optforce.getlastop()

    def setitem_raw(self, offset, length, descr, value):
        self.rawbuffer_value.setitem_raw(self.offset+offset, length, descr, value)

    def getitem_raw(self, offset, length, descr):
        return self.rawbuffer_value.getitem_raw(self.offset+offset, length, descr)

    def _visitor_walk_recursive(self, visitor):
        box = self.rawbuffer_value.get_key_box()
        visitor.register_virtual_fields(self.keybox, [box])
        self.rawbuffer_value.visitor_walk_recursive(visitor)

    @specialize.argtype(1)
    def _visitor_dispatch_virtual_type(self, visitor):
        return visitor.visit_vrawslice(self.offset)


class OptVirtualize(optimizer.Optimization):
    "Virtualize objects until they escape."

    _last_guard_not_forced_2 = None

    def make_virtual(self, known_class, source_op, descr):
        opinfo = info.InstancePtrInfo(known_class, vdescr=descr)
        opinfo.init_fields(descr)
        source_op.set_forwarded(opinfo)
        return opinfo

    def make_varray(self, arraydescr, size, source_op, clear=False):
        if arraydescr.is_array_of_structs():
            assert clear
            opinfo = info.ArrayStructInfo(size, vdescr=arraydescr)
        else:
            const = self.new_const_item(arraydescr)
            opinfo = info.ArrayPtrInfo(const, size, clear, vdescr=arraydescr)
        source_op.set_forwarded(opinfo)
        return opinfo

    def make_vstruct(self, structdescr, source_op):
        opinfo = info.StructPtrInfo(vdescr=structdescr)
        opinfo.init_fields(structdescr)
        source_op.set_forwarded(opinfo)
        return opinfo

    def make_virtual_raw_memory(self, size, source_op):
        raise Exception("unsupported")
        logops = self.optimizer.loop.logops
        vvalue = VRawBufferValue(self.optimizer.cpu, logops, size, source_op)
        self.make_equal_to(source_op, vvalue)
        return vvalue

    def make_virtual_raw_slice(self, rawbuffer_value, offset, source_op):
        raise Exception("unsupported")
        vvalue = VRawSliceValue(rawbuffer_value, offset, source_op)
        self.make_equal_to(source_op, vvalue)
        return vvalue

    def optimize_GUARD_NO_EXCEPTION(self, op):
        if self.last_emitted_operation is REMOVED:
            return
        self.emit_operation(op)

    def optimize_GUARD_NOT_FORCED(self, op):
        if self.last_emitted_operation is REMOVED:
            return
        self.emit_operation(op)

    def optimize_GUARD_NOT_FORCED_2(self, op):
        self._last_guard_not_forced_2 = op

    def optimize_FINISH(self, op):
        if self._last_guard_not_forced_2 is not None:
            guard_op = self._last_guard_not_forced_2
            self.emit_operation(op)
            guard_op = self.optimizer.store_final_boxes_in_guard(guard_op, [])
            i = len(self.optimizer._newoperations) - 1
            assert i >= 0
            self.optimizer._newoperations.insert(i, guard_op)
        else:
            self.emit_operation(op)

    def optimize_CALL_MAY_FORCE_I(self, op):
        effectinfo = op.getdescr().get_extra_info()
        oopspecindex = effectinfo.oopspecindex
        if oopspecindex == EffectInfo.OS_JIT_FORCE_VIRTUAL:
            if self._optimize_JIT_FORCE_VIRTUAL(op):
                return
        self.emit_operation(op)
    optimize_CALL_MAY_FORCE_R = optimize_CALL_MAY_FORCE_I
    optimize_CALL_MAY_FORCE_F = optimize_CALL_MAY_FORCE_I
    optimize_CALL_MAY_FORCE_N = optimize_CALL_MAY_FORCE_I

    def optimize_COND_CALL(self, op):
        effectinfo = op.getdescr().get_extra_info()
        oopspecindex = effectinfo.oopspecindex
        if oopspecindex == EffectInfo.OS_JIT_FORCE_VIRTUALIZABLE:
            opinfo = self.getptrinfo(op.getarg(2))
            if opinfo and opinfo.is_virtual():
                return
        self.emit_operation(op)

    def optimize_VIRTUAL_REF(self, op):
        # get some constants
        vrefinfo = self.optimizer.metainterp_sd.virtualref_info
        c_cls = vrefinfo.jit_virtual_ref_const_class
        vref_descr = vrefinfo.descr
        descr_virtual_token = vrefinfo.descr_virtual_token
        descr_forced = vrefinfo.descr_forced
        #
        # Replace the VIRTUAL_REF operation with a virtual structure of type
        # 'jit_virtual_ref'.  The jit_virtual_ref structure may be forced soon,
        # but the point is that doing so does not force the original structure.
        newop = ResOperation(rop.NEW_WITH_VTABLE, [], descr=vref_descr)
        vrefvalue = self.make_virtual(c_cls, newop, vref_descr)
        op.set_forwarded(newop)
        newop.set_forwarded(vrefvalue)
        token = ResOperation(rop.FORCE_TOKEN, [])
        self.emit_operation(token)
        vrefvalue.setfield(descr_virtual_token, token)
        vrefvalue.setfield(descr_forced, self.optimizer.cpu.ts.CONST_NULLREF)

    def optimize_VIRTUAL_REF_FINISH(self, op):
        # This operation is used in two cases.  In normal cases, it
        # is the end of the frame, and op.getarg(1) is NULL.  In this
        # case we just clear the vref.virtual_token, because it contains
        # a stack frame address and we are about to leave the frame.
        # In that case vref.forced should still be NULL, and remains
        # NULL; and accessing the frame through the vref later is
        # *forbidden* and will raise InvalidVirtualRef.
        #
        # In the other (uncommon) case, the operation is produced
        # earlier, because the vref was forced during tracing already.
        # In this case, op.getarg(1) is the virtual to force, and we
        # have to store it in vref.forced.
        #
        vrefinfo = self.optimizer.metainterp_sd.virtualref_info
        seo = self.optimizer.send_extra_operation

        # - set 'forced' to point to the real object
        objbox = op.getarg(1)
        if not CONST_NULL.same_constant(objbox):
            seo(ResOperation(rop.SETFIELD_GC, op.getarglist(),
                             descr=vrefinfo.descr_forced))

        # - set 'virtual_token' to TOKEN_NONE (== NULL)
        args = [op.getarg(0), CONST_NULL]
        seo(ResOperation(rop.SETFIELD_GC, args,
                         descr=vrefinfo.descr_virtual_token))
        # Note that in some cases the virtual in op.getarg(1) has been forced
        # already.  This is fine.  In that case, and *if* a residual
        # CALL_MAY_FORCE suddenly turns out to access it, then it will
        # trigger a ResumeGuardForcedDescr.handle_async_forcing() which
        # will work too (but just be a little pointless, as the structure
        # was already forced).

    def _optimize_JIT_FORCE_VIRTUAL(self, op):
        raise Exception("implement me")
        vref = self.getvalue(op.getarg(1))
        vrefinfo = self.optimizer.metainterp_sd.virtualref_info
        if vref.is_virtual():
            tokenvalue = vref.getfield(vrefinfo.descr_virtual_token, None)
            if (tokenvalue is not None and tokenvalue.is_constant() and
                    not tokenvalue.box.nonnull()):
                forcedvalue = vref.getfield(vrefinfo.descr_forced, None)
                if forcedvalue is not None and not forcedvalue.is_null():
                    self.make_equal_to(op, forcedvalue)
                    self.last_emitted_operation = REMOVED
                    return True
        return False

    def optimize_GETFIELD_GC_I(self, op):
        opinfo = self.getptrinfo(op.getarg(0))
        # XXX dealt with by heapcache
        # If this is an immutable field (as indicated by op.is_always_pure())
        # then it's safe to reuse the virtual's field, even if it has been
        # forced, because it should never be written to again.
        #if op.is_always_pure():
        #    
        #    if value.is_forced_virtual() and op.is_always_pure():
        #        fieldvalue = value.getfield(op.getdescr(), None)
        #        if fieldvalue is not None:
        #            self.make_equal_to(op, fieldvalue)
        #            return
        if opinfo and opinfo.is_virtual():
            fieldop = opinfo.getfield(op.getdescr())
            if fieldop is None:
                raise Exception("I think this is plain illegal")
                xxx
                fieldvalue = self.optimizer.new_const(op.getdescr())
            self.make_equal_to(op, fieldop)
        else:
            self.make_nonnull(op.getarg(0))
            self.emit_operation(op)
    optimize_GETFIELD_GC_R = optimize_GETFIELD_GC_I
    optimize_GETFIELD_GC_F = optimize_GETFIELD_GC_I

    # note: the following line does not mean that the two operations are
    # completely equivalent, because GETFIELD_GC_PURE is_always_pure().
    optimize_GETFIELD_GC_PURE_I = optimize_GETFIELD_GC_I
    optimize_GETFIELD_GC_PURE_R = optimize_GETFIELD_GC_I
    optimize_GETFIELD_GC_PURE_F = optimize_GETFIELD_GC_I

    def optimize_SETFIELD_GC(self, op):
        opinfo = self.getptrinfo(op.getarg(0))
        if opinfo is not None and opinfo.is_virtual():
            opinfo.setfield(op.getdescr(),
                            self.get_box_replacement(op.getarg(1)))
        else:
            self.make_nonnull(op.getarg(0))
            self.emit_operation(op)

    def optimize_NEW_WITH_VTABLE(self, op):
        known_class = ConstInt(descr2vtable(self.optimizer.cpu, op.getdescr()))
        self.make_virtual(known_class, op, op.getdescr())

    def optimize_NEW(self, op):
        self.make_vstruct(op.getdescr(), op)

    def optimize_NEW_ARRAY(self, op):
        sizebox = self.get_constant_box(op.getarg(0))
        if sizebox is not None:
            self.make_varray(op.getdescr(), sizebox.getint(), op)
        else:
            self.emit_operation(op)

    def optimize_NEW_ARRAY_CLEAR(self, op):
        sizebox = self.get_constant_box(op.getarg(0))
        if sizebox is not None:
            self.make_varray(op.getdescr(), sizebox.getint(), op, clear=True)
        else:
            self.emit_operation(op)        

    def optimize_CALL_N(self, op):
        effectinfo = op.getdescr().get_extra_info()
        if effectinfo.oopspecindex == EffectInfo.OS_RAW_MALLOC_VARSIZE_CHAR:
            self.do_RAW_MALLOC_VARSIZE_CHAR(op)
        elif effectinfo.oopspecindex == EffectInfo.OS_RAW_FREE:
            self.do_RAW_FREE(op)
        elif effectinfo.oopspecindex == EffectInfo.OS_JIT_FORCE_VIRTUALIZABLE:
            # we might end up having CALL here instead of COND_CALL
            info = self.getptrinfo(op.getarg(1))
            if info and info.is_virtual():
                return
        else:
            self.emit_operation(op)
    optimize_CALL_R = optimize_CALL_N
    optimize_CALL_I = optimize_CALL_N

    def do_RAW_MALLOC_VARSIZE_CHAR(self, op):
        sizebox = self.get_constant_box(op.getarg(1))
        if sizebox is None:
            self.emit_operation(op)
            return
        self.make_virtual_raw_memory(sizebox.getint(), op)
        self.last_emitted_operation = REMOVED

    def do_RAW_FREE(self, op):
        opinfo = self.getrawptrinfo(op.getarg(1))
        if opinfo and opinfo.is_virtual():
            return
        self.emit_operation(op)

    def optimize_INT_ADD(self, op):
        if 0:
            XXX
            value = self.getvalue(op.getarg(0))
            offsetbox = self.get_constant_box(op.getarg(1))
            if value.is_virtual() and offsetbox is not None:
                offset = offsetbox.getint()
                # the following check is constant-folded to False if the
                # translation occurs without any VRawXxxValue instance around
                if value.is_about_raw:
                    if isinstance(value, VRawBufferValue):
                        self.make_virtual_raw_slice(value, offset, op)
                        return
                    elif isinstance(value, VRawSliceValue):
                        offset = offset + value.offset
                        self.make_virtual_raw_slice(value.rawbuffer_value, offset,
                                                    op)
                        return
        self.emit_operation(op)

    def optimize_ARRAYLEN_GC(self, op):
        opinfo = self.getptrinfo(op.getarg(0))
        if opinfo and opinfo.is_virtual():
            self.make_constant_int(op, opinfo.getlength())
        else:
            self.make_nonnull(op.getarg(0))
            self.emit_operation(op)

    def optimize_GETARRAYITEM_GC_I(self, op):
        opinfo = self.getptrinfo(op.getarg(0))
        if opinfo and opinfo.is_virtual():
            indexbox = self.get_constant_box(op.getarg(1))
            if indexbox is not None:
                item = opinfo.getitem(indexbox.getint())
                if item is None:   # reading uninitialized array items?
                    assert False, "can't read uninitialized items"
                    itemvalue = value.constvalue     # bah, just return 0
                self.make_equal_to(op, item)
                return
        self.make_nonnull(op.getarg(0))
        self.emit_operation(op)
    optimize_GETARRAYITEM_GC_R = optimize_GETARRAYITEM_GC_I
    optimize_GETARRAYITEM_GC_F = optimize_GETARRAYITEM_GC_I

    # note: the following line does not mean that the two operations are
    # completely equivalent, because GETARRAYITEM_GC_PURE is_always_pure().
    optimize_GETARRAYITEM_GC_PURE_I = optimize_GETARRAYITEM_GC_I
    optimize_GETARRAYITEM_GC_PURE_R = optimize_GETARRAYITEM_GC_I
    optimize_GETARRAYITEM_GC_PURE_F = optimize_GETARRAYITEM_GC_I

    def optimize_SETARRAYITEM_GC(self, op):
        opinfo = self.getptrinfo(op.getarg(0))
        if opinfo and opinfo.is_virtual():
            indexbox = self.get_constant_box(op.getarg(1))
            if indexbox is not None:
                opinfo.setitem(indexbox.getint(),
                               self.get_box_replacement(op.getarg(2)))
                return
        self.make_nonnull(op.getarg(0))
        self.emit_operation(op)

    def _unpack_arrayitem_raw_op(self, op, indexbox):
        index = indexbox.getint()
        cpu = self.optimizer.cpu
        descr = op.getdescr()
        basesize, itemsize, _ = cpu.unpack_arraydescr_size(descr)
        offset = basesize + (itemsize*index)
        return offset, itemsize, descr

    def optimize_GETARRAYITEM_RAW_I(self, op):
        opinfo = self.getrawptrinfo(op.getarg(0))
        if opinfo and opinfo.is_virtual():
            raise Exception("implement raw virtuals")
            xxx
            indexbox = self.get_constant_box(op.getarg(1))
            if indexbox is not None:
                offset, itemsize, descr = self._unpack_arrayitem_raw_op(op, indexbox)
                try:
                    itemvalue = value.getitem_raw(offset, itemsize, descr)
                except InvalidRawOperation:
                    pass
                else:
                    self.make_equal_to(op, itemvalue)
                    return
        self.make_nonnull(op.getarg(0))
        self.emit_operation(op)
    optimize_GETARRAYITEM_RAW_F = optimize_GETARRAYITEM_RAW_I

    def optimize_SETARRAYITEM_RAW(self, op):
        opinfo = self.getrawptrinfo(op.getarg(0))
        if opinfo and opinfo.is_virtual():
            indexbox = self.get_constant_box(op.getarg(1))
            if indexbox is not None:
                raise Exception("implement raw virtuals")
                offset, itemsize, descr = self._unpack_arrayitem_raw_op(op, indexbox)
                itemvalue = self.getvalue(op.getarg(2))
                try:
                    value.setitem_raw(offset, itemsize, descr, itemvalue)
                    return
                except InvalidRawOperation:
                    pass
        self.make_nonnull(op.getarg(0))
        self.emit_operation(op)

    def _unpack_raw_load_store_op(self, op, offsetbox):
        offset = offsetbox.getint()
        cpu = self.optimizer.cpu
        descr = op.getdescr()
        itemsize = cpu.unpack_arraydescr_size(descr)[1]
        return offset, itemsize, descr

    def optimize_RAW_LOAD_I(self, op):
        raise Exception("implement me")
        value = self.getvalue(op.getarg(0))
        if value.is_virtual():
            offsetbox = self.get_constant_box(op.getarg(1))
            if offsetbox is not None:
                offset, itemsize, descr = self._unpack_raw_load_store_op(op, offsetbox)
                try:
                    itemvalue = value.getitem_raw(offset, itemsize, descr)
                except InvalidRawOperation:
                    pass
                else:
                    self.make_equal_to(op, itemvalue)
                    return
        value.ensure_nonnull()
        self.emit_operation(op)
    optimize_RAW_LOAD_F = optimize_RAW_LOAD_I

    def optimize_RAW_STORE(self, op):
        raise Exception("implement me")
        value = self.getvalue(op.getarg(0))
        if value.is_virtual():
            offsetbox = self.get_constant_box(op.getarg(1))
            if offsetbox is not None:
                offset, itemsize, descr = self._unpack_raw_load_store_op(op, offsetbox)
                itemvalue = self.getvalue(op.getarg(2))
                try:
                    value.setitem_raw(offset, itemsize, descr, itemvalue)
                    return
                except InvalidRawOperation:
                    pass
        value.ensure_nonnull()
        self.emit_operation(op)

    def optimize_GETINTERIORFIELD_GC_I(self, op):
        opinfo = self.getptrinfo(op.getarg(0))
        if opinfo and opinfo.is_virtual():
            indexbox = self.get_constant_box(op.getarg(1))
            if indexbox is not None:
                descr = op.getdescr()
                fld = opinfo.getinteriorfield_virtual(indexbox.getint(), descr)
                if fld is None:
                    raise Exception("I think this is illegal")
                    xxx
                    fieldvalue = self.new_const(descr)
                self.make_equal_to(op, fld)
                return
        self.make_nonnull(op.getarg(0))
        self.emit_operation(op)
    optimize_GETINTERIORFIELD_GC_R = optimize_GETINTERIORFIELD_GC_I
    optimize_GETINTERIORFIELD_GC_F = optimize_GETINTERIORFIELD_GC_I

    def optimize_SETINTERIORFIELD_GC(self, op):
        opinfo = self.getptrinfo(op.getarg(0))
        if opinfo and opinfo.is_virtual():
            indexbox = self.get_constant_box(op.getarg(1))
            if indexbox is not None:
                opinfo.setinteriorfield_virtual(indexbox.getint(),
                                                op.getdescr(),
                                       self.get_box_replacement(op.getarg(2)))
                return
        self.make_nonnull(op.getarg(0))
        self.emit_operation(op)


dispatch_opt = make_dispatcher_method(OptVirtualize, 'optimize_',
        default=OptVirtualize.emit_operation)

OptVirtualize.propagate_forward = dispatch_opt
