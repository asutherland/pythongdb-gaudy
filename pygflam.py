
from pyflam import *

from pygments import highlight
from pygments.lexers import get_lexer_for_filename
from pygments.formatter import Formatter
from pygments.filter import Filter
from pygments.util import get_bool_opt, get_int_opt

from pygments.style import Style
from pygments.token import Token, Comment, Name, Keyword, \
    Generic, Number, String, Whitespace, Punctuation, Operator

class FlamFruityStyle(Style):
    """
    Augmented version of pygments' FruityStyle from 0.10-1ubuntu2
    
    !!! from the class docstring:

    Pygments version of the "native" vim theme.

    !!! from the fruity.py file docstring:

    pygments.styles.fruity
    ~~~~~~~~~~~~~~~~~~~~~~

    pygments version of my "fruity" vim theme.

    :copyright: 2007 by Armin Ronacher.
    :license: BSD, see LICENSE for more details.
    """

    background_color = '#111111'

    styles = {
        Whitespace:         '#888888',
        Token:              '#ffffff',
        Generic.Output:     '#444444 bg:#222222',
        Keyword:            '#fb660a bold',
        Keyword.Pseudo:     'nobold',
        Number:             '#0086f7 bold',
        Name.Tag:           '#fb660a bold',
        Name.Variable:      '#fb660a',
        Name.Constant:      '#fb660a',
        Comment:            '#008800 bg:#0f140f italic',
        Name.Attribute:     '#ff0086 bold',
        String:             '#0086d2',
        Name.Function:      '#ff0086 bold',
        Generic.Heading:    '#ffffff bold',
        Keyword.Type:       '#cdcaa9 bold',
        Generic.Subheading: '#ffffff bold',
        Name.Constant:      '#0086d2',
        Comment.Preproc:    '#ff0007 bold',
        ### augmentations follow from here
        ## This is where :: should be, but...
        Keyword.Declaration: '#888888',
        ## it is really in operator
        Operator:            '#888888',
        Punctuation:         '#888888',
        # mozilla code filter color things...
        Name.Variable.Static: '#fb660a',
        Name.Variable.Member: '#ff8686',
        Name.Variable.Argument: '#cd0a86', #ff0086
        Name.Interface: '#cd660a', # fb660a
        Name.Class: '#22cacd', ##ff0086',
        Name.Class.Scaffolding: '#888844',
    }

class MozillaCodeFilter(Filter):
    SCAFFOLDING = set([
        'nsresult',
        'nsRefPtr', 'nsCOMPtr',
        'nsTArray', 'nsTObserverArray',
        ])
    NS_SCAFFOLDING = set([
        'NS_IMETHODIMP',
        'NS_ENSURE_ARG_POINTER',
        #'NS_FAILED', 'NS_SUCCEEDED',
        'NS_ENSURE_TRUE', 'NS_ENSURE_SUCCESS',
        'NS_ADDREF', 'NS_IF_ADDREF',
        'NS_ASSERTION',
        ])
    NS_NATIVE_TYPES = set([
        'PRUint32', 'PRInt32',
        'PRBool',
        ])
    NS_RICH_TYPES = set([
        'nsString', 'nsDependentString',
        'nsCString', 'nsCDependentString',
        ])

    def __init__(self, **options):
        Filter.__init__(self, **options)
    
    def filter(self, lexer, stream):
        for ttype, value in stream:
            # highlight included filenames specially
            if ttype is Comment.Preproc:
                if value.startswith('include '):
                    yield Comment.Preproc, 'include '
                    yield String, value[8:]
                    continue
            # variable/class/interface highlighting
            elif ttype is Name:
                # have control flow capture short names so we don't need to
                #  make all kinds of checks below
                if len(value) < 3:
                    pass
                # arguments
                elif value[0] == 'a':
                    if (value[1].isupper()):
                        yield Name.Variable.Argument, value
                        continue
                # static fields
                elif value[0] == 's':
                    if (value[1] == '_' or value[1].isupper()):
                        yield Name.Variable.Static, value
                        continue
                # member fields
                elif value[0] == 'm' and (value[1] == '_' or value[1].isupper()):
                    yield Name.Variable.Member, value
                    continue
                elif value.startswith('ns'):
                    # interfaces
                    if value[2] == 'I':
                        yield Name.Interface, value
                        continue
                    # classes
                    elif value.startswith('ns'):
                        if value == "nsnull":
                            yield Name.Constant, value
                            continue
                        elif value in self.SCAFFOLDING:
                            yield Name.Class.Scaffolding, value
                            continue
                        elif value in self.NS_RICH_TYPES:
                            yield Keyword.Type, value
                            continue
                        yield Name.Class, value
                        continue
                elif value.startswith('NS_'):
                    if value in self.NS_SCAFFOLDING:
                        yield Name.Class.Scaffolding, value
                        continue
                elif value.startswith('PR'):
                    if value in ('PR_TRUE', 'PR_FALSE'):
                        yield Name.Constant, value
                        continue
                    elif value in self.NS_NATIVE_TYPES:
                        yield Keyword.Type, value
                        continue


            yield ttype, value

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
            #print ttype, value
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
                fmtstr = '{n}%5d %s' % (lineno, fmtstr)
            if lineno in self.magic_lines:
                color_name = self.magic_lines[lineno]
                fmtstr = '{%s}%s{-bg}' % (color_name, fmtstr)
            
            self.pout(fmtstr, *data)

def flamhighlight(filename, **flamoptions):
    lexer = get_lexer_for_filename(filename)
    lexer.add_filter(MozillaCodeFilter())
    formatter = FlamMagicFormatter(style=FlamFruityStyle, **flamoptions)
    f = open(filename, 'r')
    highlight(f.read(), lexer, formatter)
    f.close()

if __name__ == '__main__':
    import sys
    flamhighlight(sys.argv[1],
                  show_lines=True,
                  #line_range=(585, 615),#(40,80)
                  magic_lines={45: 'curline'},
                  bg_colors={'curline': 0x35})
