# dispatch.py - command dispatching for mercurial
#
# Copyright 2005-2007 Matt Mackall <mpm@selenic.com>
#
# This software may be used and distributed according to the terms of the
# GNU General Public License version 2 or any later version.

from __future__ import absolute_import, print_function

import difflib
import errno
import getopt
import os
import pdb
import re
import signal
import sys
import time
import traceback


from .i18n import _
from .pycompat import getattr

from hgdemandimport import tracing

from . import (
    cmdutil,
    color,
    commands,
    demandimport,
    encoding,
    error,
    extensions,
    fancyopts,
    help,
    hg,
    hook,
    profiling,
    pycompat,
    registrar,
    scmutil,
    ui as uimod,
    util,
)

from .utils import (
    procutil,
    stringutil,
)


class request(object):
    def __init__(
        self,
        args,
        ui=None,
        repo=None,
        fin=None,
        fout=None,
        ferr=None,
        fmsg=None,
        prereposetups=None,
    ):
        self.args = args
        self.ui = ui
        self.repo = repo

        # input/output/error streams
        self.fin = fin
        self.fout = fout
        self.ferr = ferr
        # separate stream for status/error messages
        self.fmsg = fmsg

        # remember options pre-parsed by _earlyparseopts()
        self.earlyoptions = {}

        # reposetups which run before extensions, useful for chg to pre-fill
        # low-level repo state (for example, changelog) before extensions.
        self.prereposetups = prereposetups or []

        # store the parsed and canonical command
        self.canonical_command = None

    def _runexithandlers(self):
        exc = None
        handlers = self.ui._exithandlers
        try:
            while handlers:
                func, args, kwargs = handlers.pop()
                try:
                    func(*args, **kwargs)
                except:  # re-raises below
                    if exc is None:
                        exc = sys.exc_info()[1]
                    self.ui.warnnoi18n(b'error in exit handlers:\n')
                    self.ui.traceback(force=True)
        finally:
            if exc is not None:
                raise exc


def run():
    b"run the command in sys.argv"
    initstdio()
    with tracing.log('parse args into request'):
        req = request(pycompat.sysargv[1:])
    err = None
    try:
        status = dispatch(req)
    except error.StdioError as e:
        err = e
        status = -1

    # In all cases we try to flush stdio streams.
    if util.safehasattr(req.ui, b'fout'):
        try:
            req.ui.fout.flush()
        except IOError as e:
            err = e
            status = -1

    if util.safehasattr(req.ui, b'ferr'):
        try:
            if err is not None and err.errno != errno.EPIPE:
                req.ui.ferr.write(
                    b'abort: %s\n' % encoding.strtolocal(err.strerror)
                )
            req.ui.ferr.flush()
        # There's not much we can do about an I/O error here. So (possibly)
        # change the status code and move on.
        except IOError:
            status = -1

    _silencestdio()
    sys.exit(status & 255)


if pycompat.ispy3:

    def initstdio():
        pass

    def _silencestdio():
        for fp in (sys.stdout, sys.stderr):
            # Check if the file is okay
            try:
                fp.flush()
                continue
            except IOError:
                pass
            # Otherwise mark it as closed to silence "Exception ignored in"
            # message emitted by the interpreter finalizer. Be careful to
            # not close procutil.stdout, which may be a fdopen-ed file object
            # and its close() actually closes the underlying file descriptor.
            try:
                fp.close()
            except IOError:
                pass


else:

    def initstdio():
        for fp in (sys.stdin, sys.stdout, sys.stderr):
            procutil.setbinary(fp)

    def _silencestdio():
        pass


def _getsimilar(symbols, value):
    sim = lambda x: difflib.SequenceMatcher(None, value, x).ratio()
    # The cutoff for similarity here is pretty arbitrary. It should
    # probably be investigated and tweaked.
    return [s for s in symbols if sim(s) > 0.6]


def _reportsimilar(write, similar):
    if len(similar) == 1:
        write(_(b"(did you mean %s?)\n") % similar[0])
    elif similar:
        ss = b", ".join(sorted(similar))
        write(_(b"(did you mean one of %s?)\n") % ss)


def _formatparse(write, inst):
    similar = []
    if isinstance(inst, error.UnknownIdentifier):
        # make sure to check fileset first, as revset can invoke fileset
        similar = _getsimilar(inst.symbols, inst.function)
    if len(inst.args) > 1:
        write(
            _(b"hg: parse error at %s: %s\n")
            % (pycompat.bytestr(inst.args[1]), inst.args[0])
        )
        if inst.args[0].startswith(b' '):
            write(_(b"unexpected leading whitespace\n"))
    else:
        write(_(b"hg: parse error: %s\n") % inst.args[0])
        _reportsimilar(write, similar)
    if inst.hint:
        write(_(b"(%s)\n") % inst.hint)


