=====================================
PyPy: Python in Python Implementation
=====================================

Welcome to PyPy!

PyPy is both an implementation of the Python programming language, and
an extensive compiler framework for dynamic language implementations.
You can build self-contained Python implementations which execute
independently from CPython.

The home page is:

    http://pypy.org/

If you want to help developing PyPy, this document might help you:

    http://doc.pypy.org/

It will also point you to the rest of the documentation which is generated
from files in the pypy/doc directory within the source repositories. Enjoy
and send us feedback!

    the pypy-dev team <pypy-dev@python.org>


Building
========

First switch to or download the correct branch.  The basic choices are
``default`` for Python 2.7 and, for Python 3.X, the corresponding py3.X
branch (e.g. ``py3.5``).

Build with:

.. code-block:: console

    $ rpython/bin/rpython -Ojit pypy/goal/targetpypystandalone.py

This ends up with a ``pypy-c`` or ``pypy3-c`` binary in the main pypy
directory.  We suggest to use virtualenv with the resulting
pypy-c/pypy3-c as the interpreter; you can find more details about
various installation schemes here:

    http://doc.pypy.org/en/latest/install.html
    
Links that Might be Useful
===========================
    http://eli.thegreenplace.net/2010/06/30/python-internals-adding-a-new-statement-to-python/
    
    http://hirzels.com/martin/papers/dls12-thorn-patterns.pdf
    
    http://doc.pypy.org/en/latest/getting-started-dev.html?highlight=grammar

Pattern vs. Types
==================
Patterns:
1. check whether a given input has certain "structure"
2. extract the pieces or no piece
3. can optionally bind pieces to variables that can be used later

 
Some Use Cases (may not be the same syntax)
===============
If none of the cases matches, it will throw an exception.
We may want match to be an expression but not a statement.


def haha(arg):
    x = match(arg):
        with 1 or 2:
            3
        with 3:
            7
        with y if type(y) is Int:
            2
        with _:
            throws Exception("Not an Int")
    return x

def yosh(arg):
    x = match(arg):
        with []:
            [3]
        with {}:
            {3}
        with _:
            print "nima"
            None
    return x
    
More use cases of match:

      x = match(L):
         [x : str , y : str, x[::-1]]:
            x + y
         _:
            "Wrong"
            
      y = match(L):
         [_ : int, _ : int, _ : int]:
            ...
         _:
            ...
      
     
