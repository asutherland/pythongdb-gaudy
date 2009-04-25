# Use pygments to approximate the "list" command
# Andrew Sutherland <asutherland@asutherland.org>

import gdb, gdb.backtrace
import os.path

import pygflam as pygflam

class PygSourceList(gdb.Command):
    def __init__(self):
        gdb.Command.__init__(self, "sl", gdb.COMMAND_FILES)
    
    def invoke(self, argStr, from_tty):
        args = argStr.split()

        frame = gdb.selected_frame()
        sal = frame.find_sal()

        line_range = (sal.line - 15, sal.line + 10)

        if not os.path.isfile(sal.symtab.filename):
            print 'Unable to comply! No such file at: %s' % (
                sal.symtab.filename,)

        pygflam.flamhighlight(
            sal.symtab.filename,
            line_range=line_range,
            magic_lines={sal.line: 'curline'},
            bg_colors={'curline': 0x35}
            )


PygSourceList()
