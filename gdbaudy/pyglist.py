# Use pygments to approximate the "list" command
# Andrew Sutherland <asutherland@asutherland.org>

import gdb, gdb.backtrace
import os.path

import pygflam as pygflam

class GlobalContext(object):
    def __init__(self):
        self.last_frame = None
        self.last_filename = None
        self.last_line = 0
        self.last_range = (0, 0)

CONTEXT = GlobalContext()


class PygSourceList(gdb.Command):
    '''Prints a syntax-highlighted source listing.  Currently limited to only
work based on the current debug frame and position.

By default, the 11 lines before the current position and 8 lines after are
displayed.  A line context is saved between command invocations if the current
source line does not change.

Arguments when used in a new context:
  (none)  Shows the 11 lines before the current position and 8 lines after.
  N       Shows the N/2 lines befores the current position and N/2 lines after.
  -N      Shows the N lines before the current position and 8 lines after.
  +N      Shows the 11 lines before the current position and N lines after.
  M N     Shows the M lines before the current position and N lines after.
  
Arguments in an existing context:
  (none)  Shows the 20 lines after the last-shown lines.
  -       Shows the 20 lines preceding the last-shown lines.
  N       Shows the N lines after the last-shown lines.
  -N      Shows the N lines preceding the last-shown lines.
  M N     Shows the M last lines of the last-shown lines and N lines after the
            last-shown lines.

Arguments regardless of context
  @A,B    Shows lines A through B.
'''
    def __init__(self):
        gdb.Command.__init__(self, "sl", gdb.COMMAND_FILES)
    
    def invoke(self, argStr, from_tty):
        frame = gdb.selected_frame()
        sal = frame.find_sal()

        # figure out if the context has changed or not
        same_context = (CONTEXT.last_frame == frame and
                        CONTEXT.last_filename == sal.symtab.filename and
                        CONTEXT.last_line == sal.line)
        # save the current position for the next call
        CONTEXT.last_frame = frame
        CONTEXT.last_filename = sal.symtab.filename
        CONTEXT.last_line = sal.line

        # bail if the file does not exist. (we do this after the context saving
        #  in case the users moves into a frame where we can't help and then
        #  moves back.)
        if not os.path.isfile(sal.symtab.filename):
            print 'Unable to comply! No such file at: %s' % (
                sal.symtab.filename,)

        # paranoia, should figure out if this is needed...
        argStr = argStr.strip()

        # "global" argument syntax
        if argStr and argStr[0] == '@':
            argStr = argStr[1:]
            args = map(int, argStr.split(','))
            line_range = args
        # first time in this context?
        elif not same_context:
            if argStr == '':
                line_range = (sal.line - 11, sal.line + 8)
            elif ' ' in argStr:
                args = map(int, argStr.split(' '))
                line_range = (sal.line - args[0], sal.line + args[1])
            elif argStr[0] == '-':
                if len(argStr) == 1:
                    # XXX uh, this would seem to happen when our context info
                    #  tricks us into thinking it's a new context, but it's not
                    arg = 11
                else:
                    arg = int(argStr[1:])
                line_range = (sal.line - arg, sal.line + 8)
            elif argStr[0] == '+':
                arg = int(argStr[1:])
                line_range = (sal.line - 11, sal.line + arg)
            elif argStr.isdigit():
                arg = int(argStr)
                halfarg = arg // 2
                line_range = (sal.line - halfarg, sal.line + halfarg)
            else:
                print 'That is not a thing.'
                return
        else:
            line_range = CONTEXT.last_range
            if argStr == '':
                line_range = (line_range[1]+1, line_range[1]+20)
            elif ' ' in argStr:
                args = map(int, argStr.split(' '))
                line_range = (line_range[1]-args[0]+1, line_range[1]+args[1])
            elif argStr[0] == '-':
                if len(argStr) == 1:
                    line_range = (line_range[0]-20, line_range[0]-1)
                else:
                    arg = int(argStr[1:])
                    line_range = (line_range[0]-arg, line_range[0]-1)
            elif argStr.isdigit():
                arg = int(argStr)
                line_range = (line_range[1]+1, line_range[1]+arg)
            else:
                print 'That is not a thing.'
                return

        CONTEXT.last_range = line_range

        pygflam.flamhighlight(
            sal.symtab.filename,
            line_range=line_range,
            magic_lines={sal.line: 'curline'},
            bg_colors={'curline': 0x35}
            )


PygSourceList()
