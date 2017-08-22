# Gaudy Python GDB Commands #

Extra commands for your GDB, lovingly wrapped in all the colors of the rainbow.
All of the colors, all at once.

## What Do They Do? ##

### cbt: colorizing backtrace ###



## How Do I Install Them? ##

### Whoops, Dependencies ###

I've been out of the Python game for quite some time.  Let's just install the
deps globally, why not?

`pip install strictyaml`

### Okay, I did that, so... ###

The old way is to add something like the following to your `~/.gdbinit`.  But
the near future is cribbing tromey's installation and python structuring at
https://github.com/tromey/gdb-helpers

```
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
```

Then you can use:
- "cbt" as an awesome colorized backtrace
- "cbt terse" produces the bare necessities of a colorized backtrace
- "cbt full" is like "cbt" but with locals displayed too.
- "sl" as an awesome colorized / syntax highlighting "list"-like command.
- "mbt" as a colorized fused C++/JS mozilla-specific backtrace

## How Do I Develop Them? ##

That's right, by reading this far, you've earned the right to help develop these
further!

Python has built-in reloading, so after you make sure you have the following in
your `.gdbinit `:
```
python from imp import reload
```
...you can do the following:
- `python reload(gdbaudy.bt)`
- `python reload(gdbaudy.pp)`