def _formatargs(args):
    return b' '.join(procutil.shellquote(a) for a in args)


def dispatch(req):
    """run the command specified in req.args; returns an integer status code"""
    with tracing.log('dispatch.dispatch'):
        if req.ferr:
            ferr = req.ferr
        elif req.ui:
            ferr = req.ui.ferr
        else:
            ferr = procutil.stderr

        try:
            if not req.ui:
                req.ui = uimod.ui.load()
            req.earlyoptions.update(_earlyparseopts(req.ui, req.args))
            if req.earlyoptions[b'traceback']:
                req.ui.setconfig(b'ui', b'traceback', b'on', b'--traceback')

            # set ui streams from the request
            if req.fin:
                req.ui.fin = req.fin
            if req.fout:
                req.ui.fout = req.fout
            if req.ferr:
                req.ui.ferr = req.ferr
            if req.fmsg:
                req.ui.fmsg = req.fmsg
        except error.Abort as inst:
            ferr.write(_(b"abort: %s\n") % inst)
            if inst.hint:
                ferr.write(_(b"(%s)\n") % inst.hint)
            return -1
        except error.ParseError as inst:
            _formatparse(ferr.write, inst)
            return -1

        msg = _formatargs(req.args)
        starttime = util.timer()
        ret = 1  # default of Python exit code on unhandled exception
        try:
            ret = _runcatch(req) or 0
        except error.ProgrammingError as inst:
            req.ui.error(_(b'** ProgrammingError: %s\n') % inst)
            if inst.hint:
                req.ui.error(_(b'** (%s)\n') % inst.hint)
            raise
        except KeyboardInterrupt as inst:
            try:
                if isinstance(inst, error.SignalInterrupt):
                    msg = _(b"killed!\n")
                else:
                    msg = _(b"interrupted!\n")
                req.ui.error(msg)
            except error.SignalInterrupt:
                # maybe pager would quit without consuming all the output, and
                # SIGPIPE was raised. we cannot print anything in this case.
                pass
            except IOError as inst:
                if inst.errno != errno.EPIPE:
                    raise
            ret = -1
        finally:
            duration = util.timer() - starttime
            req.ui.flush()
            if req.ui.logblockedtimes:
                req.ui._blockedtimes[b'command_duration'] = duration * 1000
                req.ui.log(
                    b'uiblocked',
                    b'ui blocked ms\n',
                    **pycompat.strkwargs(req.ui._blockedtimes)
                )
            return_code = ret & 255
            req.ui.log(
                b"commandfinish",
                b"%s exited %d after %0.2f seconds\n",
                msg,
                return_code,
                duration,
                return_code=return_code,
                duration=duration,
                canonical_command=req.canonical_command,
            )
            try:
                req._runexithandlers()
            except:  # exiting, so no re-raises
                ret = ret or -1
        return ret


