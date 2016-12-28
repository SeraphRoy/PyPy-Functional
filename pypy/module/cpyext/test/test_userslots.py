from rpython.rtyper.lltypesystem import rffi
from pypy.module.cpyext.pyobject import make_ref, from_ref
from pypy.module.cpyext.api import generic_cpy_call
from pypy.module.cpyext.typeobject import PyTypeObjectPtr
from pypy.module.cpyext.test.test_api import BaseApiTest
from pypy.module.cpyext.test.test_cpyext import AppTestCpythonExtensionBase


class TestAppLevelObject(BaseApiTest):
    def test_nb_add_from_python(self, space, api):
        w_date = space.appexec([], """():
            class DateType(object):
                def __add__(self, other):
                    return 'sum!'
            return DateType()
            """)
        w_datetype = space.type(w_date)
        py_date = make_ref(space, w_date)
        py_datetype = rffi.cast(PyTypeObjectPtr, make_ref(space, w_datetype))
        assert py_datetype.c_tp_as_number
        assert py_datetype.c_tp_as_number.c_nb_add
        w_obj = generic_cpy_call(space, py_datetype.c_tp_as_number.c_nb_add,
                                 py_date, py_date)
        assert space.str_w(w_obj) == 'sum!'

    def test_tp_new_from_python(self, space, api):
        w_date = space.appexec([], """():
            class Date(object):
                def __new__(cls, year, month, day):
                    self = object.__new__(cls)
                    self.year = year
                    self.month = month
                    self.day = day
                    return self
            return Date
            """)
        py_datetype = rffi.cast(PyTypeObjectPtr, make_ref(space, w_date))
        one = space.newint(1)
        arg = space.newtuple([one, one, one])
        # call w_date.__new__
        w_obj = space.call_function(w_date, one, one, one)
        w_year = space.getattr(w_obj, space.newbytes('year'))
        assert space.int_w(w_year) == 1

        w_obj = generic_cpy_call(space, py_datetype.c_tp_new, py_datetype, 
                                 arg, space.newdict({}))
        w_year = space.getattr(w_obj, space.newbytes('year'))
        assert space.int_w(w_year) == 1

class AppTestUserSlots(AppTestCpythonExtensionBase):
    def test_tp_hash_from_python(self):
        # to see that the functions are being used,
        # run pytest with -s
        module = self.import_extension('foo', [
           ("use_hash", "METH_O",
            '''
                long hash = args->ob_type->tp_hash(args);
                return PyLong_FromLong(hash);
            ''')])
        class C(object):
            def __hash__(self):
                return -23
        c = C()
        # uses the userslot slot_tp_hash
        ret = module.use_hash(C())
        assert hash(c) == ret
        # uses the slotdef renamed cpyext_tp_hash_int
        ret = module.use_hash(3)
        assert hash(3) == ret

    def test_tp_str(self):
        module = self.import_extension('foo', [
           ("tp_str", "METH_VARARGS",
            '''
                 PyTypeObject *type = (PyTypeObject *)PyTuple_GET_ITEM(args, 0);
                 PyObject *obj = PyTuple_GET_ITEM(args, 1);
                 if (!type->tp_str)
                 {
                     PyErr_SetString(PyExc_ValueError, "no tp_str");
                     return NULL;
                 }
                 return type->tp_str(obj);
             '''
             )
            ])
        class C:
            def __str__(self):
                return "text"
        assert module.tp_str(type(C()), C()) == "text"
        class D(int):
            def __str__(self):
                return "more text"
        assert module.tp_str(int, D(42)) == "42"
        class A(object):
            pass
        s = module.tp_str(type(A()), A())
        assert 'A object' in s

