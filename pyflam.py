# pyflam, a gaudy colorizing library
#    Copyright (C) 2009 Andrew Sutherland (sombrero@alum.mit.edu)
#
#    This program is free software: you can redistribute it and/or modify
#    it under the terms of the GNU General Public License as published by
#    the Free Software Foundation, either version 3 of the License, or
#    (at your option) any later version.
#
#    This program is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU General Public License for more details.
#
#    You should have received a copy of the GNU General Public License
#    along with this program.  If not, see <http://www.gnu.org/licenses/>.

### This has been extracted out of my chroniquery library.  It was already
###  GPLv3 which works out well considering it is put here to be called by code
###  that is explicitly GPL v3 in the first place!  However, this will probably
###  still get licensed under something more permissive...

import re, sys, os.path, time, textwrap

class FlamOut(object):
    def __init__(self, fout=None):
        self.fout = fout or sys.stdout

        self._cmap = {}
        self._pat = re.compile(
            '(?:{([^}]+)})|(%([-#0 +~]*)(\d*)\.?(\d*)([sdx]))')

        self.init_map()

        self._indentLevel = 0
        self._verbose = False

        self._num_columns = None
        self._columns_last_updated = None

        self._wrapper = textwrap.TextWrapper()

    def _get_terminal_columns(self):
        # we just don't want to be doing this 100 times a second
        if self._columns_last_updated is not None:
            now = time.time()
            if now - self._columns_last_updated > 2:
                self._columns_last_updated = now;
                self._num_columns = None
        if self._num_columns is None:
            import termhelp
            self._num_columns = termhelp.getTerminalSize()[1]
        return self._num_columns

    def configure(self, **kwargs):
        self._verbose = kwargs.get('verbose', self._verbose)

    def init_map(self):
        self.map_control('-fg', '39')
        self.map_control('-bg', '49')

        self.map_fg('h', 127)
        # normal
        self.map_fg('n', 0xf8)
        self.map_fg('bn', 0xff)
        # error
        self.map_fg('e', 124)
        # warning
        self.map_fg('w', 220)
        # good
        self.map_fg('g',  46)
        
        # subtle
        self.map_fg('s', 0xee)

        # function-name (or filename, maybe)
        self.map_fg('fn', 0x4d)
        # container name/class name
        self.map_fg('cn', 0x41)
        
        # javascript function name
        self.map_fg('jfn', 0xc9)
        
        # interface name
        self.map_fg('in', 0x49)
        
        # script name
        self.map_fg('sn', 0x35)
        # line number
        self.map_fg('ln', 0x34)

        # example
        self.map_fg('ex', 81)
        
        self.map_fg('k', 129)
        self.map_fg('v', 38)
        self.map_fg('sk', 0x36)
        self.map_fg('sv', 0x18)

        interesting_colors = [0x3f, 0x63, 0x87, 0xab, 0xcf, 0xce, 0xcd, 0xcc,
                              0x45, 0x69, 0x8d, 0xb1, 0xd5, 0xd4, 0xd3, 0xd2,
                              0x4b, 0x6f, 0x93, 0xb7, 0xdb, 0xda, 0xd9, 0xd8,
                              0x51, 0x75, 0x99, 0xbd, 0xe1, 0xe0, 0xdf, 0xde]
        self.INTERESTING_COUNT = len(interesting_colors)
        for i, c in enumerate(interesting_colors):
            self.map_fg('i%d' % i, c)
    
    _ANSI_COLOR_LUT = ((0,0,0), (170,0,0), (0,170,0), (170,85,0),
                       (0,0,170), (170,0,170), (0,170,170), (170,170,170),
                       (85,85,85), (255,85,85), (85,255,85), (255,255,85),
                       (85,85,255), (255,85,255), (85,255,255), (255,255,255),
                       )
    def _crack_colorcode(self, color):
        '''
        Break a 256-color xterm-color into r,g,b
        '''
        # Base ANSI colors
        if color < 16:
            return self._ANSI_COLOR_LUT[color]
        # 6x6x6 Color Cube
        elif color < 232:
            color -= 16
            ired = (color // 36)
            igreen = (color // 6) % 6
            iblue = color % 6
            
            return (ired and (ired * 40 + 55),
                    igreen and (igreen * 40 + 55),
                    iblue and (iblue * 40 + 55))
        # gray-scale
        else:
            gray = color - 232
            level = gray * 10 + 8
            return (level, level, level)

    _COLOR_HEXMAP = None
    @property
    def _color_hexmap(self):
        if self._COLOR_HEXMAP is not None:
            return self._COLOR_HEXMAP
        chmap = self._COLOR_HEXMAP = {}
        for index in range(256):
            chmap[index] = self._crack_colorcode(index)
        return chmap

    def _parse_hexcolor(self, hexcolor):
        '''@return (r, g, b) triple given a hex-color string'''
        if hexcolor[0] == '#':
            hexcolor = hexcolor[1:]
        if len(hexcolor) == 6:
            return (int(hexcolor[0:2], 16),
                    int(hexcolor[2:4], 16),
                    int(hexcolor[4:6], 16))
        else:
            def halp(s):
                v = int(s, 16)
                return v * 16 + v
            return (halp(hexcolor[0]),
                    halp(hexcolor[1]),
                    halp(hexcolor[2]))

    def hexcolor_to_colorcode(self, hexcolor):
        bestcode = None
        bestdist = 256 * 256 * 4
        # desired red, green, blue
        dr, dg, db = self._parse_hexcolor(hexcolor)
        for code, crgb in self._color_hexmap.items():
            # candidate red, green, blue
            cr, cg, cb = crgb
            dist = ((dr - cr) * (dr - cr) +
                    (dg - cg) * (dg - cg) +
                    (db - cb) * (db - cb))
            if dist < bestdist:
                bestcode = code
                bestdist = dist
        return bestcode

    def map_fg(self, name, code):
        self._cmap[name] = '\x1b[38;5;%dm' % code

    def map_fg_hex(self, name, hexvalue):
        self.map_fg(name, self.hexcolor_to_colorcode(hexvalue))

    def map_bg(self, name, code):
        self._cmap[name] = '\x1b[48;5;%dm' % code

    def map_bg_hex(self, name, hexvalue):
        self.map_bg(name, self.hexcolor_to_colorcode(hexvalue))

    def map_control(self, name, bytestr):
        self._cmap[name] = '\x1b[%sm' % (bytestr,)

    def i(self, indentAdjust):
        self._indentLevel += indentAdjust
        if self._indentLevel < 0:
            self._indentLevel = 0

    def __call__(self, msg, *args, **kwargs):
        '''
        Formatting atoms, illustrated (colons are delimiters):
        - {color code}: Where color code is one of our character sequences like
                         "g", "b", etc.
        - {.24}: Inserts space characters so the cursor is positioned at column
                  24.  It does nothing if you are already at/past column 24.

        - %x: Print hex-formatted number with 0x prefix.
        - %d: Print as decimal number, no padding.
        - %s: Print as string, no padding.

        - %10s: Right-aligned string padded out to 10 characters with spaces.
        - %-10s: Left-aligned string padded out to 10 characters in length.
        - %.10s: String truncated to a maximum length of 10 characters.
        - %10.10s: Right-aligned string padded out to 10 characters with spaces;
                    truncated if its length exceeds 10 characters.
        - %10d: Right-aligned decimal number padded with spaces/truncated.
        - %-10d: Left-aligned decimal number padded with spaces/truncated.

        - %~2s: String wrapped at the screen's column width and indented 2
                 characters further in than the column the first character of
                 the string is going in.
        '''
        state = {'offset': self._indentLevel, 'iarg': 0}
        def map_helper(m):
            lstart = state.get('lstart', 0)
            mstart = m.start(0)
            idxPrevNewline = msg.rfind('\n', lstart, mstart)
            # no newline means increment
            if idxPrevNewline == -1:
                state['offset'] = state['offset'] + mstart - lstart
            # yes newline means offset is since that newline
            else:
                state['offset'] = mstart - idxPrevNewline - 1 # sub off the \n
            state['lstart'] = m.end(0)

            if m.group(2) is not None:
                iarg = state['iarg']
                if m.group(6) == 'x':
                    v = '0x%x' % args[iarg]
                else:
                    v = str(args[iarg])
                
                #%([#0- +]*)(\d*)(?:\.(\d*))?[sdx]
                alignLeft = False
                if m.group(4):
                    mini = int(m.group(4))
                else:
                    mini = 0
                if m.group(5):
                    limit = int(m.group(5))
                else:
                    limit = 64000
                if m.group(3):
                    conversionFlags = m.group(3)
                    if '-' in conversionFlags:
                        alignLeft = True
                    if '~' in conversionFlags:
                        cur_offset = state['offset']
                        next_offset = cur_offset + mini
                        mini = 0
                        wrapper = self._wrapper
                        # We need to tell the wrapper our current indent offset
                        #  even though we don't want to output it; we will slice
                        #  it back off before using the output.
                        wrapper.initial_indent = ' ' * cur_offset
                        wrapper.subsequent_indent = ' ' * next_offset
                        wrapper.width = self._get_terminal_columns()
                        wrapped = wrapper.wrap(v)
                        wrapped[0] = wrapped[0][cur_offset:]
                        if len(wrapped) > 1:
                            state['offset'] = len(wrapped[-1])
                        else:
                            state['offset'] = state['offset'] + len(wrapped[0])
                        v = '\n'.join(wrapped)
                        state['iarg'] = iarg + 1
                        return v
                    
                if len(v) > limit:
                    v = v[:limit]
                if len(v) < mini:
                    if alignLeft:
                        v += ' ' * (mini - len(v))
                    else:
                        v = ' ' * (mini - len(v)) + v
                idxNewline = v.rfind('\n')
                if idxNewline == -1:
                    state['offset'] = state['offset'] + len(v)
                else:
                    state['offset'] = len(v) - idxNewline - 1
                state['iarg'] = iarg + 1
                return v
            
            #print m.start(0), m.end(0), m.start(1), m.end(1)
            # TODO: make alignment logic work over multiple lines...
            if m.group(1)[0] == '.':
                desired_offset = int(m.group(1)[1:])
                space = ' ' * (desired_offset - state['offset'])
                state['offset'] = desired_offset
                return space
            #print 'delta:', m.start(0) - state.get('lstart',0)
            
            state['needrestore'] = True
            return self._cmap[m.group(1)]
        

        ostr = self._pat.sub(map_helper, msg)
        if 'needrestore' in state:
            ostr += self._cmap['n']

        if self._indentLevel:
            indent = ' ' * self._indentLevel
            # TODO: also handle the wrapping as required
            ostr = indent + ostr.replace('\n', '\n' + indent)
        
        self.fout.write(ostr + '\n')
    
    def pp(self, o, label=None, indent=0):
        '''
        Colorized pretty printer.
        '''
        if label:
            self('{n}%s', label)
        if type(o) in (tuple, list):
            last = len(o) - 1
            for i, v in enumerate(o):
                if i == 0:
                    pre = '['
                    post = (i != last) and ',' or ']'
                elif i == last:
                    pre = ' '
                    post = ']'
                else:
                    pre= ' '
                    post = ','
                if type(v) in (tuple, list, dict):
                    self.i(1)
                    self.pp(v)
                    self.i(-1)
                else:
                    self('{n}%s{v}%s{n}%s', pre, v, post)
        elif type(o) in (dict,):
            i = 0
            last = len(o) - 1
            for k, v in o.items():
                if i == 0:
                    pre = '{'
                    post = (i != last) and ',' or '}'
                elif i == last:
                    pre = ' '
                    post ='}'
                else:
                    pre= ' '
                    post = ','
                if type(v) in (tuple, list, dict):
                    self('{n}%s{k}%s{n}:', pre, k)
                    self.i(1)
                    self.pp(v)
                    self.i(-1)
                    if i == last:
                        self('{n}%s', post)
                else:
                    self('{n}%s{k}%s{n}: {v}%s{n}%s', pre, k, v, post)
                i += 1

        else:
            self('%s', str(o))

    def v(self, msg, *args, **kwargs):
        if self._verbose:
            self(msg, *args, **kwargs)
            
    def h(self):
        self('-' * 40)

class FlamHTML(FlamOut):
    def __init__(self, filename_or_fout, style=True, title=''):
        super(FlamHTML, self).__init__()
        
        self._style = style

        if isinstance(filename_or_fout, basestring):
            basename, extname = os.path.splitext(filename_or_fout)
            self._html_basename_with_path = basename
            self._html_basename_sans_path = os.path.basename(basename)
            self._html_extname = extname
            self.fout = open(filename_or_fout, 'wt')
            self.write_html_intro(title)
        else:
            self.fout = filename_or_fout

        self.title = title
        self.fstack = []
        self.indentStack = []

    def linkToPermutation(self, uniqueVal):
        relpath = '%s-%s%s' % (self._html_basename_sans_path,
                               uniqueVal,
                               self._html_extname)
        self.fout.write('<a href="%s">' % (relpath,))

    def closeLink(self):
        self.fout.write('</a>')

    def pushFilePermutation(self, uniqueVal):
        self.fstack.append(self.fout)
        self.indentStack.append(self._indentLevel)

        path = '%s-%s%s' % (self._html_basename_with_path,
                            uniqueVal,
                            self._html_extname)

        self.fout = open(path, 'wt')
        self._indentLevel = 0
        self.write_html_intro('%s: %s' % (self.title, uniqueVal))

    def popFilePermutation(self):
        self.close()
        self.fout = self.fstack.pop()
        self._indentLevel = self.indentStack.pop()

    _CTYPE_MAP= {'fg': 'color',
                 'bg': 'background-color'}
    
    def __call__(self, msg, *args, **kwargs):
        state = {'offset': 0, 'iarg': 0}
        def map_helper(m):
            if m.group(2) is not None:
                iarg = state['iarg']
                v = str(args[iarg])
                state['offset'] = state['offset'] + len(v)
                state['iarg'] = iarg + 1
                # HTML escaping here! gah!
                v = v.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
                return v
            
            #print m.start(0), m.end(0), m.start(1), m.end(1)
            # TODO: make alignment logic work over multiple lines...
            if m.group(1)[0] == '.':
                desired_offset = int(m.group(1)[1:])
                space = ' ' * (desired_offset - state['offset'])
                state['offset'] = desired_offset
                state['lstart'] = m.end(0)
                return space
            #print 'delta:', m.start(0) - state.get('lstart',0)
            state['offset'] = state['offset'] + m.start(0) - state.get('lstart',0)
            
            state['lstart'] = m.end(0)

            # <HTML>
            s = ''
            if state.get('needrestore'):
                s += '</span>'
            # </HTML>
            
            state['needrestore'] = True
            # <HTML>
            if self._style:
                return s + '<span class="%s">' % m.group(1)
            else:
                ctype, cval = self._cmap[m.group(1)]
                return s + '<span style="%s: %s;">' % (self._CTYPE_MAP[ctype],
                                                       cval)
            # </HTML>

        ostr = self._pat.sub(map_helper, msg)
        # <HTML>
        if state.get('needrestore'):
            ostr += '</span>'
        # </HTML>

        if self._indentLevel:
            indent = ' ' * self._indentLevel
            # TODO: also handle the wrapping as required
            ostr = indent + ostr.replace('\n', '\n' + indent)
        
        self.fout.write(ostr + '\n')

    def _colorcode_to_hex(self, colorcode):
        return '%02x%02x%02x' % self._crack_colorcode(colorcode)
    
    def map_fg(self, name, code):
        self._cmap[name] = ('fg', self._colorcode_to_hex(code))

    def map_bg(self, name, code):
        self._cmap[name] = ('bg', self._colorcode_to_hex(code))
        
    def write_styles(self):
        # make the 'n' foreground color the link color
        self.fout.write('a {color: #%s;}\n' % (self._cmap['n'][1],))
        for key, val in self._cmap.items():
            ctype, cval = val
            
            self.fout.write('.%s {%s: #%s;}\n' %
                            (key, self._CTYPE_MAP[ctype], cval))

    def write_html_intro(self, title='A PyFlam Document'):
        self.fout.write('<html><head><title>%s</title>\n' % title)
        if self._style:
            self.fout.write('<style type="text/css">\n')
            self.write_styles()
            self.fout.write('</style></head>\n')
        self.fout.write('<body bgcolor="#000000"><pre>')
        
    def write_html_outro(self):
        self.fout.write('</pre></body></html>')

    def close(self):
        self.write_html_outro()
        self.fout.close()
        self.fout = None

pout = FlamOut()