def _runcatch(req):
    with tracing.log('dispatch._runcatch'):

        def catchterm(*args):
            raise error.SignalInterrupt

        ui = req.ui
        try:
            for name in b'SIGBREAK', b'SIGHUP', b'SIGTERM':
                num = getattr(signal, name, None)
                if num:
                    signal.signal(num, catchterm)
        except ValueError:
            pass  # happens if called in a thread

        def _runcatchfunc():
            realcmd = None
            try:
                cmdargs = fancyopts.fancyopts(
                    req.args[:], commands.globalopts, {}
                )
                cmd = cmdargs[0]
                aliases, entry = cmdutil.findcmd(cmd, commands.table, False)
                realcmd = aliases[0]
            except (
                error.UnknownCommand,
                error.AmbiguousCommand,
                IndexError,
                getopt.GetoptError,
            ):
                # Don't handle this here. We know the command is
                # invalid, but all we're worried about for now is that
                # it's not a command that server operators expect to
                # be safe to offer to users in a sandbox.
                pass
            if realcmd == b'serve' and b'--stdio' in cmdargs:
                # We want to constrain 'hg serve --stdio' instances pretty
                # closely, as many shared-ssh access tools want to grant
                # access to run *only* 'hg -R $repo serve --stdio'. We
                # restrict to exactly that set of arguments, and prohibit
                # any repo name that starts with '--' to prevent
                # shenanigans wherein a user does something like pass
                # --debugger or --config=ui.debugger=1 as a repo
                # name. This used to actually run the debugger.
                if (
                    len(req.args) != 4
                    or req.args[0] != b'-R'
                    or req.args[1].startswith(b'--')
                    or req.args[2] != b'serve'
                    or req.args[3] != b'--stdio'
                ):
                    raise error.Abort(
                        _(b'potentially unsafe serve --stdio invocation: %s')
                        % (stringutil.pprint(req.args),)
                    )

            try:
                debugger = b'pdb'
                debugtrace = {b'pdb': pdb.set_trace}
                debugmortem = {b'pdb': pdb.post_mortem}

                # read --config before doing anything else
                # (e.g. to change trust settings for reading .hg/hgrc)
                cfgs = _parseconfig(req.ui, req.earlyoptions[b'config'])

                if req.repo:
                    # copy configs that were passed on the cmdline (--config) to
                    # the repo ui
                    for sec, name, val in cfgs:
                        req.repo.ui.setconfig(
                            sec, name, val, source=b'--config'
                        )

                # developer config: ui.debugger
                debugger = ui.config(b"ui", b"debugger")
                debugmod = pdb
                if not debugger or ui.plain():
                    # if we are in HGPLAIN mode, then disable custom debugging
                    debugger = b'pdb'
                elif req.earlyoptions[b'debugger']:
                    # This import can be slow for fancy debuggers, so only
                    # do it when absolutely necessary, i.e. when actual
                    # debugging has been requested
                    with demandimport.deactivated():
                        try:
                            debugmod = __import__(debugger)
                        except ImportError:
                            pass  # Leave debugmod = pdb

                debugtrace[debugger] = debugmod.set_trace
                debugmortem[debugger] = debugmod.post_mortem

                # enter the debugger before command execution
                if req.earlyoptions[b'debugger']:
                    ui.warn(
                        _(
                            b"entering debugger - "
                            b"type c to continue starting hg or h for help\n"
                        )
                    )

                    if (
                        debugger != b'pdb'
                        and debugtrace[debugger] == debugtrace[b'pdb']
                    ):
                        ui.warn(
                            _(
                                b"%s debugger specified "
                                b"but its module was not found\n"
                            )
                            % debugger
                        )
                    with demandimport.deactivated():
                        debugtrace[debugger]()
                try:
                    return _dispatch(req)
                finally:
                    ui.flush()
            except:  # re-raises
                # enter the debugger when we hit an exception
                if req.earlyoptions[b'debugger']:
                    traceback.print_exc()
                    debugmortem[debugger](sys.exc_info()[2])
                raise

        return _callcatch(ui, _runcatchfunc)


def _callcatch(ui, func):
    """like scmutil.callcatch but handles more high-level exceptions about
    config parsing and commands. besides, use handlecommandexception to handle
    uncaught exceptions.
    """
    try:
        return scmutil.callcatch(ui, func)
    except error.AmbiguousCommand as inst:
        ui.warn(
            _(b"hg: command '%s' is ambiguous:\n    %s\n")
            % (inst.args[0], b" ".join(inst.args[1]))
        )
    except error.CommandError as inst:
        if inst.args[0]:
            ui.pager(b'help')
            msgbytes = pycompat.bytestr(inst.args[1])
            ui.warn(_(b"hg %s: %s\n") % (inst.args[0], msgbytes))
            commands.help_(ui, inst.args[0], full=False, command=True)
        else:
            ui.warn(_(b"hg: %s\n") % inst.args[1])
            ui.warn(_(b"(use 'hg help -v' for a list of global options)\n"))
    except error.ParseError as inst:
        _formatparse(ui.warn, inst)
        return -1
    except error.UnknownCommand as inst:
        nocmdmsg = _(b"hg: unknown command '%s'\n") % inst.args[0]
        try:
            # check if the command is in a disabled extension
            # (but don't check for extensions themselves)
            formatted = help.formattedhelp(
                ui, commands, inst.args[0], unknowncmd=True
            )
            ui.warn(nocmdmsg)
            ui.write(formatted)
        except (error.UnknownCommand, error.Abort):
            suggested = False
            if len(inst.args) == 2:
                sim = _getsimilar(inst.args[1], inst.args[0])
                if sim:
                    ui.warn(nocmdmsg)
                    _reportsimilar(ui.warn, sim)
                    suggested = True
            if not suggested:
                ui.warn(nocmdmsg)
                ui.warn(_(b"(use 'hg help' for a list of commands)\n"))
    except IOError:
        raise
    except KeyboardInterrupt:
        raise
    except:  # probably re-raises
        if not handlecommandexception(ui):
            raise

    return -1


def aliasargs(fn, givenargs):
    args = []
    # only care about alias 'args', ignore 'args' set by extensions.wrapfunction
    if not util.safehasattr(fn, b'_origfunc'):
        args = getattr(fn, 'args', args)
    if args:
        cmd = b' '.join(map(procutil.shellquote, args))

        nums = []

        def replacer(m):
            num = int(m.group(1)) - 1
            nums.append(num)
            if num < len(givenargs):
                return givenargs[num]
            raise error.Abort(_(b'too few arguments for command alias'))

        cmd = re.sub(br'\$(\d+|\$)', replacer, cmd)
        givenargs = [x for i, x in enumerate(givenargs) if i not in nums]
        args = pycompat.shlexsplit(cmd)
    return args + givenargs


