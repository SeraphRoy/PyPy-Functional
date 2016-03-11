from _pytest.monkeypatch import monkeypatch
from rpython.tool import ansi_print, ansi_mandelbrot


class FakeOutput(object):
    def __init__(self, tty=True):
        self.monkey = monkeypatch()
        self.tty = tty
        self.output = []
    def __enter__(self, *args):
        self.monkey.setattr(ansi_print, 'ansi_print', self._print)
        self.monkey.setattr(ansi_print, 'isatty', self._isatty)
        self.monkey.setattr(ansi_mandelbrot, 'ansi_print', self._print)
        return self.output
    def __exit__(self, *args):
        self.monkey.undo()

    def _print(self, text, colors, newline=True, flush=True):
        if newline:
            text += '\n'
        self.output.append((text, colors))
    def _isatty(self):
        return self.tty


def test_simple():
    log = ansi_print.Logger('test')
    with FakeOutput() as output:
        log('Hello')
    assert output == [('[test] Hello\n', ())]

def test_bold():
    log = ansi_print.Logger('test')
    with FakeOutput() as output:
        log.bold('Hello')
    assert output == [('[test] Hello\n', (1,))]

def test_not_a_tty():
    log = ansi_print.Logger('test')
    with FakeOutput(tty=False) as output:
        log.bold('Hello')
    assert output == [('[test] Hello\n', ())]

def test_dot_1():
    log = ansi_print.Logger('test')
    with FakeOutput() as output:
        log.dot()
    assert len(output) == 1
    assert len(output[0][0]) == 1    # single character
    # output[0][1] is some ansi color code from mandelbort_driver

def test_dot_mixing_with_regular_lines():
    log = ansi_print.Logger('test')
    with FakeOutput() as output:
        log.dot()
        log.dot()
        log.WARNING('oops')
        log.WARNING('maybe?')
        log.dot()
    assert len(output) == 5
    assert len(output[0][0]) == 1    # single character
    assert len(output[1][0]) == 1    # single character
    assert output[2] == ('\n[test:WARNING] oops\n', (31,))
    assert output[3] == ('[test:WARNING] maybe?\n', (31,))
    assert len(output[4][0]) == 1    # single character
