
from pyflam import *

from pygments import highlight
from pygments.token import Token
from pygments.lexers import get_lexer_for_filename
from pygments.filter import Filter
from pygments.formatter import Formatter
from pygments.styles import get_style_by_name
from pygments.util import get_bool_opt, get_int_opt

Meta = Token.Meta
CurrentLine = Meta.CurrentLine

class FlamMagicFormatter(Formatter):
    '''
    Format our output using pyflam, also introducing some exciting features:
    - Highlight 
    '''
    name = 'PyFlam'
    aliases = ['pyflam', 'flam']
    filenames = []

    def __init__(self, **options):
        Formatter.__init__(self, **options)

        self.show_lines = get_bool_opt(options, 'show_lines', True)

        if 'line_range' in options:
            self.first_line, self.last_line = options['line_range']
        else:
            self.first_line = 1
            self.last_line = 256 * 256

        if 'magic_lines' in options:
            self.magic_lines = options['magic_lines']
        else:
            self.magic_lines = []

        self.pout = FlamOut()
        self._init_styles()
        if 'bg_colors' in options:
            for name, code in options['bg_colors'].items():
                self.pout.map_bg(name, code)

    def _init_styles(self):
        self.ignore_tokens = set()
        explicitly_set = set()

        def set_for_type_and_children(ttype, code):
            self.pout.map_fg(str(ttype), code)
            for subtype in ttype.subtypes:
                if subtype not in explicitly_set:
                    set_for_type_and_children(subtype, code)

        # this yields a stream of (token, style_for_token(token))
        # where style_for_token produces a dict where we only care about color
        for ttype, sdef in self.style:
            if sdef['color']: # color is optional! (None if not defined)
                code = self.pout.hexcolor_to_colorcode(sdef['color'])
                explicitly_set.add(ttype)
                set_for_type_and_children(ttype, code)
            else:
                self.ignore_tokens.add(ttype)

    def _line_consolidator(self, tokensource):
        '''
        Yields (line number, format string, data)
        '''
        fmtstr = ''
        data = []
        lineno = 1
        for ttype, value in tokensource:
            if ttype in self.ignore_tokens:
                fmtbit = '%s'
            else:
                fmtbit = '{' + str(ttype) + '}%s'
            parts = value.split('\n')
            for part in parts[:-1]:
                fmtstr += fmtbit
                data.append(part)
                yield lineno, fmtstr, data
                lineno += 1
                fmtstr = ''
                data = []
            if parts[-1]:
                fmtstr += fmtbit
                data.append(parts[-1])
        if fmtstr:
            yield lineno, fmtstr, data

    def format(self, tokensource, outfile):
        #self.pout.fout = outfile
        
        for lineno, fmtstr, data in self._line_consolidator(tokensource):
            if lineno < self.first_line:
                continue
            if lineno > self.last_line:
                break
            if self.show_lines:
                fmtstr = '{n}%3.3d%s' % (lineno, fmtstr)
            if lineno in self.magic_lines:
                color_name = self.magic_lines[lineno]
                fmtstr = '{%s}%s{-bg}' % (color_name, fmtstr)
            
            self.pout(fmtstr, *data)

def flamhighlight(filename, **flamoptions):
    lexer = get_lexer_for_filename(filename)
    formatter = FlamMagicFormatter(style='fruity', **flamoptions)
    f = open(filename, 'r')
    highlight(f.read(), lexer, formatter)
    f.close()

if __name__ == '__main__':
    import sys
    flamhighlight(sys.argv[1],
                  show_lines=True,
                  line_range=(40, 80),
                  magic_lines={45: 'curline'},
                  bg_colors={'curline': 0x35})