def aliasinterpolate(name, args, cmd):
    '''interpolate args into cmd for shell aliases

    This also handles $0, $@ and "$@".
    '''
    # util.interpolate can't deal with "$@" (with quotes) because it's only
    # built to match prefix + patterns.
    replacemap = dict((b'$%d' % (i + 1), arg) for i, arg in enumerate(args))
    replacemap[b'$0'] = name
    replacemap[b'$$'] = b'$'
    replacemap[b'$@'] = b' '.join(args)
    # Typical Unix shells interpolate "$@" (with quotes) as all the positional
    # parameters, separated out into words. Emulate the same behavior here by
    # quoting the arguments individually. POSIX shells will then typically
    # tokenize each argument into exactly one word.
    replacemap[b'"$@"'] = b' '.join(procutil.shellquote(arg) for arg in args)
    # escape '\$' for regex
    regex = b'|'.join(replacemap.keys()).replace(b'$', br'\$')
    r = re.compile(regex)
    return r.sub(lambda x: replacemap[x.group()], cmd)


class cmdalias(object):
    def __init__(self, ui, name, definition, cmdtable, source):
        self.name = self.cmd = name
        self.cmdname = b''
        self.definition = definition
        self.fn = None
        self.givenargs = []
        self.opts = []
        self.help = b''
        self.badalias = None
        self.unknowncmd = False
        self.source = source

        try:
            aliases, entry = cmdutil.findcmd(self.name, cmdtable)
            for alias, e in pycompat.iteritems(cmdtable):
                if e is entry:
                    self.cmd = alias
                    break
            self.shadows = True
        except error.UnknownCommand:
            self.shadows = False

        if not self.definition:
            self.badalias = _(b"no definition for alias '%s'") % self.name
            return

        if self.definition.startswith(b'!'):
            shdef = self.definition[1:]
            self.shell = True

            def fn(ui, *args):
                env = {b'HG_ARGS': b' '.join((self.name,) + args)}

                def _checkvar(m):
                    if m.groups()[0] == b'$':
                        return m.group()
                    elif int(m.groups()[0]) <= len(args):
                        return m.group()
                    else:
                        ui.debug(
                            b"No argument found for substitution "
                            b"of %i variable in alias '%s' definition.\n"
                            % (int(m.groups()[0]), self.name)
                        )
                        return b''

                cmd = re.sub(br'\$(\d+|\$)', _checkvar, shdef)
                cmd = aliasinterpolate(self.name, args, cmd)
                return ui.system(
                    cmd, environ=env, blockedtag=b'alias_%s' % self.name
                )

            self.fn = fn
            self.alias = True
            self._populatehelp(ui, name, shdef, self.fn)
            return

        try:
            args = pycompat.shlexsplit(self.definition)
        except ValueError as inst:
            self.badalias = _(b"error in definition for alias '%s': %s") % (
                self.name,
                stringutil.forcebytestr(inst),
            )
            return
        earlyopts, args = _earlysplitopts(args)
        if earlyopts:
            self.badalias = _(
                b"error in definition for alias '%s': %s may "
                b"only be given on the command line"
            ) % (self.name, b'/'.join(pycompat.ziplist(*earlyopts)[0]))
            return
        self.cmdname = cmd = args.pop(0)
        self.givenargs = args

        try:
            tableentry = cmdutil.findcmd(cmd, cmdtable, False)[1]
            if len(tableentry) > 2:
                self.fn, self.opts, cmdhelp = tableentry
            else:
                self.fn, self.opts = tableentry
                cmdhelp = None

            self.alias = True
            self._populatehelp(ui, name, cmd, self.fn, cmdhelp)

        except error.UnknownCommand:
            self.badalias = _(
                b"alias '%s' resolves to unknown command '%s'"
            ) % (self.name, cmd,)
            self.unknowncmd = True
        except error.AmbiguousCommand:
            self.badalias = _(
                b"alias '%s' resolves to ambiguous command '%s'"
            ) % (self.name, cmd)

    def _populatehelp(self, ui, name, cmd, fn, defaulthelp=None):
        # confine strings to be passed to i18n.gettext()
        cfg = {}
        for k in (b'doc', b'help', b'category'):
            v = ui.config(b'alias', b'%s:%s' % (name, k), None)
            if v is None:
                continue
            if not encoding.isasciistr(v):
                self.badalias = _(
                    b"non-ASCII character in alias definition '%s:%s'"
                ) % (name, k)
                return
            cfg[k] = v

        self.help = cfg.get(b'help', defaulthelp or b'')
        if self.help and self.help.startswith(b"hg " + cmd):
            # drop prefix in old-style help lines so hg shows the alias
            self.help = self.help[4 + len(cmd) :]

        self.owndoc = b'doc' in cfg
        doc = cfg.get(b'doc', pycompat.getdoc(fn))
        if doc is not None:
            doc = pycompat.sysstr(doc)
        self.__doc__ = doc

        self.helpcategory = cfg.get(
            b'category', registrar.command.CATEGORY_NONE
        )

    @property
    def args(self):
        args = pycompat.maplist(util.expandpath, self.givenargs)
        return aliasargs(self.fn, args)

    def __getattr__(self, name):
        adefaults = {
            r'norepo': True,
            r'intents': set(),
            r'optionalrepo': False,
            r'inferrepo': False,
        }
        if name not in adefaults:
            raise AttributeError(name)
        if self.badalias or util.safehasattr(self, b'shell'):
            return adefaults[name]
        return getattr(self.fn, name)

    def __call__(self, ui, *args, **opts):
        if self.badalias:
            hint = None
            if self.unknowncmd:
                try:
                    # check if the command is in a disabled extension
                    cmd, ext = extensions.disabledcmd(ui, self.cmdname)[:2]
                    hint = _(b"'%s' is provided by '%s' extension") % (cmd, ext)
                except error.UnknownCommand:
                    pass
            raise error.Abort(self.badalias, hint=hint)
        if self.shadows:
            ui.debug(
                b"alias '%s' shadows command '%s'\n" % (self.name, self.cmdname)
            )

        ui.log(
            b'commandalias',
            b"alias '%s' expands to '%s'\n",
            self.name,
            self.definition,
        )
        if util.safehasattr(self, b'shell'):
            return self.fn(ui, *args, **opts)
        else:
            try:
                return util.checksignature(self.fn)(ui, *args, **opts)
            except error.SignatureError:
                args = b' '.join([self.cmdname] + self.args)
                ui.debug(b"alias '%s' expands to '%s'\n" % (self.name, args))
                raise


