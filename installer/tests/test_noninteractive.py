#!/usr/bin/env python3
"""Nothing the installer starts may stop to ask a question nobody can answer:

    python3 installer/tests/test_noninteractive.py

The failure this guards against (seen on a real Windows PC): the apps build asks "Continue anyway? [y/N]"
whenever its stdin is a terminal. The installer gave it the NUL device as stdin, and on Windows Python
reports NUL as a terminal - so it asked, got end-of-input and died with an EOFError traceback ("Building the
apps failed"). On Linux/macOS /dev/null is not a terminal, so the same code took the quiet path and the
suite never saw it. This test makes a terminal that is already at end-of-input (a pty with Ctrl-D waiting),
which is what Windows hands the tool, and checks:
  - the build tool, run the way the installer runs it (MGU_NO_DOCKER=1), carries on instead of asking
  - the same tool on a real terminal that nobody answers stops with a clear line, not a traceback
  - every Python / Node child the installer starts gets a closed pipe for stdin, never the NUL device
"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(REPO, 'installer'))
import bimmhacks_installer as bi          # noqa: E402

MAKE_KIT = os.path.join(REPO, 'idrive7-apps', 'kit', 'tools', 'make_kit.py')
try:
    import pty
except ImportError:                          # Windows
    pty = None


def run_on_a_terminal_at_eof(argv, env_extra):
    """run argv with a stdin that IS a terminal and is at end-of-input -> (exit status, output)"""
    master, slave = pty.openpty()
    os.write(master, b'\x04')                # Ctrl-D at the start of a line
    empty_path = tempfile.mkdtemp(prefix='nopath-')        # no docker on PATH, whatever this machine has
    env = dict(os.environ, PATH=empty_path, PYTHONIOENCODING='utf-8')
    env.pop('MGU_NO_DOCKER', None)
    env.update(env_extra)
    try:
        p = subprocess.Popen(argv, stdin=slave, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env,
                             cwd=os.path.join(REPO, 'idrive7-apps'))
        os.close(slave)
        out = p.communicate(timeout=120)[0].decode('utf-8', 'replace')
        return p.returncode, out
    finally:
        os.close(master)
        shutil.rmtree(empty_path, ignore_errors=True)


@unittest.skipIf(pty is None, 'needs a pty')
class BuildToolNeverAsks(unittest.TestCase):
    def test_under_the_installer_it_continues(self):
        rc, out = run_on_a_terminal_at_eof([sys.executable, MAKE_KIT], {'MGU_NO_DOCKER': '1'})
        self.assertNotIn('EOFError', out)
        self.assertNotIn('Traceback', out)
        self.assertNotIn('Continue anyway?', out)
        self.assertIn('not asking: continuing', out)
        # it went on to the next step (no BMW SDK in a test run, so that is where it stops)
        self.assertIn('oap-sdk is missing', out)

    def test_a_terminal_nobody_answers_stops_with_a_line_not_a_traceback(self):
        rc, out = run_on_a_terminal_at_eof([sys.executable, MAKE_KIT], {})
        self.assertEqual(rc, 1)
        self.assertNotIn('Traceback', out)
        self.assertIn('stopped - nothing was built or packed', out)


class InstallerChildren(unittest.TestCase):
    def test_a_child_gets_a_closed_pipe_that_is_not_a_terminal(self):
        p = bi.popen_noinput([sys.executable, '-c', 'import sys; print(sys.stdin.isatty()); print(repr(sys.stdin.read()))'],
                             stdout=subprocess.PIPE)
        out = p.communicate(timeout=30)[0].decode().split()
        self.assertEqual(out, ['False', "''"])

    def test_every_tool_the_installer_starts_is_given_one(self):
        """build, root tool, coding snapshot, Store installer, USB staging: stdin is a pipe (closed), never DEVNULL"""
        seen = []

        class Proc(object):
            returncode = 0

            def __init__(self, argv, **kw):
                seen.append((os.path.basename(str(argv[1] if len(argv) > 1 else argv[0])), kw.get('stdin')))
                self.stdin = open(os.devnull, 'wb')
                self.stdout = open(os.devnull, 'rb')

            def wait(self):
                return 0

            def communicate(self, *a, **k):
                return b'', b''

        key = tempfile.NamedTemporaryFile(delete=False)
        key.close()
        real_popen, real_ensure = subprocess.Popen, bi.ensure_cryptography
        subprocess.Popen = Proc
        bi.ensure_cryptography = lambda log, install=True: None
        calls = [
            lambda: bi.build_kit('node', ['bimmhacks_hello'], 'en', lambda m: None),
            lambda: bi.run_ssh_root('status', lambda m: None),
            lambda: bi.do_backup_coding(lambda m: None, out_dir=tempfile.gettempdir()),
            lambda: bi.do_store_install('node', '127.0.0.1', key.name, log=lambda m: None),
            lambda: bi.do_usb_repo('node', tempfile.mkdtemp(), [], log=lambda m: None),
        ]
        try:
            for call in calls:
                try:
                    call()
                except bi.InstallerError:
                    pass                      # the stand-in produces no output files: only the launch matters
        finally:
            subprocess.Popen, bi.ensure_cryptography = real_popen, real_ensure
            os.unlink(key.name)
        self.assertEqual(len(seen), len(calls), seen)
        for name, stdin in seen:
            self.assertEqual(stdin, subprocess.PIPE, '%s was started with stdin=%r' % (name, stdin))


if __name__ == '__main__':
    unittest.main(verbosity=2)
