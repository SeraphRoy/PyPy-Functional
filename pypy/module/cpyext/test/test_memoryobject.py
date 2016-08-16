import pytest
from pypy.module.cpyext.test.test_api import BaseApiTest
from pypy.module.cpyext.test.test_cpyext import AppTestCpythonExtensionBase



class TestMemoryViewObject(BaseApiTest):
    def test_fromobject(self, space, api):
        if space.is_true(space.lt(space.sys.get('version_info'),
                                  space.wrap((2, 7)))):
            py.test.skip("unsupported before Python 2.7")

        w_hello = space.newbytes("hello")
        assert api.PyObject_CheckBuffer(w_hello)
        w_view = api.PyMemoryView_FromObject(w_hello)
        w_bytes = space.call_method(w_view, "tobytes")
        assert space.unwrap(w_bytes) == "hello"


class AppTestBufferProtocol(AppTestCpythonExtensionBase):
    def test_buffer_protocol(self):
        module = self.import_module(name='buffer_test')
        arr = module.PyMyArray(10)
        y = memoryview(arr)
        assert y.format == 'i'