class lazyaliasentry(object):
    """like a typical command entry (func, opts, help), but is lazy"""

    def __init__(self, ui, name, definition, cmdtable, source):
        self.ui = ui
        self.name = name
        self.definition = definition
        self.cmdtable = cmdtable.copy()
        self.source = source
        self.alias = True

    @util.propertycache
    def _aliasdef(self):
        return cmdalias(
            self.ui, self.name, self.definition, self.cmdtable, self.source
        )

    def __getitem__(self, n):
        aliasdef = self._aliasdef
        if n == 0:
            return aliasdef
        elif n == 1:
            return aliasdef.opts
        elif n == 2:
            return aliasdef.help
        else:
            raise IndexError

    def __iter__(self):
        for i in range(3):
            yield self[i]

    def __len__(self):
        return 3


def addaliases(ui, cmdtable):
    # aliases are processed after extensions have been loaded, so they
    # may use extension commands. Aliases can also use other alias definitions,
    # but only if they have been defined prior to the current definition.
    for alias, definition in ui.configitems(b'alias', ignoresub=True):
        try:
            if cmdtable[alias].definition == definition:
                continue
        except (KeyError, AttributeError):
            # definition might not exist or it might not be a cmdalias
            pass

        source = ui.configsource(b'alias', alias)
        entry = lazyaliasentry(ui, alias, definition, cmdtable, source)
        cmdtable[alias] = entry


def _parse(ui, args):
    options = {}
    cmdoptions = {}

    try:
        args = fancyopts.fancyopts(args, commands.globalopts, options)
    except getopt.GetoptError as inst:
        raise error.CommandError(None, stringutil.forcebytestr(inst))

    if args:
        cmd, args = args[0], args[1:]
        aliases, entry = cmdutil.findcmd(
            cmd, commands.table, ui.configbool(b"ui", b"strict")
        )
        cmd = aliases[0]
        args = aliasargs(entry[0], args)
        defaults = ui.config(b"defaults", cmd)
        if defaults:
            args = (
                pycompat.maplist(util.expandpath, pycompat.shlexsplit(defaults))
                + args
            )
        c = list(entry[1])
    else:
        cmd = None
        c = []

    # combine global options into local
    for o in commands.globalopts:
        c.append((o[0], o[1], options[o[1]], o[3]))

    try:
        args = fancyopts.fancyopts(args, c, cmdoptions, gnu=True)
    except getopt.GetoptError as inst:
        raise error.CommandError(cmd, stringutil.forcebytestr(inst))

    # separate global options back out
    for o in commands.globalopts:
        n = o[1]
        options[n] = cmdoptions[n]
        del cmdoptions[n]

    return (cmd, cmd and entry[0] or None, args, options, cmdoptions)


