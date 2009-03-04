# chroniquery, a chronicle-recorder python interface/abstraction library
#    Copyright (C) 2007 Andrew Sutherland (sombrero@alum.mit.edu)
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

### IMPORTANTE!  pyflam is intended to be its own standalone library
###  and will be released under at least LGPL v3 if not something
###  more permissive.  However, it's been promoted up to GPL v3 for
###  inclusion with chroniquery for now. 

import re, sys
        
class FlamOut(object):
    def __init__(self):
        self._cmap = {}
        self._pat = re.compile('(?:{([^}]+)})|(%([-#0 +]*)(\d*)\.?(\d*)([sdx]))')

        self.init_map()

        self._indentLevel = 0
        self._verbose = False

    def configure(self, **kwargs):
        self._verbose = kwargs.get('verbose', self._verbose)

    def init_map(self):
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
    
    def map_fg(self, name, code):
        self._cmap[name] = '\x1b[38;5;%dm' % code

    def map_bg(self, name, code):
        self._cmap[name] = '\x1b[48;5;%dm' % code

    def i(self, indentAdjust):
        self._indentLevel += indentAdjust
        if self._indentLevel < 0:
            self._indentLevel = 0

    def __call__(self, msg, *args, **kwargs):
        state = {'offset': self._indentLevel, 'iarg': 0}
        def map_helper(m):
            state['offset'] = state['offset'] + m.start(0) - state.get('lstart',0)
            state['lstart'] = m.end(0)

            if m.group(2) is not None:
                iarg = state['iarg']
                if m.group(6) == 'x':
                    v = '0x%x' % args[iarg]
                else:
                    v = str(args[iarg])
                
                #%([#0- +]*)(\d*)(?:\.(\d*))?[sdx]
                alignLeft = False
                if m.group(3):
                    conversionFlags = m.group(3)
                    if '-' in conversionFlags:
                        alignLeft = True
                if m.group(4):
                    mini = int(m.group(4))
                else:
                    mini = 0
                if m.group(5):
                    limit = int(m.group(5))
                else:
                    limit = 64000
                    
                if len(v) > limit:
                    v = v[:limit]
                if len(v) < mini:
                    if alignLeft:
                        v += ' ' * (mini - len(v))
                    else:
                        v = ' ' * (mini - len(v)) + v
                            
                state['offset'] = state['offset'] + len(v)
                state['iarg'] = iarg + 1
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
            
            state['needrestore'] = True
            return self._cmap[m.group(1)]
        

        ostr = self._pat.sub(map_helper, msg)
        if 'needrestore' in state:
            ostr += self._cmap['n']

        if self._indentLevel:
            indent = ' ' * self._indentLevel
            # TODO: also handle the wrapping as required
            ostr = indent + ostr.replace('\n', '\n' + indent)
        
        print ostr
    
    def pp(self, o, label=None, indent=0):
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
    def __init__(self, fout, style=True):
        super(FlamHTML, self).__init__()
        
        self._style = style
        self.fout = fout

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

pout = FlamOut()
