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

import gdb
import gdb.frames
from gdb.FrameIterator import FrameIterator

from pyflam import *
import sys
import os.path
import itertools

class ContextHelper(object):
    def __init__(self, frameHelpers=[]):
        self.pathCounts = {}
        self.seenValues = {}
        self.interestingValues = {}

        self.frameHelpers = frameHelpers
        for frameHelper in self.frameHelpers:
            frameHelper.setup()

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
        if isinstance(value, str) and value.startswith('0x'):
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

    def runHelpers(self, frame):
        syn_frames = None
        show = True
        for frameHelper in self.frameHelpers:
            helper_frames, helper_show = frameHelper.process_frame(frame)
            if not helper_show:
                show = False
            if helper_frames:
                syn_frames = helper_frames
        return syn_frames, show


# This comes from gdb.command.backtrace, hence the copyright up top
class ColorFrameWrapper(object):
    '''
    Wraps a frame.
    '''
    def __init__ (self, frame, context, frame_num):
        self.frame = frame;
        self.context = context
        self.frame_num = frame_num

        # -- Tell the context about the file path
        # symtab and line
        sal = self.frame.find_sal()
        if sal.symtab and sal.symtab.filename:
            self.context.considerPath(sal.symtab.filename)
        
        # -- Tell the function about all the values it sees (args and locals)
        block = self.block = None
        try:
            block = self.block = self.frame.block()
        except:
            pass
        if block:
            # if this is a locals frame, jump up to the args frame...
            if block.function is None:
                block = block.superblock
            for sym in block:
                self.context.considerValue(
                    self.frame_num,
                    *self.munge_symbol(sym, block))

        self.syn_frames, self.show_me = self.context.runHelpers(self.frame)

    def munge_symbol (self, sym, block):
        '''
        Given a symbol in the context of a block, return a tuple of the printable
        name for the symbol and its value in this frame.

        @param sym A symbol, probably either an argument or a local.
        @param block The frame block in which we are operating.
        '''
        # uh, pierce linkage names unless they are register values?
        #  maybe this is a trick to get the fully qualified type?
        if len (sym.linkage_name):
            nsym, is_field_of_this = gdb.lookup_symbol (sym.linkage_name, block)
            if not nsym:
                return sym.linkage_name, '<danger=ignored>'
            if nsym and nsym.addr_class != gdb.SYMBOL_LOC_REGISTER:
                sym = nsym

        # load the value!
        try:
            val = self.frame.read_var (sym)
            if val != None:
                val = str (val)
        # FIXME: would be nice to have a more precise exception here.
        except RuntimeError as text:
            val = text
        except Exception as e:
            val = "problemo"
        if val == None:
            val = "???"
        return sym.print_name, val

    def print_frame_locals (self, block):
        if not block:
            return

        first = True

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

    def print_frame_args (self, block):
        if not block:
            return
        # if there are locals, we will have a block with no associated
        #  function, but its superblock should be the args!
        if block.function is None:
            block = block.superblock

        first = True

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
                fmtbits.append('{sk}%s{s}={sv}%~2s')
                fmtvals.append(key)
                fmtvals.append(val)

        pout('\n'.join(fmtbits) + '{-fg}', *fmtvals)
        #pout('{n}(' + '{n}, '.join(fmtbits) + '{n})', *fmtvals)

    # FIXME: this should probably just be a method on gdb.Frame.
    # But then we need stream wrappers.
    def describe (self, frame_num, mode, args=True):
        if self.syn_frames:
            for syn_frame in self.syn_frames:
                pout('{s} JS {jfn}%s {.48}{s}at {cn}%s{s}:{ln}%d {s}%010x{-fg}',
                     syn_frame.func_name,
                     syn_frame.filename,
                     #self.context.chewPath(syn_frame.filename) or '???',
                     syn_frame.line,
                     syn_frame.pc)

        if not self.show_me:
            return

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
            if name.startswith('mozilla::'):
                name = name[9:]

            if not name or (not sal.symtab or not sal.symtab.filename):
                lib = gdb.solib_name (pc)
                if lib:
                    # stream.write (" from " + lib)
                    pass

            if mode == MODE_TERSE:
                pout('{s}%3.3d {fn}%s{s}:{ln}%d{-fg}',
                     frame_num, name, sal.line)
            elif mode == MODE_PASTE:
                pout('{s}%3.3d {fn}%s{-fg}\n    {cn}%s{s}:{ln}%d{-fg}',
                     frame_num, name,
                     sal.symtab and sal.symtab.filename and self.context.chewPath(sal.symtab.filename) or '???',
                     sal.line)
            else:
                pout('{s}%3.3d {fn}%s {.48}{s}at {cn}%s{s}:{ln}%d {s}%010x{-fg}',
                     frame_num, name,
                     sal.symtab and sal.symtab.filename and self.context.chewPath(sal.symtab.filename) or '???',
                     sal.line, pc)
                pout.i(6)
                if args:
                    self.print_frame_args(self.block)

                if mode == MODE_FULL:
                    if not args:
                        block = self.frame.block()
                    self.print_frame_locals(self.block)
                pout.i(-6)

    #def __getattr__ (self, name):
    #    return getattr (self.frame, name)

MODE_NORMAL = 0
MODE_TERSE = 1
MODE_PASTE = 2
MODE_FULL = 3

class ColorFilteringBacktrace (gdb.Command):
    """Print backtrace of all stack frames, or innermost COUNT frames.
With a negative argument, print outermost -COUNT frames.
Use of the 'full' qualifier also prints the values of the local variables.
Use of the 'raw' qualifier avoids any filtering by loadable modules.
Use of the 'terse' qualifier tells us to only show class name.
"""

    def __init__ (self):
        gdb.Command.__init__ (self, "cbt", gdb.COMMAND_STACK)

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
        mode = MODE_NORMAL

        for word in arg.split (" "):
            if word == '':
                continue
            elif word == 'raw':
                filter = False
            elif word == 'full':
                mode = MODE_FULL
            elif word == 'terse':
                mode = MODE_TERSE
            elif word == 'paste':
                mode = MODE_PASTE
            else:
                count = int (word)

        # FIXME: provide option to start at selected frame
        # However, should still number as if starting from newest
        context = ContextHelper()

        frames = []
        iterFrames = None
        if filter:
            iterFrames = gdb.frames.execute_frame_filters(gdb.newest_frame(), 0, -1)
        if not iterFrames:
            iterFrames = FrameIterator(gdb.newest_frame())

        # Now wrap in an iterator that numbers the frames.
        iterFrames = zip(itertools.count (0), iterFrames)

        for iFrame, gdbFrame in iterFrames:
            frames.append(ColorFrameWrapper(gdbFrame, context, iFrame))
        context.process()

        # Extract sub-range user wants.
        iterFrames = zip(itertools.count (0), iter(frames))
        if count < 0:
            iterFrames = self.final_n (iterFrames, count)
        elif count > 0:
            iterFrames = itertools.islice (iterFrames, 0, count)

        # zero it...
        pout.i(-100)
        for pair in iterFrames:
            pair[1].describe (pair[0], mode)

ColorFilteringBacktrace()
