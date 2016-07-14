==========================
What's new in PyPy2.7 5.3+
==========================

.. this is a revision shortly after release-pypy2.7-v5.3
.. startrev: 873218a739f1

.. 418b05f95db5
Improve CPython compatibility for ``is``. Now code like ``if x is ():``
works the same way as it does on CPython.  See http://pypy.readthedocs.io/en/latest/cpython_differences.html#object-identity-of-primitive-values-is-and-id .

.. pull request #455
Add sys.{get,set}dlopenflags, for cpyext extensions.

.. branch: fix-gen-dfa

Resolves an issue with the generator script to build the dfa for Python syntax.

.. branch: z196-support

Fixes a critical issue in the register allocator and extends support on s390x.
PyPy runs and translates on the s390x revisions z10 (released February 2008, experimental)
and z196 (released August 2010) in addition to zEC12 and z13.
To target e.g. z196 on a zEC12 machine supply CFLAGS="-march=z196" to your shell environment.

.. branch: s390x-5.3-catchup

Implement the backend related changes for s390x.

.. branch: incminimark-ll_assert
.. branch: vmprof-openbsd

.. branch: testing-cleanup

Simplify handling of interp-level tests and make it more forward-
compatible.

.. branch: pyfile-tell
Sync w_file with the c-level FILE* before returning FILE* in PyFile_AsFile

.. branch: rw-PyString_AS_STRING
Allow rw access to the char* returned from PyString_AS_STRING, also refactor
PyStringObject to look like cpython's and allow subclassing PyString_Type and
PyUnicode_Type

.. branch: save_socket_errno

Bug fix: if ``socket.socket()`` failed, the ``socket.error`` did not show
the errno of the failing system call, but instead some random previous
errno.

.. branch: PyTuple_Type-subclass

Refactor PyTupleObject to look like cpython's and allow subclassing 
PyTuple_Type

.. branch: call-via-pyobj

Use offsets from PyTypeObject to find actual c function to call rather than
fixed functions, allows function override after PyType_Ready is called

.. branch: issue2335

Avoid exhausting the stack in the JIT due to successive guard
failures in the same Python function ending up as successive levels of
RPython functions, while at app-level the traceback is very short

.. branch: use-madv-free

Try harder to memory to the OS.  See e.g. issue #2336.  Note that it does
not show up as a reduction of the VIRT column in ``top``, and the RES
column might also not show the reduction, particularly on Linux >= 4.5 or
on OS/X: it uses MADV_FREE, which only marks the pages as returnable to
the OS if the memory is low.