def _parseconfig(ui, config):
    """parse the --config options from the command line"""
    configs = []

    for cfg in config:
        try:
            name, value = [cfgelem.strip() for cfgelem in cfg.split(b'=', 1)]
            section, name = name.split(b'.', 1)
            if not section or not name:
                raise IndexError
            ui.setconfig(section, name, value, b'--config')
            configs.append((section, name, value))
        except (IndexError, ValueError):
            raise error.Abort(
                _(
                    b'malformed --config option: %r '
                    b'(use --config section.name=value)'
                )
                % pycompat.bytestr(cfg)
            )

    return configs


def _earlyparseopts(ui, args):
    options = {}
    fancyopts.fancyopts(
        args,
        commands.globalopts,
        options,
        gnu=not ui.plain(b'strictflags'),
        early=True,
        optaliases={b'repository': [b'repo']},
    )
    return options


def _earlysplitopts(args):
    """Split args into a list of possible early options and remainder args"""
    shortoptions = b'R:'
    # TODO: perhaps 'debugger' should be included
    longoptions = [b'cwd=', b'repository=', b'repo=', b'config=']
    return fancyopts.earlygetopt(
        args, shortoptions, longoptions, gnu=True, keepsep=True
    )


def runcommand(lui, repo, cmd, fullargs, ui, options, d, cmdpats, cmdoptions):
    # run pre-hook, and abort if it fails
    hook.hook(
        lui,
        repo,
        b"pre-%s" % cmd,
        True,
        args=b" ".join(fullargs),
        pats=cmdpats,
        opts=cmdoptions,
    )
    try:
        ret = _runcommand(ui, options, cmd, d)
        # run post-hook, passing command result
        hook.hook(
            lui,
            repo,
            b"post-%s" % cmd,
            False,
            args=b" ".join(fullargs),
            result=ret,
            pats=cmdpats,
            opts=cmdoptions,
        )
    except Exception:
        # run failure hook and re-raise
        hook.hook(
            lui,
            repo,
            b"fail-%s" % cmd,
            False,
            args=b" ".join(fullargs),
            pats=cmdpats,
            opts=cmdoptions,
        )
        raise
    return ret


def _getlocal(ui, rpath, wd=None):
    """Return (path, local ui object) for the given target path.

    Takes paths in [cwd]/.hg/hgrc into account."
    """
    if wd is None:
        try:
            wd = encoding.getcwd()
        except OSError as e:
            raise error.Abort(
                _(b"error getting current working directory: %s")
                % encoding.strtolocal(e.strerror)
            )
    path = cmdutil.findrepo(wd) or b""
    if not path:
        lui = ui
    else:
        lui = ui.copy()
        lui.readconfig(os.path.join(path, b".hg", b"hgrc"), path)

    if rpath:
        path = lui.expandpath(rpath)
        lui = ui.copy()
        lui.readconfig(os.path.join(path, b".hg", b"hgrc"), path)

    return path, lui


def _checkshellalias(lui, ui, args):
    """Return the function to run the shell alias, if it is required"""
    options = {}

    try:
        args = fancyopts.fancyopts(args, commands.globalopts, options)
    except getopt.GetoptError:
        return

    if not args:
        return

    cmdtable = commands.table

    cmd = args[0]
    try:
        strict = ui.configbool(b"ui", b"strict")
        aliases, entry = cmdutil.findcmd(cmd, cmdtable, strict)
    except (error.AmbiguousCommand, error.UnknownCommand):
        return

    cmd = aliases[0]
    fn = entry[0]

    if cmd and util.safehasattr(fn, b'shell'):
        # shell alias shouldn't receive early options which are consumed by hg
        _earlyopts, args = _earlysplitopts(args)
        d = lambda: fn(ui, *args[1:])
        return lambda: runcommand(
            lui, None, cmd, args[:1], ui, options, d, [], {}
        )


