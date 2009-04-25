# Copyright (C) 2008 Free Software Foundation, Inc.

# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

# This file is basically gdb.command.backtrace with my coloring code kludged in
#  (from the Archer gdb project)
# Well, okay, now it has the ContextHelper and so does much more work and is
#  arguably less efficient, perhaps more ugly, etc. etc.

import gdb, gdb.backtrace
from gdb.FrameIterator import FrameIterator
import itertools

import gdb.command.backtrace
from pyflam import *
import sys
import os.path

class ContextHelper:
    def __init__(self):
        self.pathCounts = {}
        self.seenValues = {}
        self.interestingValues = {}

    def considerPath(self, path):
        # screw escaping
        parts = path.split(os.path.sep)
        if len(parts) == 1:
            return
        curNode = self.pathCounts
        for part in parts:
            if not part:
                continue
            if part in curNode:
                count, subNode = curNode[part]
                curNode[part] = (count + 1, subNode)
            else:
                subNode = {}
                curNode[part] = (1, subNode)
            curNode = subNode

    def chewPath(self, path):
        parts = path.split(os.path.sep)
        if len(parts) == 1:
            return path
        curNode = self.pathCounts
        lastCount = None
        for iPart, part in enumerate(parts):
            if not part:
                continue
            count, subNode = curNode[part]
            # once things diverge is the interesting part
            if lastCount is not None and lastCount != count:
                return os.path.sep.join(parts[iPart:])
            lastCount = count
            curNode = subNode

    def considerValue(self, frame_num, name, value):
        if value.startswith('0x'):
            if value in self.seenValues:
                info = self.seenValues[value]
                info['count'] += 1
            else:
                info = self.seenValues[value] = {
                    'count': 1,
                    'name': '%s%d' % (name, frame_num)}

    def process(self):
        eligible = []
        for value, info in self.seenValues.items():
            if info['count'] > 1:
                eligible.append((info['count'], value))
        eligible.sort(reverse=True)
        for iColor, cvtupe in enumerate(eligible[:pout.INTERESTING_COUNT]):
            count, value = cvtupe
            info = self.seenValues[value]
            info['colorName'] = 'i%d' % iColor
            self.interestingValues[value] = info

    def isInterestingValue(self, value):
        return value in self.interestingValues
    def getValueInfo(self, value):
        info = self.interestingValues[value]
        return info['name'], info['colorName']


