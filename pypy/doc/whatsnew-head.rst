==========================
What's new in PyPy2.7 5.8+
==========================

.. this is a revision shortly after release-pypy2.7-v5.7.0
.. startrev: 44f31f6dd39f

Add cpyext interfaces for ``PyModule_New``

Correctly handle `dict.pop`` where the ``pop``
key is not the same type as the ``dict``'s and ``pop``
is called with a default (will be part of release 5.7.1)

.. branch: issue2522

Fix missing tp_new on w_object called through multiple inheritance
(will be part of release 5.7.1)

.. branch: lstrip_to_empty_string

.. branch: vmprof-native

PyPy support to profile native frames in vmprof.

.. branch: reusing-r11
.. branch: branch-prediction

Performance tweaks in the x86 JIT-generated machine code: rarely taken
blocks are moved off-line.  Also, the temporary register used to contain
large constants is reused across instructions.