def _dispatch(req):
    args = req.args
    ui = req.ui

    # check for cwd
    cwd = req.earlyoptions[b'cwd']
    if cwd:
        os.chdir(cwd)

    rpath = req.earlyoptions[b'repository']
    path, lui = _getlocal(ui, rpath)

    uis = {ui, lui}

    if req.repo:
        uis.add(req.repo.ui)

    if (
        req.earlyoptions[b'verbose']
        or req.earlyoptions[b'debug']
        or req.earlyoptions[b'quiet']
    ):
        for opt in (b'verbose', b'debug', b'quiet'):
            val = pycompat.bytestr(bool(req.earlyoptions[opt]))
            for ui_ in uis:
                ui_.setconfig(b'ui', opt, val, b'--' + opt)

    if req.earlyoptions[b'profile']:
        for ui_ in uis:
            ui_.setconfig(b'profiling', b'enabled', b'true', b'--profile')

    profile = lui.configbool(b'profiling', b'enabled')
    with profiling.profile(lui, enabled=profile) as profiler:
        # Configure extensions in phases: uisetup, extsetup, cmdtable, and
        # reposetup
        extensions.loadall(lui)
        # Propagate any changes to lui.__class__ by extensions
        ui.__class__ = lui.__class__

        # (uisetup and extsetup are handled in extensions.loadall)

        # (reposetup is handled in hg.repository)

        addaliases(lui, commands.table)

        # All aliases and commands are completely defined, now.
        # Check abbreviation/ambiguity of shell alias.
        shellaliasfn = _checkshellalias(lui, ui, args)
        if shellaliasfn:
            # no additional configs will be set, set up the ui instances
            for ui_ in uis:
                extensions.populateui(ui_)
            return shellaliasfn()

        # check for fallback encoding
        fallback = lui.config(b'ui', b'fallbackencoding')
        if fallback:
            encoding.fallbackencoding = fallback

        fullargs = args
        cmd, func, args, options, cmdoptions = _parse(lui, args)

        # store the canonical command name in request object for later access
        req.canonical_command = cmd

        if options[b"config"] != req.earlyoptions[b"config"]:
            raise error.Abort(_(b"option --config may not be abbreviated!"))
        if options[b"cwd"] != req.earlyoptions[b"cwd"]:
            raise error.Abort(_(b"option --cwd may not be abbreviated!"))
        if options[b"repository"] != req.earlyoptions[b"repository"]:
            raise error.Abort(
                _(
                    b"option -R has to be separated from other options (e.g. not "
                    b"-qR) and --repository may only be abbreviated as --repo!"
                )
            )
        if options[b"debugger"] != req.earlyoptions[b"debugger"]:
            raise error.Abort(_(b"option --debugger may not be abbreviated!"))
        # don't validate --profile/--traceback, which can be enabled from now

        if options[b"encoding"]:
            encoding.encoding = options[b"encoding"]
        if options[b"encodingmode"]:
            encoding.encodingmode = options[b"encodingmode"]
        if options[b"time"]:

            def get_times():
                t = os.times()
                if t[4] == 0.0:
                    # Windows leaves this as zero, so use time.clock()
                    t = (t[0], t[1], t[2], t[3], time.clock())
                return t

            s = get_times()

            def print_time():
                t = get_times()
                ui.warn(
                    _(b"time: real %.3f secs (user %.3f+%.3f sys %.3f+%.3f)\n")
                    % (
                        t[4] - s[4],
                        t[0] - s[0],
                        t[2] - s[2],
                        t[1] - s[1],
                        t[3] - s[3],
                    )
                )

            ui.atexit(print_time)
        if options[b"profile"]:
            profiler.start()

        # if abbreviated version of this were used, take them in account, now
        if options[b'verbose'] or options[b'debug'] or options[b'quiet']:
            for opt in (b'verbose', b'debug', b'quiet'):
                if options[opt] == req.earlyoptions[opt]:
                    continue
                val = pycompat.bytestr(bool(options[opt]))
                for ui_ in uis:
                    ui_.setconfig(b'ui', opt, val, b'--' + opt)

        if options[b'traceback']:
            for ui_ in uis:
                ui_.setconfig(b'ui', b'traceback', b'on', b'--traceback')

        if options[b'noninteractive']:
            for ui_ in uis:
                ui_.setconfig(b'ui', b'interactive', b'off', b'-y')

        if cmdoptions.get(b'insecure', False):
            for ui_ in uis:
                ui_.insecureconnections = True

        # setup color handling before pager, because setting up pager
        # might cause incorrect console information
        coloropt = options[b'color']
        for ui_ in uis:
            if coloropt:
                ui_.setconfig(b'ui', b'color', coloropt, b'--color')
            color.setup(ui_)

        if stringutil.parsebool(options[b'pager']):
            # ui.pager() expects 'internal-always-' prefix in this case
            ui.pager(b'internal-always-' + cmd)
        elif options[b'pager'] != b'auto':
            for ui_ in uis:
                ui_.disablepager()

        # configs are fully loaded, set up the ui instances
        for ui_ in uis:
            extensions.populateui(ui_)

        if options[b'version']:
            return commands.version_(ui)
        if options[b'help']:
            return commands.help_(ui, cmd, command=cmd is not None)
        elif not cmd:
            return commands.help_(ui, b'shortlist')

        repo = None
        cmdpats = args[:]
        if not func.norepo:
            # use the repo from the request only if we don't have -R
            if not rpath and not cwd:
                repo = req.repo

            if repo:
                # set the descriptors of the repo ui to those of ui
                repo.ui.fin = ui.fin
                repo.ui.fout = ui.fout
                repo.ui.ferr = ui.ferr
                repo.ui.fmsg = ui.fmsg
            else:
                try:
                    repo = hg.repository(
                        ui,
                        path=path,
                        presetupfuncs=req.prereposetups,
                        intents=func.intents,
                    )
                    if not repo.local():
                        raise error.Abort(
                            _(b"repository '%s' is not local") % path
                        )
                    repo.ui.setconfig(
                        b"bundle", b"mainreporoot", repo.root, b'repo'
                    )
                except error.RequirementError:
                    raise
                except error.RepoError:
                    if rpath:  # invalid -R path
                        raise
                    if not func.optionalrepo:
                        if func.inferrepo and args and not path:
                            # try to infer -R from command args
                            repos = pycompat.maplist(cmdutil.findrepo, args)
                            guess = repos[0]
                            if guess and repos.count(guess) == len(repos):
                                req.args = [b'--repository', guess] + fullargs
                                req.earlyoptions[b'repository'] = guess
                                return _dispatch(req)
                        if not path:
                            raise error.RepoError(
                                _(
                                    b"no repository found in"
                                    b" '%s' (.hg not found)"
                                )
                                % encoding.getcwd()
                            )
                        raise
            if repo:
                ui = repo.ui
                if options[b'hidden']:
                    repo = repo.unfiltered()
            args.insert(0, repo)
        elif rpath:
            ui.warn(_(b"warning: --repository ignored\n"))

        msg = _formatargs(fullargs)
        ui.log(b"command", b'%s\n', msg)
        strcmdopt = pycompat.strkwargs(cmdoptions)
        d = lambda: util.checksignature(func)(ui, *args, **strcmdopt)
        try:
            return runcommand(
                lui, repo, cmd, fullargs, ui, options, d, cmdpats, cmdoptions
            )
        finally:
            if repo and repo != req.repo:
                repo.close()