# This comes from gdb.command.backtrace, hence the copyright up top
class ColorFrameWrapper:
    def __init__ (self, frame, context, frame_num):
        self.frame = frame;
        self.context = context
        self.frame_num = frame_num

        self.func = gdb.find_pc_function (self.frame.addr_in_block ())

        sal = self.frame.find_sal()
        if sal.symtab and sal.symtab.filename:
            self.context.considerPath(sal.symtab.filename)
        
        if self.func:
            for sym in self.func.value:
                self.context.considerValue(
                    self.frame_num,
                    *self.munge_symbol(sym, self.func.value))

    def munge_symbol (self, sym, block):
        if len (sym.linkage_name):
            nsym, is_field_of_this = gdb.lookup_symbol (sym.linkage_name, block)
            if nsym.addr_class != gdb.SYMBOL_LOC_REGISTER:
                sym = nsym

        try:
            val = self.frame.read_var (sym)
            if val != None:
                val = str (val)
        # FIXME: would be nice to have a more precise exception here.
        except RuntimeError, text:
            val = text
        except Exception, e:
            val = "problemo"
        if val == None:
            val = "???"
        return sym.print_name, val

    def print_frame_locals (self, func):
        if not func:
            return

        first = True
        block = func.value

        fmtbits = []
        fmtvals = []

        for sym in block:
            if sym.is_argument:
                continue;

            key, val = self.munge_symbol(sym, block)
            if self.context.isInterestingValue(val):
                valDesc, valColor = self.context.getValueInfo(val)
                fmtbits.append('{sk}%s{s}={sv}%s {' + valColor + '}%s')
                fmtvals.extend((key, val, valDesc))
            else:
                fmtbits.append('{sk}%s{s}={sv}%s')
                fmtvals.append(key)
                fmtvals.append(val)

        pout('\n'.join(fmtbits), *fmtvals)

    def print_frame_args (self, func):
        if not func:
            return

        first = True
        block = func.value

        fmtbits = []
        fmtvals = []

        for sym in block:
            if not sym.is_argument:
                continue;

            key, val = self.munge_symbol(sym, block)
            if self.context.isInterestingValue(val):
                valDesc, valColor = self.context.getValueInfo(val)
                fmtbits.append('{sk}%s{s}={sv}%s {' + valColor + '}%s')
                fmtvals.extend((key, val, valDesc))
            else:
                fmtbits.append('{sk}%s{s}={sv}%s')
                fmtvals.append(key)
                fmtvals.append(val)

        pout('\n'.join(fmtbits) + '{-fg}', *fmtvals)
        #pout('{n}(' + '{n}, '.join(fmtbits) + '{n})', *fmtvals)

    # FIXME: this should probably just be a method on gdb.Frame.
    # But then we need stream wrappers.
    def describe (self, frame_num, full):
        if self.frame.type () == gdb.DUMMY_FRAME:
            pout('{s}%2.2d <function called from gdb>{-fg}', frame_num)
        elif self.frame.type () == gdb.SIGTRAMP_FRAME:
            pout('{s}%2.2d <signal handler called>{-fg}', frame_num)
        else:
            sal = self.frame.find_sal ()
            pc = self.frame.pc ()
            name = self.frame.name ()
            if not name:
                name = "??"

            if not self.frame.name () or (not sal.symtab or not sal.symtab.filename):
                lib = gdb.solib_address (pc)
                if lib:
                    # stream.write (" from " + lib)
                    pass

            pout('{s}%2.2d {ln}%08x {s}in {fn}%s {s}at {cn}%s{s}:{ln}%d{-fg}',
                 frame_num, pc, name,
                 sal.symtab and sal.symtab.filename and self.context.chewPath(sal.symtab.filename) or '???',
                 sal.line)
            pout.i(2)
            self.print_frame_args(self.func)

            if full:
                self.print_frame_locals (self.func)
            pout.i(-2)

    def __getattr__ (self, name):
        return getattr (self.frame, name)

class ColorFilteringBacktrace (gdb.Command):
    """Print backtrace of all stack frames, or innermost COUNT frames.
With a negative argument, print outermost -COUNT frames.
Use of the 'full' qualifier also prints the values of the local variables.
Use of the 'raw' qualifier avoids any filtering by loadable modules.
"""

    def __init__ (self):
        # FIXME: this is not working quite well enough to replace
        # "backtrace" yet.
        gdb.Command.__init__ (self, "cbt", gdb.COMMAND_STACK)
        self.reverse = gdb.command.backtrace.ReverseBacktraceParameter()

    def reverse_iter (self, iter):
        result = []
        for item in iter:
            result.append (item)
        result.reverse()
        return result

    def final_n (self, iter, x):
        result = []
        for item in iter:
            result.append (item)
        return result[x:]

    def invoke (self, arg, from_tty):
        i = 0
        count = 0
        filter = True
        full = False

        for word in arg.split (" "):
            if word == '':
                continue
            elif word == 'raw':
                filter = False
            elif word == 'full':
                full = True
            else:
                count = int (word)

        # FIXME: provide option to start at selected frame
        # However, should still number as if starting from newest
        context = ContextHelper()

        frames = []
        iterFrames = FrameIterator (gdb.newest_frame ())
        if filter:
            iterFrames = gdb.backtrace.create_frame_filter (iterFrames)

        # Now wrap in an iterator that numbers the frames.
        iterFrames = itertools.izip (itertools.count (0), iterFrames)

        for iFrame, gdbFrame in iterFrames:
            frames.append(ColorFrameWrapper(gdbFrame, context, iFrame))
        context.process()

        # Reverse if the user wanted that.
        if self.reverse.value:
            frames.reverse()

        # Extract sub-range user wants.
        iterFrames = itertools.izip (itertools.count (0), iter(frames))
        if count < 0:
            iterFrames = self.final_n (iterFrames, count)
        elif count > 0:
            iterFrames = itertools.islice (iterFrames, 0, count)

        # zero it...
        pout.i(-100)
        for pair in iterFrames:
            pair[1].describe (pair[0], full)

ColorFilteringBacktrace()
