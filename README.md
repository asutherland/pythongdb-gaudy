to your ~/.gdbinit, add

### CUT HERE ###
python import sys
python sys.path[0:0] = ["/path/to/pythongdb-gaudy/"]
# backtrace, no external dependencies
python import gdbaudy.bt
# syntax-highlighting source list, needs pygments
python import gdbaudy.pyglist
# mozilla backtrace...
#  needs http://hg.mozilla.org/users/jblandy_mozilla.com/archer-mozilla/
#  (follow its install instructions / make sure it is on your sys.path)
python import gdbaudy.mozbt
### STOP CUTTING HERE ###

Then you can use:
- "cbt" as an awesome colorized backtrace
- "cbt terse" produces the bare necessities of a colorized backtrace
- "cbt full" is like "cbt" but with locals displayed too.
- "sl" as an awesome colorized / syntax highlighting "list"-like command.
- "mbt" as a colorized fused C++/JS mozilla-specific backtrace