def _runcommand(ui, options, cmd, cmdfunc):
    """Run a command function, possibly with profiling enabled."""
    try:
        with tracing.log("Running %s command" % cmd):
            return cmdfunc()
    except error.SignatureError:
        raise error.CommandError(cmd, _(b'invalid arguments'))


def _exceptionwarning(ui):
    """Produce a warning message for the current active exception"""

    # For compatibility checking, we discard the portion of the hg
    # version after the + on the assumption that if a "normal
    # user" is running a build with a + in it the packager
    # probably built from fairly close to a tag and anyone with a
    # 'make local' copy of hg (where the version number can be out
    # of date) will be clueful enough to notice the implausible
    # version number and try updating.
    ct = util.versiontuple(n=2)
    worst = None, ct, b''
    if ui.config(b'ui', b'supportcontact') is None:
        for name, mod in extensions.extensions():
            # 'testedwith' should be bytes, but not all extensions are ported
            # to py3 and we don't want UnicodeException because of that.
            testedwith = stringutil.forcebytestr(
                getattr(mod, 'testedwith', b'')
            )
            report = getattr(mod, 'buglink', _(b'the extension author.'))
            if not testedwith.strip():
                # We found an untested extension. It's likely the culprit.
                worst = name, b'unknown', report
                break

            # Never blame on extensions bundled with Mercurial.
            if extensions.ismoduleinternal(mod):
                continue

            tested = [util.versiontuple(t, 2) for t in testedwith.split()]
            if ct in tested:
                continue

            lower = [t for t in tested if t < ct]
            nearest = max(lower or tested)
            if worst[0] is None or nearest < worst[1]:
                worst = name, nearest, report
    if worst[0] is not None:
        name, testedwith, report = worst
        if not isinstance(testedwith, (bytes, str)):
            testedwith = b'.'.join(
                [stringutil.forcebytestr(c) for c in testedwith]
            )
        warning = _(
            b'** Unknown exception encountered with '
            b'possibly-broken third-party extension %s\n'
            b'** which supports versions %s of Mercurial.\n'
            b'** Please disable %s and try your action again.\n'
            b'** If that fixes the bug please report it to %s\n'
        ) % (name, testedwith, name, stringutil.forcebytestr(report))
    else:
        bugtracker = ui.config(b'ui', b'supportcontact')
        if bugtracker is None:
            bugtracker = _(b"https://mercurial-scm.org/wiki/BugTracker")
        warning = (
            _(
                b"** unknown exception encountered, "
                b"please report by visiting\n** "
            )
            + bugtracker
            + b'\n'
        )
    sysversion = pycompat.sysbytes(sys.version).replace(b'\n', b'')
    warning += (
        (_(b"** Python %s\n") % sysversion)
        + (_(b"** Mercurial Distributed SCM (version %s)\n") % util.version())
        + (
            _(b"** Extensions loaded: %s\n")
            % b", ".join([x[0] for x in extensions.extensions()])
        )
    )
    return warning


def handlecommandexception(ui):
    """Produce a warning message for broken commands

    Called when handling an exception; the exception is reraised if
    this function returns False, ignored otherwise.
    """
    warning = _exceptionwarning(ui)
    ui.log(
        b"commandexception",
        b"%s\n%s\n",
        warning,
        pycompat.sysbytes(traceback.format_exc()),
    )
    ui.warn(warning)
    return False  # re-raise the exception
